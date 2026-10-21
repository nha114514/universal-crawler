import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from crawler_system.browser_session import iqiyi_playback_payload, profile_lock, session_response
from crawler_system.config import settings
from crawler_system.errors import CrawlerError
from crawler_system.network import Response, validate_url
from crawler_system.resources import collect_resources


class LiveBrowserSessionTests(unittest.TestCase):
    def test_netflix_auto_uses_browser_instead_of_anonymous_preview(self):
        url = 'https://www.netflix.com/watch/80077368'
        response = Response(url, 200, {'content-type': 'text/html'},
                            b'<title>Selected video</title><main>Normal signed-in browser resource</main>', True)
        with patch('crawler_system.resources.validate_url'), \
                patch('crawler_system.browser_session.session_response', return_value=response) as session, \
                patch('crawler_system.network.Network.fetch', side_effect=AssertionError('anonymous request')):
            item = collect_resources(url)['items'][0]
        session.assert_called_once_with(url)
        self.assertEqual(item['extractor'], 'scrapling:edge-session')

    def test_open_login_window_is_reused_without_losing_session_or_closing_it(self):
        from playwright.sync_api import sync_playwright

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                accepted = self.path == '/login' or 'fixture_session=yes' in self.headers.get('Cookie', '')
                self.send_response(200 if accepted else 401)
                self.send_header('Content-Type', 'text/html')
                if self.path == '/login':
                    self.send_header('Set-Cookie', 'fixture_session=yes; Path=/; HttpOnly; SameSite=Lax')
                self.end_headers()
                body = b'<main>Accepted existing browser login session and private research resources</main>' if accepted else b'<main>Please sign in</main>'
                if accepted and self.path == '/iqiyi':
                    body += b'<script>window.QiyiPlayerProphetData={tvId:"fixture",videoInfo:{tvId:"fixture",title:"Fixture"},ev:"fixture"}</script>'
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        url = f'http://127.0.0.1:{server.server_port}'
        ready, stop, outcome = threading.Event(), threading.Event(), {}
        with tempfile.TemporaryDirectory() as directory:
            config = {**settings(), 'browser_profile': directory, 'domain_interval': 0}

            def login_browser():
                try:
                    with profile_lock(config), sync_playwright() as pw:
                        context = pw.chromium.launch_persistent_context(directory, executable_path=config['browser_executable'],
                            headless=True, args=['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'])
                        try:
                            port, browser_path = Path(directory, 'DevToolsActivePort').read_text().splitlines()[:2]
                            state = {'endpoint': 'http://127.0.0.1:' + port, 'browser_path': browser_path}
                            Path(directory, 'crawler-session.json').write_text(json.dumps(state), encoding='utf-8')
                            page = context.pages[0]
                            page.goto(url + '/login')
                            page.evaluate('window.fixtureLoginMarker = 42')
                            ready.set()
                            stop.wait(30)
                            outcome['original_tab_intact'] = page.evaluate('window.fixtureLoginMarker') == 42
                            outcome['only_original_tab'] = len(context.pages) == 1
                        finally:
                            context.close()
                except Exception as error:
                    outcome['error'] = str(error)
                    ready.set()

            owner = threading.Thread(target=login_browser, daemon=True)
            owner.start()
            try:
                self.assertTrue(ready.wait(20), 'Fixture browser did not start')
                self.assertNotIn('error', outcome)

                def fixture_validation(target, resolve=True):
                    return target if target.startswith(url + '/') else validate_url(target, resolve)

                with patch('crawler_system.network.settings', return_value=config), \
                        patch('crawler_system.network.validate_url', side_effect=fixture_validation), \
                        patch('crawler_system.browser_session.validate_url', side_effect=fixture_validation):
                    failure = None
                    try:
                        response = session_response(url + '/private')
                    except CrawlerError as error:
                        failure = error.code
                self.assertIsNone(failure, 'Scanning the open login browser must not require closing it')
                self.assertEqual(response.status, 200)
                self.assertIn(b'Accepted existing browser login', response.body)
                self.assertEqual(response.render_diagnostics['browser_session'], 'edge-live-profile')
                failure = None
                with patch('crawler_system.iqiyi._identity'), patch('crawler_system.browser_session.validate_url', side_effect=fixture_validation):
                    try:
                        payload = iqiyi_playback_payload(url + '/iqiyi', config)
                    except CrawlerError as error:
                        failure = error.code
                self.assertIsNone(failure, 'Existing iQIYI download checks must also reuse the open login window')
                self.assertEqual(payload['tvId'], 'fixture')
            finally:
                stop.set()
                owner.join(10)
                server.shutdown()
                server.server_close()
            self.assertNotIn('error', outcome)
            self.assertTrue(outcome['original_tab_intact'])
            self.assertTrue(outcome['only_original_tab'])

    def test_live_session_metadata_cannot_connect_to_an_external_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {**settings(), 'browser_profile': directory}
            Path(directory, 'DevToolsActivePort').write_text('9222\n/devtools/browser/fixture', encoding='utf-8')
            Path(directory, 'crawler-session.json').write_text(json.dumps({
                'endpoint': 'http://example.org:9222', 'browser_path': '/devtools/browser/fixture'}), encoding='utf-8')
            with profile_lock(config), patch('crawler_system.network.settings', return_value=config), \
                    patch('crawler_system.browser_session.validate_url'), patch('httpx.get') as request:
                with self.assertRaises(CrawlerError) as caught:
                    session_response('https://example.org/')
                self.assertEqual(caught.exception.code, 'BROWSER_PROFILE_BUSY')
                request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
