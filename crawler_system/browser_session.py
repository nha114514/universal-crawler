"""Direct Edge sessions through installed libraries, with an owned profile."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from .config import ROOT, python_command, runtime_env, settings
from .errors import CrawlerError
from .network import Network, validate_url


def live_endpoint(config):
    """Accept only the browser currently advertising our owned profile's port."""
    import httpx
    profile = Path(config['browser_profile']).resolve()
    try:
        state = json.loads((profile / 'crawler-session.json').read_text(encoding='utf-8'))
        port, browser_path = (profile / 'DevToolsActivePort').read_text().splitlines()[:2]
        endpoint = f'http://127.0.0.1:{int(port)}'
        if not 0 < int(port) < 65536 or state['endpoint'] != endpoint or state['browser_path'] != browser_path:
            return None
        version = httpx.get(endpoint + '/json/version', timeout=1, trust_env=False).json()
        target = urlsplit(version['webSocketDebuggerUrl'])
        if target.hostname == '127.0.0.1' and target.port == int(port) and target.path == browser_path:
            return endpoint
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        pass
    return None


@contextmanager
def profile_lock(config):
    profile = Path(config['browser_profile']).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / 'crawler.lock').open('a+b') as lock:
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CrawlerError('BROWSER_PROFILE_BUSY', 'Dedicated Edge session is in use', 'Wait for the current crawl task to finish and retry; system-opened login window can remain open')
        try:
            yield profile
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def session_response(url):
    validate_url(url)
    network = Network()
    network.config.update(use_browser_profile=True, browser_timeout_ms=15000, browser_retries=1)
    endpoint = live_endpoint(network.config)
    if endpoint:
        network.config['live_browser_endpoint'] = endpoint
        response = network.fetch(url, render=True)
    else:
        with profile_lock(network.config):
            response = network.fetch(url, render=True)
    response.render_diagnostics['browser_session'] = 'edge-live-profile' if endpoint else 'edge-owned-profile'
    return response


def collect_session(url):
    from .web import collect_html
    response = session_response(url)
    item = collect_html(response, None, 'never')
    item['extractor'] = 'scrapling:edge-session'
    item['metadata'].update(response.render_diagnostics)
    return item


def iqiyi_playback_payload(url, config):
    """Read the normal page response in our profile; never export cookies."""
    from playwright.sync_api import sync_playwright
    from .iqiyi import _identity
    _identity(url)
    validate_url(url)
    validated = set()

    def guard(route):
        target = route.request.url
        try:
            validate_url(target, resolve=False)
            parsed = urlsplit(target)
            endpoint = (parsed.hostname, parsed.port)
            if endpoint not in validated:
                validate_url(target)
                validated.add(endpoint)
            if route.request.resource_type in {'image', 'font', 'media'} or parsed.path.lower().endswith(('.ts', '.m4s', '.mp4', '.f4v', '.aac')):
                route.abort()
            else:
                route.continue_()
        except CrawlerError:
            route.abort()

    endpoint = live_endpoint(config)
    with ExitStack() as stack:
        if not endpoint:
            profile = stack.enter_context(profile_lock(config))
        playwright = stack.enter_context(sync_playwright())
        if endpoint:
            context = playwright.chromium.connect_over_cdp(endpoint).contexts[0]
        else:
            context = playwright.chromium.launch_persistent_context(
                str(profile), executable_path=config['browser_executable'], headless=True,
                chromium_sandbox=True, accept_downloads=False, service_workers='block',
                args=['--disable-extensions', '--no-first-run', '--no-default-browser-check'])
        page = context.new_page() if endpoint else context.pages[0] if context.pages else context.new_page()
        try:
            page.route('**/*', guard)
            page.route_web_socket('**/*', lambda route: route.close())
            page.goto(url, wait_until='commit', timeout=20000)
            page.wait_for_function('() => !!window.QiyiPlayerProphetData?.ev', timeout=12000, polling=100)
            return page.evaluate('''() => {
                const value = window.QiyiPlayerProphetData;
                return {tvId:value.tvId, videoInfo:{tvId:value.videoInfo?.tvId, title:value.videoInfo?.title},
                        ev:value.ev, _browser_user_agent:navigator.userAgent};
            }''')
        except Exception:
            raise CrawlerError('IQIYI_SESSION_UNAVAILABLE', 'Dedicated Edge did not obtain valid playback data for this video',
                               'Open dedicated Edge in settings, check normal playback status, keep window open and retry download') from None
        finally:
            page.close() if endpoint else context.close()


def open_login(url):
    validate_url(url)
    config = settings()
    endpoint = live_endpoint(config)
    if endpoint:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            page = browser.contexts[0].new_page()
            page.goto(url, wait_until='commit', timeout=30000)
        return {'started': True, 'reused': True, 'message': 'Reused dedicated Edge; keep window open after logging in to crawl directly'}
    # Check before spawning: never interrupt a scan or an existing login window.
    with profile_lock(config):
        pass
    process = subprocess.Popen([python_command(), '-m', 'crawler_system.browser_session', url], cwd=ROOT,
                               env=runtime_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'started': True, 'pid': process.pid, 'message': 'Keep window open after logging in via dedicated Edge; crawling will directly reuse current session'}


def login_window(url):
    from playwright.sync_api import sync_playwright
    validate_url(url)
    config = settings()
    with profile_lock(config) as profile, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(profile), executable_path=config['browser_executable'],
                       headless=False, chromium_sandbox=True, args=['--disable-extensions', '--no-first-run', '--no-default-browser-check',
                           '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'], accept_downloads=False)
        state_path = profile / 'crawler-session.json'
        try:
            from .storage import atomic_text
            port, browser_path = (profile / 'DevToolsActivePort').read_text().splitlines()[:2]
            atomic_text(state_path, json.dumps({'endpoint': f'http://127.0.0.1:{port}', 'browser_path': browser_path}))
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            while context.pages:
                try:
                    context.pages[0].wait_for_timeout(500)
                except Exception:
                    break
        finally:
            try:
                context.close()
            finally:
                state_path.unlink(missing_ok=True)


if __name__ == '__main__':
    import sys
    login_window(sys.argv[1])
