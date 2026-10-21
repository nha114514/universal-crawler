from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawler_system.browser_session import collect_session, profile_lock
from crawler_system.config import settings
from crawler_system.errors import CrawlerError
from crawler_system.network import Response
from crawler_system.web import collect_url


class BrowserSessionTests(unittest.TestCase):
    def test_direct_session_preserves_observed_http_status_and_profile(self):
        response = Response('https://example.org/article', 200, {'content-type': 'text/html'}, b'<title>Public article</title><article>Accessible research in our own Edge profile.</article>', True)
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.browser_session.validate_url'), patch('crawler_system.network.settings', return_value={**settings(), 'browser_profile': directory}), patch('crawler_system.network.Network.fetch', return_value=response) as fetch:
            item = collect_session(response.url)
        self.assertEqual(item['extractor'], 'scrapling:edge-session')
        self.assertEqual(item['http_status'], 200)
        self.assertEqual(item['metadata']['browser_session'], 'edge-owned-profile')
        self.assertTrue(fetch.call_args.kwargs['render'])

    def test_private_input_is_rejected_before_browser_starts(self):
        with patch('crawler_system.network.Network.fetch') as fetch:
            with self.assertRaises(CrawlerError) as caught:
                collect_session('http://127.0.0.1/private')
        self.assertEqual(caught.exception.code, 'PRIVATE_URL')
        fetch.assert_not_called()

    def test_profile_lock_rejects_concurrent_use_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {'browser_profile': directory}
            with profile_lock(config):
                with self.assertRaises(CrawlerError) as caught:
                    with profile_lock(config):
                        pass
                self.assertEqual(caught.exception.code, 'BROWSER_PROFILE_BUSY')
            with profile_lock(config):
                pass

    def test_auto_failure_keeps_original_error_when_profile_is_busy(self):
        with patch('crawler_system.web._collect_url', side_effect=CrawlerError('NETWORK_FAILURE', 'original')), patch('crawler_system.browser_session.collect_session', side_effect=CrawlerError('BROWSER_PROFILE_BUSY', 'busy')):
            with self.assertRaises(CrawlerError) as error:
                collect_url('https://example.org/')
        self.assertEqual(error.exception.code, 'NETWORK_FAILURE')

    def test_auto_incomplete_page_can_use_owned_profile(self):
        partial = {'url': 'https://example.org/', 'content': '', 'metadata': {}, 'warnings': [], 'retrieval_error': {'code': 'WEB_LOGIN_REQUIRED', 'message': 'login'}}
        usable = {'url': partial['url'], 'content': 'Accessible page', 'metadata': {}, 'warnings': [], 'text_status': 'full_text', 'retrieval_error': None}
        with patch('crawler_system.web._collect_url', return_value=partial), patch('crawler_system.browser_session.collect_session', return_value=usable):
            item = collect_url(partial['url'])
        self.assertEqual(item['metadata']['previous_attempt']['code'], 'WEB_LOGIN_REQUIRED')

    def test_static_mode_does_not_use_browser(self):
        with patch('crawler_system.web._collect_url', side_effect=CrawlerError('LOGIN_OR_BLOCKED', 'denied')), patch('crawler_system.browser_session.collect_session') as browser:
            with self.assertRaises(CrawlerError):
                collect_url('https://example.org/', render='never')
        browser.assert_not_called()
