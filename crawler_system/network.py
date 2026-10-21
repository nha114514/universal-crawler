from dataclasses import dataclass, field
import asyncio
import hashlib
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit, urljoin, unquote
import uuid

from .config import settings
from .errors import CrawlerError

FAKE_IP_V4 = ipaddress.ip_network("198.18.0.0/15")
FAKE_IP_V6 = ipaddress.ip_network("fc00::/7")


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes
    rendered: bool = False
    render_diagnostics: dict = field(default_factory=dict)
    media_entries: list = field(default_factory=list)


def validate_url(url, resolve=True):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host, port = parsed.hostname.lower(), parsed.port
    except (ValueError, TypeError):
        raise CrawlerError("INVALID_URL", "Only HTTP(S) URLs without credentials are supported", "Check the input URL")
    if any(ord(c) < 32 for c in url) or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise CrawlerError("PRIVATE_URL", "Crawling localhost or private network addresses is not allowed", "Use a public URL")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
        if resolve:
            try:
                addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
            except socket.gaierror:
                raise CrawlerError("DNS_FAILURE", f"Failed to resolve domain {host}", "Check network or proxy and retry")
            if any(address.version == 4 and address in FAKE_IP_V4 for address in addresses):
                # Fake-IP DNS (Clash/Mihomo/sing-box TUN) answers every domain from
                # 198.18.0.0/15 plus a ULA twin; the proxy then routes by hostname.
                # Only drop those placeholders so real private answers still fail.
                addresses = [address for address in addresses
                             if not (address in FAKE_IP_V4 if address.version == 4 else address in FAKE_IP_V6)]
    if any(not address.is_global for address in addresses):
        raise CrawlerError("PRIVATE_URL", "Target resolves to localhost, private, or reserved address", "Use a public URL")
    return url


def safe_filename(name):
    name = unquote(str(name)).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069\u200e\u200f]', "_", name).strip(" .")[:150]
    if not name:
        name = "download.bin"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        name = "_" + name
    return name


