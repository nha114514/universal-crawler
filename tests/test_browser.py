import os
import unittest
from unittest.mock import patch

from crawler_system.config import settings
from crawler_system.network import Network
from crawler_system.parsers import parse_html
from crawler_system.web import collect_url


class BrowserTests(unittest.TestCase):
    def test_deferred_script_cannot_hold_ready_resource_page_hostage(self):
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        url = 'https://example.org/deferred-fixture'
        fixture = '<title>Files</title><main>Public downloadable resource fixture<a href="paper.pdf">Paper</a></main><script defer src="https://example.org/stalled.js"></script>'
        def intercepted_fetch(target, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                page.route(url, lambda route: route.fulfill(status=200, content_type='text/html', body=fixture))
                page.route('https://example.org/stalled.js', lambda route: None)
            options.update(page_setup=setup, timeout=1800)
            return real_fetch(target, **options)
        with patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            response = Network().fetch(url, render=True)
        self.assertTrue(any(asset['kind'] == 'pdf' for asset in parse_html(response, resources_only=True)['assets']))

    def test_blob_player_finds_manifest_and_skips_binary_fetch(self):
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        url = 'https://example.org/mse-fixture'
        fixture = '<title>MSE clip</title><main><video></video></main><script>document.querySelector("video").src=URL.createObjectURL(new Blob([]));setTimeout(()=>fetch("/manifest.mpd").then(()=>fetch("/segment.m4s").catch(()=>{})),350);</script>'
        def intercepted_fetch(target, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                page.route(url, lambda route: route.fulfill(status=200, content_type='text/html', body=fixture))
                page.route('https://example.org/manifest.mpd', lambda route: route.fulfill(status=200, content_type='application/dash+xml', body='<MPD/>'))
            options['page_setup'] = setup
            return real_fetch(target, **options)
        with patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            response = Network().fetch(url, render=True)
        item = parse_html(response, resources_only=True)
        self.assertEqual(item['assets'][0]['url'], 'https://example.org/manifest.mpd')
        self.assertEqual(item['assets'][0]['kind'], 'media')
        self.assertEqual(response.render_diagnostics['content_wait'], 'ready')
        self.assertGreaterEqual(response.render_diagnostics['skipped_media_downloads'], 1)

    def test_ready_content_does_not_wait_for_stalled_third_party_load(self):
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        url = 'https://example.org/stalled-resource-fixture'
        fixture = '<html><body><article><p>Useful public article content is already ready.</p></article><script async src="https://example.org/stalled.js"></script></body></html>'
        def intercepted_fetch(target, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                page.route(url, lambda route: route.fulfill(status=200, content_type='text/html', body=fixture))
                page.route('https://example.org/stalled.js', lambda route: None)
            options.update(page_setup=setup, timeout=1800)
            return real_fetch(target, **options)
        with patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            response = Network().fetch(url, render=True)
        self.assertIn('Useful public article', parse_html(response)['content'])

    def test_generic_player_source_is_captured_without_stream_download(self):
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        url = 'https://example.org/player-fixture'
        fixture = '<html><title>Dynamic clip</title><body><main><video></video></main><script>setTimeout(()=>{document.querySelector("video").src="https://example.org/clip.mp4";},400);</script></body></html>'
        def intercepted_fetch(target, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                page.route(url, lambda route: route.fulfill(status=200, content_type='text/html', body=fixture))
            options['page_setup'] = setup
            return real_fetch(target, **options)
        with patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            response = Network().fetch(url, render=True)
        item = parse_html(response)
        self.assertEqual(item['assets'][0]['url'], 'https://example.org/clip.mp4')
        self.assertEqual(item['kind'], 'video')
        self.assertEqual(item['content'], '')
        self.assertGreaterEqual(response.render_diagnostics['skipped_media_downloads'], 1)

    def test_public_video_addresses_are_captured_from_player_response(self):
        import json
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.async_fetch
        url = "https://www.douyin.com/jingxuan"
        api_url = "https://www.douyin.com/aweme/v2/web/module/feed/"
        fixture = '<html><head><meta charset="utf-8"></head><body><main></main><script>fetch("/aweme/v2/web/module/feed/",{method:"POST"}).then(r=>r.json()).then(()=>{document.querySelector("main").innerHTML=`<div data-aweme-id="1234567890123456789"><span>公开视频</span></div>`;});</script></body></html>'
        payload = {"status_code": 0, "aweme_list": [{"aweme_id": "1234567890123456789", "desc": "公开视频", "author": {"nickname": "作者"}, "video": {"duration": 12000, "download_addr": {"url_list": ["https://example.org/fixture.mp4"]}}}]}

        async def intercepted_fetch(target, **options):
            original_setup = options["page_setup"]
            async def setup(page):
                await original_setup(page)
                await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=fixture))
                await page.route(api_url, lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
            options["page_setup"] = setup
            return await real_fetch(target, **options)

        with patch.object(DynamicFetcher, "async_fetch", side_effect=intercepted_fetch):
            response = Network().fetch(url, render=True)
        self.assertEqual(len(getattr(response, "media_entries", [])), 1)
        self.assertEqual(response.media_entries[0]["media_url"], "https://example.org/fixture.mp4")

    def test_douyin_async_browser_waits_for_public_cards_and_blocks_private_resources(self):
        from scrapling.fetchers import DynamicFetcher
        from crawler_system.errors import CrawlerError
        real_fetch = DynamicFetcher.async_fetch
        url = "https://www.douyin.com/jingxuan"
        fixture = '<html><head><meta charset="utf-8"><title>公开精选</title></head><body><main></main><img src="https://example.org/cover.jpg"><script src="http://127.0.0.1/private"></script><script>setTimeout(()=>{document.querySelector("main").innerHTML=`<div data-aweme-id="1234567890123456789"><span>异步加载的公开视频介绍</span><span>@ 公开作者</span></div>`;},2600);</script></body></html>'

        async def intercepted_fetch(target, **options):
            original_setup = options["page_setup"]

            async def setup(page):
                await original_setup(page)
                await page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=fixture))

            options["page_setup"] = setup
            return await real_fetch(target, **options)

        response, failure = None, None
        with patch.object(DynamicFetcher, "async_fetch", side_effect=intercepted_fetch), patch.object(DynamicFetcher, "fetch", side_effect=AssertionError("Douyin must use asynchronous request checks")):
            try:
                response = Network().fetch(url, render=True)
            except CrawlerError as error:
                failure = error
        self.assertIsNone(failure, str(failure))
        item = parse_html(response)
        self.assertEqual(item["entries"][0]["title"], "异步加载的公开视频介绍")
        self.assertEqual(response.render_diagnostics["content_wait"], "ready")
        self.assertGreaterEqual(response.render_diagnostics["blocked_resources"], 1)

    def test_real_browser_javascript_rendering(self):
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        fixture = '<html><body><main id="root">Loading</main><script>setTimeout(()=>{document.querySelector("main").textContent="Rendered fixture evidence from JavaScript";},100);</script></body></html>'
        def intercepted_fetch(url, **options):
            original_setup = options["page_setup"]
            def setup(page):
                original_setup(page)
                page.route("https://example.org/js-fixture", lambda route: route.fulfill(status=200,content_type="text/html",body=fixture))
            options["page_setup"] = setup
            return real_fetch(url, **options)
        with patch.object(DynamicFetcher, "fetch", side_effect=intercepted_fetch):
            response = Network().fetch("https://example.org/js-fixture", render=True)
        item = parse_html(response)
        self.assertIn("Rendered fixture evidence", item["content"])
        self.assertEqual(item["extractor"], "scrapling-browser")

    def test_resource_browser_uses_one_valid_attempt_without_extensions(self):
        from crawler_system.resources import collect_resources
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        fixture = '<html><title>Files</title><main>Public downloadable resource fixture</main><script>setTimeout(()=>{document.querySelector("main").innerHTML="<a href=paper.pdf>Research PDF</a>";},100);</script></html>'
        def intercepted_fetch(url, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                page.route('https://example.org/resource-fixture', lambda route: route.fulfill(status=200, content_type='text/html', body=fixture))
            options['page_setup'] = setup
            return real_fetch(url, **options)
        with patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            result = collect_resources('https://example.org/resource-fixture', render='always')
        self.assertTrue(any(asset['kind'] == 'pdf' for asset in result['items'][0]['assets']))
        self.assertFalse(result['items'][0]['content'])

    def test_direct_owned_profile_preserves_fixture_cookie_between_runs(self):
        import tempfile
        from pathlib import Path
        from crawler_system.resources import collect_resources
        from crawler_system.config import settings
        from scrapling.fetchers import DynamicFetcher
        real_fetch, observed = DynamicFetcher.fetch, []
        fixture = '<title>Fixture</title><main>Public research fixture with a saved session marker<a href="paper.pdf">Paper</a></main>'
        def intercepted_fetch(url, **options):
            original_setup = options['page_setup']
            def setup(page):
                original_setup(page)
                def deliver(route):
                    observed.append('crawler_fixture=retained' in route.request.headers.get('cookie', ''))
                    route.fulfill(status=200, content_type='text/html', body=fixture, headers={'Set-Cookie': 'crawler_fixture=retained; Max-Age=3600; Path=/; Secure'})
                page.route('https://example.org/profile-fixture', deliver)
            options['page_setup'] = setup
            return real_fetch(url, **options)
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.network.settings', return_value={**settings(), 'browser_profile': str(Path(directory) / 'profile')}), patch.object(DynamicFetcher, 'fetch', side_effect=intercepted_fetch):
            for _ in range(2):
                result = collect_resources('https://example.org/profile-fixture', render='session')
                self.assertFalse(result['items'][0]['content'])
        self.assertEqual(observed, [False, True])

    def test_auto_render_waits_for_delayed_content(self):
        from crawler_system.network import Response
        from scrapling.fetchers import DynamicFetcher
        real_fetch = DynamicFetcher.fetch
        fixture = '<html><body><main id="root">Loading</main><script>setTimeout(()=>{document.querySelector("main").textContent="Delayed meaningful research content from JavaScript. ".repeat(10);},2600);</script></body></html>'
        def intercepted_fetch(url, **options):
            original_setup = options["page_setup"]
            def setup(page):
                original_setup(page)
                page.route("https://example.org/late-fixture", lambda route: route.fulfill(status=200, content_type="text/html", body=fixture))
            options["page_setup"] = setup
            return real_fetch(url, **options)
        network = Network()
        static = Response("https://example.org/late-fixture", 200, {"content-type": "text/html"}, fixture.encode())
        with patch.object(network, "_static", return_value=static), patch.object(DynamicFetcher, "fetch", side_effect=intercepted_fetch):
            item = collect_url(static.url, network=network)
        self.assertIn("Delayed meaningful research content", item["content"])
        self.assertEqual(item["extractor"], "scrapling-browser")


if __name__ == "__main__":
    unittest.main()
