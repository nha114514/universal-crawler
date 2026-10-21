import unittest
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.network import Response
from crawler_system.parsers import parse_html
from crawler_system.router import collect
from crawler_system.web import collect_url


class StubNetwork:
    def __init__(self, static, rendered=None):
        self.static = static
        self.rendered = rendered or static
        self.calls = []

    def fetch(self, url, render=False, **kwargs):
        self.calls.append((url, render))
        return Response(url, 200, {'content-type': 'text/html'},
                        (self.rendered if render else self.static).encode(), render)


class UniversalURLTests(unittest.TestCase):
    def test_auto_video_login_error_still_collects_public_page(self):
        page = {'url': 'https://vimeo.com/1234', 'title': 'Public description',
                'kind': 'web', 'content': 'A public article', 'text_status': 'full_text',
                'metadata': {}, 'assets': [], 'warnings': []}
        error = CrawlerError('VIDEO_LOGIN_REQUIRED', 'Video needs login')
        with patch('crawler_system.router.validate_url'), patch('crawler_system.router.known_video', return_value=True), patch('crawler_system.video.collect_video', side_effect=error), patch('crawler_system.router.collect_url', return_value=page) as web:
            item = collect(page['url'], network=object())
        web.assert_called_once()
        self.assertEqual(item['content'], 'A public article')
        self.assertEqual(item['metadata']['adapter_errors'][0]['code'], error.code)
        self.assertEqual(item['retrieval_error']['code'], error.code)

    def test_explicit_video_error_is_preserved(self):
        with patch('crawler_system.router.validate_url'), patch('crawler_system.video.collect_video', side_effect=CrawlerError('VIDEO_LOGIN_REQUIRED', 'Login')), patch('crawler_system.router.collect_url') as web:
            with self.assertRaises(CrawlerError):
                collect('https://vimeo.com/1234', mode='video', network=object())
        web.assert_not_called()

    def test_public_post_uses_local_page_collector_without_bridge(self):
        network = StubNetwork('<title>A public post</title><article><p>' + 'Public content. ' * 40 + '</p></article>')
        with patch('crawler_system.router.validate_url'), patch('crawler_system.router.known_video', return_value=False):
            item = collect('https://x.com/example/status/123', network=network)
        self.assertIn('Public content.', item['content'])
        self.assertNotIn('adapter_errors', item['metadata'])

    def test_x_profile_does_not_use_status_only_adapter(self):
        network = StubNetwork('<title>Public profile</title><main><p>' + 'Public profile data and research. ' * 30 + '</p></main>')
        with patch('crawler_system.router.validate_url'), patch('crawler_system.router.known_video', return_value=False):
            item = collect('https://x.com/NASA', network=network)
        self.assertIn('Public profile data', item['content'])

    def test_short_script_shell_remains_partial_instead_of_full_text(self):
        page = '<title>Archive</title><main>Loading the public archive.</main><script src="app.js"></script>'
        item = collect_url('https://archive.org/', network=StubNetwork(page), render='always')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_CONTENT_INCOMPLETE')
        self.assertEqual(item['text_status'], 'partial')

    def test_login_form_is_not_readable_body(self):
        page = '<title>Log in to Example</title><main><h1>Log in</h1><form><input type="password"><button>Log in</button></form><p>Please sign in to continue.</p></main>'
        item = collect_url('https://example.org/login', network=StubNetwork(page), render='never')
        self.assertEqual(item['content'], '')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_LOGIN_REQUIRED')

    def test_social_signup_wall_is_reported_as_login(self):
        page = '<title>NASA (@nasa) · Instagram</title><article><p>查看nasa的照片、视频和更多内容</p><p>注册就不会再错过nasa的帖子了。</p><button>注册</button><button>登录</button></article>'
        item = collect_url('https://www.instagram.com/nasa/', network=StubNetwork(page), render='never')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_LOGIN_REQUIRED')
        self.assertEqual(item['content'], '')

    def test_network_security_wall_is_not_full_text(self):
        page = '<title>Python</title><main><p>You have been blocked by network security. Log in to continue.</p></main>'
        item = collect_url('https://www.reddit.com/r/Python/', network=StubNetwork(page), render='never')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_VERIFICATION_REQUIRED')

    def test_media_mime_without_extension_is_not_plain_attachment(self):
        class Direct:
            config = {'max_text_chars': 1000}
            def fetch(self, url, **kwargs):
                return Response(url, 200, {'content-type': 'video/mp4', 'content-length': '2048'}, b'')
        item = collect_url('https://cdn.example.org/stream?id=3', network=Direct())
        self.assertEqual(item['kind'], 'video')
        self.assertEqual(item['assets'][0]['kind'], 'video')

    def test_article_with_login_widget_is_not_discarded(self):
        page = '<title>Authentication explained</title><article><p>' + 'Real research about user authentication. ' * 100 + '</p></article><aside><form><input type="password"></form>Log in</aside>'
        item = collect_url('https://example.org/article', network=StubNetwork(page), render='never')
        self.assertEqual(item['text_status'], 'full_text')
        self.assertFalse(item.get('retrieval_error'))

    def test_verification_document_title_overrides_unrelated_og_title(self):
        page = '<title>Reddit - Prove your humanity</title><meta property="og:title" content="Python"><main><p>Prove your humanity</p><audio src="/challenge.mp3"></audio></main>'
        item = collect_url('https://www.reddit.com/r/Python/', network=StubNetwork(page), render='never')
        self.assertEqual(item['retrieval_error']['code'], 'WEB_VERIFICATION_REQUIRED')
        self.assertEqual(item['content'], '')
        self.assertEqual(item['assets'], [])
        self.assertEqual(item['kind'], 'web')

    def test_runtime_media_keeps_rich_article_text(self):
        body = '<title>Science article</title><article><p>' + 'Scientific findings and research. ' * 100 + '</p></article><video src="https://example.org/clip.mp4"></video>'
        response = Response('https://example.org/article', 200, {}, body.encode(), True)
        response.media_entries = [{'url': response.url, 'title': '', 'authors': [], 'kind': 'video', 'media_url': 'https://example.org/clip.mp4'}]
        item = parse_html(response)
        self.assertIn('Scientific findings', item['content'])
        self.assertEqual(item['kind'], 'web')

    def test_router_keeps_article_when_it_also_contains_a_video(self):
        network = StubNetwork('<title>Science article</title><article><p>' + 'Scientific findings and research. ' * 100 + '</p><video src="/clip.mp4"></video></article>')
        with patch('crawler_system.router.validate_url'), patch('crawler_system.router.known_video', return_value=False), patch('crawler_system.video.collect_video') as adapter:
            item = collect('https://example.org/science', network=network)
        self.assertIn('Scientific findings', item['content'])
        self.assertEqual(item['assets'][0]['kind'], 'mp4')
        adapter.assert_not_called()

    def test_social_feed_does_not_become_its_first_video_card(self):
        page = '<title>Python</title><main><article><video src="https://v.redd.it/clip.mp4"></video><a href="/r/Python/comments/abc123/research/"><h2>Public research discussion</h2></a></article><article><a href="/r/Python/comments/def456/project/"><h2>Another programming project</h2></a></article></main>'
        item = parse_html(Response('https://www.reddit.com/r/Python/', 200, {}, page.encode(), True))
        self.assertEqual(item['kind'], 'web')
        self.assertEqual(item['text_status'], 'listing')
        self.assertEqual(len(item['entries']), 2)
        self.assertIn('Another programming project', item['content'])

    def test_unknown_news_site_can_expose_real_article_cards(self):
        page = '<title>Public news</title><main><article><a href="/stories/first"><h2>First research story</h2></a></article><article><a href="/stories/second"><h2>Second public story</h2></a></article></main>'
        item = parse_html(Response('https://new-public-site.example/', 200, {}, page.encode()))
        self.assertEqual(len(item['entries']), 2)
        self.assertTrue(all(entry['kind'] == 'web' for entry in item['entries']))

    def test_verified_public_alias_preserves_path_and_query_case(self):
        from crawler_system.platforms import preferred_public_url
        value = 'https://bilibili.com/video/BV12DHk69E3H?Token=AbC'
        self.assertEqual(preferred_public_url(value), 'https://www.bilibili.com/video/BV12DHk69E3H?Token=AbC')
        self.assertEqual(preferred_public_url('https://other.example/Page?Q=AbC'), 'https://other.example/Page?Q=AbC')

    def test_alias_does_not_remove_credentials_or_nonstandard_port(self):
        from crawler_system.platforms import preferred_public_url
        for url in ['https://user:secret@bilibili.com/', 'https://bilibili.com:8443/']:
            self.assertEqual(preferred_public_url(url), url)

    def test_youku_redirect_and_image_title_keep_video_links(self):
        page = '<title>优酷</title><main><a href="https://v.youku.com/v_show/id_XMTIz.html"><img src="cover.png" alt="实际公开的剧集标题"></a></main>'
        item = parse_html(Response('https://www.youku.com/ku/webhome', 200, {}, page.encode(), True))
        self.assertEqual(item['kind'], 'playlist')
        self.assertEqual(item['entries'][0]['title'], '实际公开的剧集标题')

    def test_static_http_denial_attempts_browser_in_auto_mode(self):
        class Denied(StubNetwork):
            def fetch(self, url, render=False, **kwargs):
                self.calls.append((url, render))
                if not render:
                    raise CrawlerError('LOGIN_OR_BLOCKED', 'HTTP 403')
                return Response(url, 200, {'content-type': 'text/html'}, ('<article><p>' + 'Public browser content ' * 30 + '</p></article>').encode(), True)
        network = Denied('')
        item = collect_url('https://example.org/article', network=network)
        self.assertEqual([render for _, render in network.calls], [False, True])
        self.assertEqual(item['text_status'], 'full_text')

    def test_static_transport_failure_also_attempts_browser(self):
        class BrowserOnly(StubNetwork):
            def fetch(self, url, render=False, **kwargs):
                self.calls.append((url, render))
                if not render:
                    raise CrawlerError('NETWORK_FAILURE', 'TLS transport unavailable')
                return Response(url, 200, {'content-type': 'text/html'}, b'<article><p>Public page loaded with the browser transport.</p></article>', True)
        network = BrowserOnly('')
        item = collect_url('https://example.org/browser-only', network=network)
        self.assertIn('Public page loaded', item['content'])
        self.assertEqual([render for _, render in network.calls], [False, True])

    def test_jsonld_media_and_iframe_are_separate_from_cover(self):
        page = '<title>Video page</title><main><img src="cover.png"><iframe src="https://www.youtube.com/embed/abcdefg1234"></iframe></main><script type="application/ld+json">{"@type":"VideoObject","name":"A video","contentUrl":"https://cdn.example.org/play?id=123","thumbnailUrl":"cover.png"}</script>'
        item = parse_html(Response('https://example.org/video', 200, {}, page.encode()))
        self.assertEqual(item['assets'][0]['kind'], 'video')
        self.assertEqual(item['assets'][0]['url'], 'https://cdn.example.org/play?id=123')
        self.assertTrue(any(asset['kind'] == 'media' and '/embed/' in asset['url'] for asset in item['assets']))
        self.assertEqual(len([a for a in item['assets'] if a['kind'] == 'png']), 1)

    def test_vimeo_player_iframe_is_a_media_entry(self):
        item = parse_html(Response('https://example.org/film', 200, {}, b'<title>Film</title><iframe src="https://player.vimeo.com/video/76979871"></iframe>'))
        self.assertEqual(item['assets'][0]['kind'], 'media')
        self.assertEqual(item['kind'], 'video')

    def test_mainstream_video_lists_extract_content_links_not_menu_links(self):
        cases = [
            ('https://www.youtube.com/', '/watch?v=abcdefg1234'),
            ('https://www.tiktok.com/explore', '/@example/video/1234567890123456789'),
            ('https://www.acfun.cn/', '/v/ac12345678'),
            ('https://www.youku.com/', 'https://v.youku.com/v_show/id_XMTIz.html'),
        ]
        for url, target in cases:
            with self.subTest(url=url):
                page = '<title>Home</title><main><a href="/login">Sign in</a><a href="' + target + '"><img src="cover.png"><h3>Public video with a real title</h3></a></main>'
                item = parse_html(Response(url, 200, {}, page.encode(), True))
                self.assertEqual(item['kind'], 'playlist')
                self.assertEqual(len(item['entries']), 1)
                self.assertEqual(item['assets'][0]['kind'], 'media')

    def test_audio_source_is_classified_as_audio(self):
        item = parse_html(Response('https://example.org/podcast', 200, {}, b'<title>Podcast</title><main><audio><source src="/stream?id=2" type="audio/mpeg"></audio></main>'))
        self.assertEqual(item['assets'][0]['kind'], 'audio')

    def test_stream_manifest_uses_media_downloader(self):
        page = '<title>Stream</title><main><video><source src="https://cdn.example.org/clip.m3u8" type="application/vnd.apple.mpegurl"></video></main>'
        item = parse_html(Response('https://example.org/stream', 200, {}, page.encode()))
        self.assertEqual(item['assets'][0]['kind'], 'media')

    def test_long_navigation_does_not_stop_pending_video_render(self):
        static = '<title>Player</title><main>' + 'Navigation text ' * 30 + '<video src="blob:placeholder"></video></main><script src="player.js"></script>'
        dynamic = '<title>Player</title><main><video src="https://cdn.example.org/actual.mp4"></video></main>'
        network = StubNetwork(static, dynamic)
        item = collect_url('https://example.org/player', network=network)
        self.assertEqual([render for _, render in network.calls], [False, True])
        self.assertEqual(item['assets'][0]['url'], 'https://cdn.example.org/actual.mp4')

    def test_browser_collects_source_without_counting_title_as_body(self):
        response = Response('https://example.org/video', 200, {}, b'<title>Clip</title><main><h1>Clip</h1></main>', True)
        response.media_entries = [{'url': response.url, 'title': 'Clip', 'authors': [], 'kind': 'video', 'media_url': 'https://cdn.example.org/clip.mp4'}]
        item = parse_html(response)
        self.assertEqual(item['kind'], 'video')
        self.assertEqual(item['content'], '')
        self.assertEqual(item['subtitle_status'], 'unchecked')


if __name__ == '__main__':
    unittest.main()