class Network:
    def __init__(self, max_bytes=None):
        self.config = settings()
        self.max_bytes = max_bytes if max_bytes is not None else self.config["max_parse_bytes"]
        self.last_request = {}

    def _pace(self, url):
        host = urlsplit(url).hostname
        interval = max(self.config["domain_interval"], 4 if host == "export.arxiv.org" else 0)
        delay = interval - (time.monotonic() - self.last_request.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()

    def _static(self, url, method="GET", data=None, headers=None, stealth=True):
        if method == "HEAD":
            import httpx
            with httpx.Client(timeout=self.config["request_timeout"], follow_redirects=False) as client:
                response = client.head(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
                return Response(str(response.url), response.status_code, dict(response.headers), b"")
        from scrapling.fetchers import Fetcher
        chunks, size, exceeded = [], 0, False

        def receive(chunk):
            nonlocal size, exceeded
            size += len(chunk)
            if size > self.max_bytes:
                exceeded = True
                return 0xFFFFFFFF  # CURL_WRITEFUNC_ERROR: stop receiving immediately.
            chunks.append(chunk)
            return len(chunk)

        try:
            operation = getattr(Fetcher, method.lower(), None)
            if operation is None:
                raise CrawlerError("UNSUPPORTED_METHOD", "Unsupported HTTP method")
            options = {"timeout": self.config["request_timeout"], "retries": 0,
                       "follow_redirects": False, "content_callback": receive, "stealthy_headers": stealth}
            if data is not None:
                options["data"] = data
            if headers:
                options["headers"] = headers
            page = operation(url, **options)
        except Exception:
            if exceeded:
                raise CrawlerError("SIZE_LIMIT", "Content exceeds parsing size limit", "Download the original file on-demand or adjust settings")
            raise
        return Response(str(page.url), page.status, {str(k).lower(): str(v) for k, v in page.headers.items()}, b"".join(chunks) or page.body)

    def _dynamic(self, url):
        from scrapling.fetchers import DynamicFetcher
        from playwright.sync_api import TimeoutError as BrowserTimeout
        from .parsers import listing_platform, video_content_link
        if (urlsplit(url).hostname or "").removeprefix("www.") == "douyin.com" and not self.config.get('live_browser_endpoint'):
            return asyncio.run(self._dynamic_douyin(url))
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = self.config["browser_cache"]
        validated = set()
        blocked = []
        diagnostics = {}
        media_sources = {}
        iqiyi_detail = video_content_link(url, 'iqiyi') and not urlsplit(url).path.startswith('/a_')

        def setup(page):
            # Defer/advert scripts can hold DOMContentLoaded after resources exist.
            # Wait for the requested content below instead of unrelated page events.
            original_goto, original_wait = page.goto, page.wait_for_load_state
            def goto(*args, **kwargs):
                kwargs['wait_until'] = 'commit'
                return original_goto(*args, **kwargs)
            def wait_for_load_state(state=None, **kwargs):
                if state in {None, 'load', 'domcontentloaded'}:
                    return None
                return original_wait(state, **kwargs)
            page.goto, page.wait_for_load_state = goto, wait_for_load_state
            def guard(route):
                target = route.request.url
                if route.request.resource_type in {'image', 'font'}:
                    diagnostics['skipped_resources'] = diagnostics.get('skipped_resources', 0) + 1
                    route.abort()
                    return
                try:
                    # DNS can be cached for this page, but scheme, credentials,
                    # ports and private literals must be checked on every URL.
                    validate_url(target, resolve=False)
                    parsed_target = urlsplit(target)
                    endpoint = (parsed_target.hostname, parsed_target.port)
                    if endpoint not in validated:
                        validate_url(target)
                        validated.add(endpoint)
                    extension = parsed_target.path.lower().rsplit('.', 1)[-1]
                    if route.request.resource_type == 'media' or extension in {'mp4', 'webm', 'mov', 'mp3', 'm4a', 'wav', 'ogg', 'aac', 'm4s', 'f4v'}:
                        if extension not in {'m4s', 'f4v'}:
                            media_sources[target] = 'audio' if extension in {'mp3', 'm4a', 'wav', 'ogg', 'aac'} else 'video'
                        diagnostics['skipped_media_downloads'] = diagnostics.get('skipped_media_downloads', 0) + 1
                        route.abort()
                        return
                    route.continue_()
                except CrawlerError as error:
                    blocked.append(error.as_dict())
                    route.abort()
            try:
                page.route("**/*", guard)
                page.route_web_socket("**/*", lambda route: route.close())
            except Exception:
                page.close()  # page_setup errors are swallowed by Scrapling; fail closed.
                raise

        def wait_for_content(page):
            # DOMContentLoaded does not mean the asynchronous feed is ready.
            # A bounded content condition also survives client-side navigation.
            condition = r"""platform => {
                const root = document.querySelector('article, main') || document.body;
                if (!root) return false;
                if (platform === 'iqiyi-detail') return !!document.querySelector('[class*="meta_titleTextBlock__"]')?.textContent.trim()
                    && !!document.querySelector('meta[name="description"]')?.content && !document.querySelector('meta[name="description"]').content.startsWith('爱奇艺-在线视频网站');
                if (platform === 'bilibili') return Array.from(document.querySelectorAll('a[href*="/video/"],a[href*="/bangumi/play/"]')).some(a => (a.getAttribute('title') || a.textContent).trim().length >= 4);
                if (platform === 'iqiyi') return Array.from(document.querySelectorAll('a[href]')).some(a => {
                    const u = new URL(a.href, location.href);
                    return /\/(?:v_|w_|a_)[^/]+\.html/.test(u.pathname) || (u.pathname === '/tvg/to_page_url' && (u.searchParams.has('album_id') || u.searchParams.has('tv_id')));
                });
                if (platform && platform !== 'douyin') return Array.from(document.querySelectorAll('a[href]')).some(a => {
                    const path = new URL(a.href, location.href).pathname;
                    const video = /\/(?:video|videos|shorts|live|short-video|v_show)\/|^\/v\/ac\d+|^\/x\/(?:cover|page)\/|^\/(?:v_|w_|a_)[^/]+\.html$|^\/b\/[^/]+\/[^/]+\.html$|^\/watch(?:\/sm|\/so|\/|$)|^\/@[^/]+\/video\/\d+/.test(path)
                        || path === '/watch' && new URL(a.href, location.href).searchParams.has('v')
                        || ['vimeo','xigua'].includes(platform) && /^\/\d{6,}/.test(path)
                        || platform === 'haokan' && path === '/v' && new URL(a.href, location.href).searchParams.has('vid')
                        || platform === 'pornhub' && path === '/view_video.php' && new URL(a.href, location.href).searchParams.has('viewkey');
                    return video && (a.getAttribute('title') || a.getAttribute('aria-label') || a.textContent).trim().length >= 4;
                });
                const copy = root.cloneNode(true);
                copy.querySelectorAll('script,style,nav,footer,header,aside,button,[hidden],[aria-hidden="true"]').forEach(e => e.remove());
                const text = copy.textContent.replace(/\s+/g,' ').trim();
                if (platform === 'douyin') return Array.from(document.querySelectorAll('[data-aweme-id],a[href*="/video/"]')).some(a => a.textContent.trim().length >= 4);
                const players = Array.from(document.querySelectorAll('video,audio'));
                const readyMedia = players.some(p => /^https?:/.test(p.currentSrc || p.src || p.querySelector('source')?.src || ''));
                if (players.length) return readyMedia || performance.getEntriesByType('resource').some(p => /\.(?:m3u8|mpd)(?:[?#]|$)/i.test(p.name));
                return text.length >= 32 && text !== document.title && !/^(loading|正在加载|加载中)[. …]*$/i.test(text);
            }"""
            try:
                page.wait_for_function(condition, arg='iqiyi-detail' if video_content_link(url, 'iqiyi') and not urlsplit(url).path.startswith('/a_') else listing_platform(url), timeout=12000, polling=250)
                diagnostics["content_wait"] = "ready"
            except BrowserTimeout:
                diagnostics["content_wait"] = "timeout"
            except Exception:
                diagnostics["content_wait"] = "failed"
            try:
                for entry in page.evaluate(r"""() => [...Array.from(document.querySelectorAll('video,audio')).map(p => ({url:p.currentSrc || p.src || p.querySelector('source')?.src || '',kind:p.tagName.toLowerCase()})), ...performance.getEntriesByType('resource').filter(p => /\.(?:m3u8|mpd)(?:[?#]|$)/i.test(p.name)).map(p => ({url:p.name,kind:'video'}))].slice(0,50)"""):
                    if entry['url'].startswith(('http://', 'https://')):
                        validate_url(entry['url'])
                        media_sources[entry['url']] = entry['kind']
                diagnostics['media_sources'] = len(media_sources)
            except Exception:
                diagnostics['media_scan'] = 'incomplete'

        options = dict(headless=True, timeout=self.config.get('browser_timeout_ms', 45000), wait=200, retries=max(1, self.config.get('browser_retries', 1)),
                       load_dom=True, network_idle=False, page_setup=setup, page_action=wait_for_content,
                       additional_args={"service_workers": "block", "accept_downloads": False})
        if listing_platform(url) == 'tencent-video':
            options['capture_xhr'] = r'/trpc\.vector_layout\.page_view\.PageService/getPage'
        if self.config.get('use_browser_profile'):
            options['user_data_dir'] = self.config['browser_profile']
        browser = Path(self.config['browser_executable'])
        if os.name == 'nt':
            if not browser.is_file():
                raise CrawlerError('BROWSER_NOT_INSTALLED', 'Crawler browser does not exist', 'Check browser_executable setting')
            options['executable_path'] = str(browser)
            diagnostics['browser'] = 'edge-temporary-profile' if browser.name.lower() == 'msedge.exe' else 'chrome-temporary-profile'
            if self.config.get('use_browser_profile'):
                diagnostics['browser'] = 'edge-owned-profile'
        if endpoint := self.config.get('live_browser_endpoint'):
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                # A new context would discard the user's current login.
                tab = browser.contexts[0].new_page()
                try:
                    setup(tab)
                    raw = tab.goto(url, wait_until='commit', timeout=options['timeout'])
                    wait_for_content(tab)
                    page = Response(tab.url, raw.status if raw else 200, raw.all_headers() if raw else {}, tab.content().encode('utf-8'))
                finally:
                    tab.close()  # Disconnect only; the user's login browser stays open.
        else:
            page = DynamicFetcher.fetch(url, **options)
        validate_url(str(page.url))
        if page.status >= 400 and blocked:
            raise CrawlerError(**blocked[0])
        diagnostics["blocked_resources"] = len(blocked)
        entries = [{'url': str(page.url), 'title': '', 'authors': [], 'kind': kind, 'media_url': target, 'source': 'observed-player-resource'}
                   for target, kind in list(media_sources.items())[:50]]
        if listing_platform(url) == 'tencent-video':
            from .platform_lists import tencent_entries
            captured_bytes = 0
            for xhr in getattr(page, 'captured_xhr', [])[:12]:
                captured_bytes += len(xhr.body)
                if captured_bytes > self.max_bytes:
                    break
                if xhr.status != 200 or urlsplit(xhr.url).hostname != 'pbaccess.video.qq.com':
                    continue
                try:
                    entries.extend(tencent_entries(json.loads(xhr.body)))
                except (ValueError, TypeError, AttributeError):
                    continue
            diagnostics['public_content_cards'] = sum(entry.get('source') == 'tencent-public-page-response' for entry in entries)
        return Response(str(page.url), page.status, {str(k).lower(): str(v) for k, v in page.headers.items()}, page.body, True, diagnostics, entries)

    async def _dynamic_douyin(self, url):
        from scrapling.fetchers import DynamicFetcher
        from playwright.async_api import TimeoutError as BrowserTimeout
        from .parsers import douyin_feed_entries
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = self.config["browser_cache"]
        validated, blocked = set(), []
        diagnostics = {"skipped_resources": 0}
        gate = asyncio.Semaphore(8)
        dns_locks = {}

        async def setup(page):
            original_goto, original_wait = page.goto, page.wait_for_load_state
            async def goto(*args, **kwargs):
                kwargs['wait_until'] = 'commit'
                return await original_goto(*args, **kwargs)
            async def wait_for_load_state(state=None, **kwargs):
                if state in {None, 'load', 'domcontentloaded'}:
                    return None
                return await original_wait(state, **kwargs)
            page.goto, page.wait_for_load_state = goto, wait_for_load_state
            async def guard(route):
                if route.request.resource_type in {"image", "media", "font"} or urlsplit(route.request.url).path.lower().endswith(('.mp4', '.webm', '.mov', '.mp3', '.m4a', '.aac', '.m4s', '.f4v')):
                    diagnostics["skipped_resources"] += 1
                    await route.abort()
                    return
                target = route.request.url
                try:
                    validate_url(target, resolve=False)
                    parsed = urlsplit(target)
                    endpoint = (parsed.hostname, parsed.port)
                    if endpoint not in validated:
                        async with gate:
                            async with dns_locks.setdefault(endpoint, asyncio.Lock()):
                                if endpoint not in validated:
                                    await asyncio.to_thread(validate_url, target)
                                    validated.add(endpoint)
                    await route.continue_()
                except CrawlerError as error:
                    blocked.append(error.as_dict())
                    await route.abort()

            async def close_socket(route):
                await route.close()

            try:
                await page.context.route("**/*", guard)
                await page.context.route_web_socket("**/*", close_socket)
            except Exception:
                await page.close()
                raise

        async def wait_for_content(page):
            try:
                await page.wait_for_function("""() => document.querySelector('video') || Array.from(document.querySelectorAll('[data-aweme-id],a[href*="/video/"]')).some(card => card.textContent.trim().length >= 4)""", timeout=12000, polling=250)
                diagnostics["content_wait"] = "ready"
            except BrowserTimeout:
                diagnostics["content_wait"] = "timeout"
            except Exception:
                diagnostics["content_wait"] = "failed"

        options = {"headless": True, "timeout": self.config.get('browser_timeout_ms', 120000), "wait": 200, "retries": max(1, self.config.get('browser_retries', 1)),
                   "load_dom": True, "network_idle": False, "page_setup": setup, "page_action": wait_for_content,
                   "capture_xhr": r"/aweme/(?:v2/web/module/feed/|v1/web/aweme/detail/)",
                   "additional_args": {"service_workers": "block", "accept_downloads": False}}
        if self.config.get('use_browser_profile'):
            options['user_data_dir'] = self.config['browser_profile']
        browser = Path(self.config['browser_executable'])
        if os.name == 'nt':
            if not browser.is_file():
                raise CrawlerError('BROWSER_NOT_INSTALLED', 'Crawler browser does not exist', 'Check browser_executable setting')
            options['executable_path'] = str(browser)
            diagnostics['browser'] = 'edge-temporary-profile' if browser.name.lower() == 'msedge.exe' else 'chrome-temporary-profile'
            if self.config.get('use_browser_profile'):
                diagnostics['browser'] = 'edge-owned-profile'
        page = await asyncio.wait_for(DynamicFetcher.async_fetch(url, **options), timeout=options['timeout'] / 1000 + 15)
        await asyncio.to_thread(validate_url, str(page.url))
        if page.status >= 400 and blocked:
            raise CrawlerError(**blocked[0])
        diagnostics["blocked_resources"] = len(blocked)
        media_entries = {}
        captured_bytes = 0
        for xhr in page.captured_xhr[:8]:
            captured_bytes += len(xhr.body)
            if captured_bytes > self.max_bytes:
                break
            if xhr.status != 200 or (urlsplit(xhr.url).hostname or "").removeprefix("www.") != "douyin.com":
                continue
            try:
                payload = json.loads(xhr.body)
                for entry in douyin_feed_entries(payload):
                    media_entries[entry["url"]] = entry
            except (ValueError, TypeError, AttributeError):
                continue
        diagnostics["public_media_addresses"] = len(media_entries)
        return Response(str(page.url), page.status, {str(k).lower(): str(v) for k, v in page.headers.items()}, page.body, True, diagnostics, list(media_entries.values()))

    def fetch(self, url, render=False, method="GET", data=None, headers=None, allow_errors=False, stealth=True):
        for redirect in range(6):
            validate_url(url)
            self._pace(url)
            try:
                response = self._dynamic(url) if render else self._static(url, method, data, headers, stealth)
            except CrawlerError:
                raise
            except Exception as error:
                raise CrawlerError("NETWORK_FAILURE", f"Crawl failed: {type(error).__name__}: {str(error)[:350]}", "Check network, site login, or browser environment")
            validate_url(response.url)
            if len(response.body) > self.max_bytes:
                raise CrawlerError("SIZE_LIMIT", "Content exceeds parsing size limit", "Download the original file on-demand or adjust settings")
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                target = urljoin(url, response.headers["location"])
                if urlsplit(target).hostname != urlsplit(url).hostname and headers:
                    headers = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "cookie"}}
                if response.status == 303 or (response.status in {301, 302} and method == "POST"):
                    method, data = "GET", None
                url = target
                continue
            if response.status in {429, 503} and redirect == 0:
                delay = response.headers.get("retry-after", "2")
                try:
                    seconds = int(delay)
                except ValueError:
                    seconds = 2
                if seconds <= 10:
                    time.sleep(max(1, seconds))
                    continue
            if not allow_errors and not 200 <= response.status < 300:
                code = "LOGIN_OR_BLOCKED" if response.status in {401, 403, 429} else "HTTP_ERROR"
                raise CrawlerError(code, f"Website returned HTTP {response.status}", "Complete site login/verification, reduce rate, or retry later")
            return response
        raise CrawlerError("REDIRECT_LIMIT", "Too many redirects", "Check original URL")

    def download(self, url, directory, max_mb=0, headers=None, expected_media=None, filename=None):
        import httpx
        validate_url(url)
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        limit = max_mb * 1024 * 1024 if max_mb else None
        part = directory / (uuid.uuid4().hex + ".part")
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                for _ in range(6):
                    validate_url(url)
                    self._pace(url)
                    with client.stream("GET", url, headers=headers or {"User-Agent": "Mozilla/5.0"}) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if not 200 <= response.status_code < 300:
                            raise CrawlerError("HTTP_ERROR", f"Download returned HTTP {response.status_code}", "Re-crawl to refresh file URL, or check site permissions")
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if expected_media and (content_type.startswith(("image/", "text/")) or "json" in content_type):
                            raise CrawlerError("MEDIA_TYPE_MISMATCH", "Target returned image or error page, no video file obtained", "Re-crawl video URL and retry")
                        if limit is not None and int(response.headers.get("content-length", "0")) > limit:
                            raise CrawlerError("SIZE_LIMIT", f"File exceeds {max_mb} MB", "Increase download limit and retry")
                        digest, size, signature = hashlib.sha256(), 0, b""
                        with part.open("wb") as file:
                            for chunk in response.iter_bytes(65536):
                                size += len(chunk)
                                if limit is not None and size > limit:
                                    raise CrawlerError("SIZE_LIMIT", f"File exceeds {max_mb} MB", "Increase download limit and retry")
                                digest.update(chunk)
                                file.write(chunk)
                                if len(signature) < 512:
                                    signature = (signature + chunk)[:512]
                        extension = ""
                        if expected_media:
                            if expected_media == 'pdf':
                                if not signature.startswith(b'%PDF-'):
                                    raise CrawlerError('MEDIA_TYPE_MISMATCH', 'Website did not return a valid PDF; attachment not saved', 'Check paper source, login, or verification status and retry')
                                extension = '.pdf'
                            elif expected_media == 'audio' and signature.startswith(b'RIFF') and signature[8:12] == b'WAVE':
                                extension = '.wav'
                            elif expected_media == 'audio' and signature.startswith(b'OggS'):
                                extension = '.ogg'
                            elif expected_media == 'audio' and (signature.startswith(b'ID3') or len(signature) > 1 and signature[0] == 0xFF and signature[1] & 0xE0 == 0xE0):
                                extension = '.aac' if content_type == 'audio/aac' else '.mp3'
                            elif signature[4:8] == b"ftyp":
                                extension = ".m4a" if expected_media == "audio" else ".mp4"
                            elif signature.startswith(b"\x1aE\xdf\xa3"):
                                extension = ".webm"
                            elif signature.startswith(b"FLV"):
                                extension = ".flv"
                            elif len(signature) > 188 and signature[0] == signature[188] == 0x47:
                                extension = ".ts"
                            else:
                                raise CrawlerError("MEDIA_TYPE_MISMATCH", "File content is not recognized video/audio; not saved as video", "Check video source and retry")
                        name = safe_filename(filename or urlsplit(url).path.rsplit("/", 1)[-1])
                        if not extension and not Path(name).suffix:
                            extension = mimetypes.guess_extension(content_type) or Path(unquote(urlsplit(url).path)).suffix
                        if extension:
                            name = str(Path(name).with_suffix(extension))
                        target = directory / (uuid.uuid4().hex[:10] + "-" + name)
                        if not target.is_relative_to(directory):
                            raise CrawlerError("INVALID_PATH", "Invalid file path")
                        part.replace(target)
                        return {"url": url, "filename": name, "path": str(target), "bytes": size, "sha256": digest.hexdigest(), "content_type": content_type}
                raise CrawlerError("REDIRECT_LIMIT", "Too many download redirects")
        finally:
            part.unlink(missing_ok=True)

