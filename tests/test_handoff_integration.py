import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from crawler_system.network import Network, Response
from crawler_system.parsers import parse_html
from crawler_system.platforms import preferred_public_url
from crawler_system.web import collect_html, collect_url


class HandoffIntegrationTests(unittest.TestCase):
    def test_album_navigation_has_no_original_video_button(self):
        page = '<title>Public programmes</title><main><a href="/a_Programme.html" title="Actual programme">Actual programme</a></main>'
        item = parse_html(Response('https://www.iqiyi.com/', 200, {}, page.encode(), True))
        self.assertEqual(item['entries'][0]['kind'], 'web')
        self.assertFalse(any(asset['kind'] == 'media' for asset in item['assets']))

    def test_niconico_video_entry_is_retained(self):
        page = '<title>Public videos</title><main><a href="/watch/sm12345678" title="Actual public video">Actual public video</a></main>'
        item = parse_html(Response('https://www.nicovideo.jp/', 200, {}, page.encode(), True))
        self.assertEqual(item['entries'][0]['url'], 'https://www.nicovideo.jp/watch/sm12345678')
        self.assertEqual(item['entries'][0]['kind'], 'video')

    def test_error_page_is_not_a_readable_article(self):
        page = b'<title>403 Forbidden</title><main>403 Forbidden</main>'
        item = collect_html(Response('https://example.org/', 200, {}, page), None, 'never')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_VERIFICATION_REQUIRED')
        self.assertEqual(item['content'], '')

    def test_real_article_about_access_denied_is_not_erased(self):
        page = '<title>How to fix Access Denied and 403 Forbidden</title><article><p>' + 'This tutorial explains access denied errors in a public web server. ' * 12 + '</p></article>'
        item = collect_html(Response('https://example.org/tutorial', 200, {}, page.encode()), None, 'never')
        self.assertIsNone(item.get('retrieval_error'))
        self.assertIn('This tutorial explains', item['content'])

    def test_unverified_alias_does_not_rewrite_the_requested_site(self):
        self.assertEqual(preferred_public_url('https://cqvip.com/Article?Q=AbC'), 'https://cqvip.com/Article?Q=AbC')

    def test_main_based_error_reference_is_not_a_verification_page(self):
        page = '<title>Access Denied - error reference</title><main><p>' + 'Access Denied may occur when the web server configuration is incorrect. ' * 12 + '</p></main>'
        item = collect_html(Response('https://example.org/reference', 200, {}, page.encode()), None, 'never')
        self.assertIsNone(item.get('retrieval_error'))
        self.assertIn('web server configuration', item['content'])

    def test_unverified_homepage_replacement_is_not_used(self):
        class Static:
            def __init__(self):
                self.urls = []

            def fetch(self, url, **kwargs):
                self.urls.append(url)
                return Response(url, 200, {'content-type': 'text/html'}, b'<article><p>Real public homepage information.</p></article>')

        network = Static()
        collect_url('https://sspai.com/', network=network)
        self.assertEqual(network.urls, ['https://sspai.com/'])

    def test_cached_dns_still_checks_each_resource_url(self):
        from scrapling.fetchers import DynamicFetcher
        outcomes, handlers = [], []
        context = SimpleNamespace(route=lambda pattern, handler: handlers.append(handler),
                                  route_web_socket=lambda *args: None)
        page = SimpleNamespace(context=context, goto=lambda *args, **kwargs: None,
                               wait_for_load_state=lambda *args, **kwargs: None, close=lambda: None,
                               route=context.route, route_web_socket=context.route_web_socket)

        def fetch(url, **options):
            options['page_setup'](page)
            for target in ['https://example.org/first.js', 'https://user:secret@example.org/second.js',
                           'ftp://example.org/third.js']:
                request = SimpleNamespace(url=target, resource_type='script')
                route = SimpleNamespace(request=request, abort=lambda: outcomes.append('blocked'),
                                        continue_=lambda: outcomes.append('allowed'))
                handlers[0](route)
            return SimpleNamespace(url=url, status=200, headers={}, body=b'<article>Public content</article>',
                                   captured_xhr=[])

        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(DynamicFetcher, 'fetch', side_effect=fetch), patch('crawler_system.network.socket.getaddrinfo', return_value=addresses):
            Network()._dynamic('https://example.org/')
        self.assertEqual(outcomes, ['allowed', 'blocked', 'blocked'])


if __name__ == '__main__':
    unittest.main()
