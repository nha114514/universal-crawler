import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.jobs import JobManager
from crawler_system.network import Response
from crawler_system.parsers import parse_html
from crawler_system.store import Store
from crawler_system.web import collect_url


class StubNetwork:
    def __init__(self, static, dynamic):
        self.static, self.dynamic = static, dynamic
        self.calls = []

    def fetch(self, url, render=False, **kwargs):
        self.calls.append(render)
        value = self.dynamic if render else self.static
        if isinstance(value, Exception):
            raise value
        return Response(url, 200, {"content-type": "text/html"}, value.encode(), render)


BILI_LIST = '<html><head><title>B站首页</title></head><body><main><a href="/video/BV1abcdefghi"><img src="cover.jpg"><h3>真实的视频标题与公开介绍</h3></a><a href="/video/BV2abcdefghi"><h3>第二条公开视频</h3></a></main></body></html>'
DOUYIN_LIST = '<html><head><title>抖音首页</title></head><body><main><a href="/video/1234567890123456789"><img src="cover.jpg"><h3>真实的抖音作品介绍</h3></a></main></body></html>'
TITLE_ONLY = '<html><head><title>抖音-记录美好生活</title></head><body><div id="root">抖音-记录美好生活</div></body></html>'


class DynamicPageTests(unittest.TestCase):
    def test_douyin_public_cards_are_extracted_without_login_popup_text(self):
        raw = '<html><body><main><div data-aweme-id="1234567890123456789"><span>01:09</span><span>568.6K</span><div><span>公开视频的真实介绍 #科普</span></div><span>@ <span>公开视频作者</span> · 21小时前</span></div><div data-aweme-id="1234567890123456789"><span>公开视频的真实介绍 #科普</span><span>@ 公开视频作者</span></div></main><div role="dialog">登录抖音 发送验证码</div></body></html>'
        item = parse_html(Response("https://www.douyin.com/jingxuan", 200, {}, raw.encode(), True))
        self.assertEqual(item["text_status"], "listing")
        self.assertEqual(len(item["entries"]), 1)
        entry = item["entries"][0]
        self.assertEqual(entry["url"], "https://www.douyin.com/video/1234567890123456789")
        self.assertEqual(entry["title"], "公开视频的真实介绍 #科普")
        self.assertEqual(entry["authors"], ["公开视频作者"])
        self.assertNotIn("验证码", item["content"])

    def test_douyin_home_uses_observed_public_entry_and_keeps_source_alias(self):
        raw = '<html><body><main><div data-aweme-id="1234567890123456789"><span>公开的精选视频介绍</span><span>@ 作者</span></div></main></body></html>'
        network = StubNetwork("", raw)
        item = collect_url("https://www.douyin.com/", network=network)
        self.assertEqual(item["url"], "https://www.douyin.com/jingxuan")
        self.assertIn("https://www.douyin.com/", item["source_urls"])
        self.assertEqual(item["text_status"], "listing")
        self.assertFalse(item.get("retrieval_error"))

    def test_platform_home_uses_browser_even_with_long_static_text(self):
        for url, rendered in [("https://www.bilibili.com/", BILI_LIST), ("https://www.douyin.com/", DOUYIN_LIST)]:
            with self.subTest(url=url):
                static = '<html><body><div>' + ('导航和推荐的静态占位信息' * 40) + '</div><script></script></body></html>'
                network = StubNetwork(static, rendered)
                item = collect_url(url, network=network)
                self.assertEqual(network.calls[0], True)
                self.assertEqual(item["extractor"], "scrapling-browser")
                self.assertEqual(item["text_status"], "listing")
                self.assertTrue(item["entries"])

    def test_long_framework_navigation_is_not_complete_content(self):
        static = '<html><body><div id="app"></div>' + ''.join(f'<a href="/menu/{i}">导航选项和登录信息{i}</a>' for i in range(40)) + '<script></script></body></html>'
        rendered = '<html><body><article><p>' + '异步加载的研究正文。' * 35 + '</p></article></body></html>'
        network = StubNetwork(static, rendered)
        item = collect_url("https://example.org/app", network=network)
        self.assertEqual(network.calls, [False, True])
        self.assertIn("异步加载的研究正文", item["content"])

    def test_ordinary_static_article_does_not_need_browser(self):
        static = '<html><body><article><p>' + '静态文章的有效正文。' * 30 + '</p></article><script src="analytics.js"></script></body></html>'
        network = StubNetwork(static, "")
        item = collect_url("https://example.org/article", network=network)
        self.assertEqual(network.calls, [False])
        self.assertEqual(item["text_status"], "full_text")

    def test_explicit_static_mode_is_respected(self):
        network = StubNetwork(BILI_LIST, "")
        item = collect_url("https://www.bilibili.com/", network=network, render="never")
        self.assertEqual(network.calls, [False])
        self.assertEqual(item["extractor"], "scrapling-static")

    def test_browser_title_only_is_reported_as_incomplete(self):
        network = StubNetwork(TITLE_ONLY, TITLE_ONLY)
        item = collect_url("https://www.douyin.com/", network=network)
        self.assertNotEqual(item["text_status"], "full_text")
        self.assertEqual(item["retrieval_error"]["code"], "DYNAMIC_CONTENT_INCOMPLETE")
        self.assertFalse(item["content"])
        self.assertFalse(any("正文通过浏览器渲染取得" in warning for warning in item["warnings"]))

    def test_failed_browser_retains_static_candidates_as_partial(self):
        network = StubNetwork(BILI_LIST, CrawlerError("NETWORK_FAILURE", "browser unavailable"))
        item = collect_url("https://www.bilibili.com/", network=network)
        self.assertEqual(network.calls, [True, False])
        self.assertEqual(item["text_status"], "partial")
        self.assertEqual(item["retrieval_error"]["code"], "DYNAMIC_RENDER_FAILED")
        self.assertTrue(item["entries"])
        self.assertIn("真实的视频标题", item["content"])

    def test_private_url_failure_is_not_hidden_by_static_fallback(self):
        network = StubNetwork(BILI_LIST, CrawlerError("PRIVATE_URL", "blocked"))
        with self.assertRaises(CrawlerError) as raised:
            collect_url("https://www.bilibili.com/", network=network)
        self.assertEqual(raised.exception.code, "PRIVATE_URL")

    def test_listing_deduplicates_video_links_and_excludes_navigation(self):
        raw = '<html><body><main><a href="/">首页</a><a href="/video/BV1abcdefghi"><img src="a.jpg"></a><a href="/video/BV1abcdefghi"><h3>可采集的视频标题</h3></a></main></body></html>'
        item = parse_html(Response("https://www.bilibili.com/", 200, {}, raw.encode(), True))
        self.assertEqual(len(item.get("entries", [])), 1)
        self.assertEqual(item["entries"][0]["title"], "可采集的视频标题")
        self.assertEqual(item["text_status"], "listing")

    def test_listing_author_is_read_from_the_whole_card(self):
        raw = '<html><body><main><div class="bili-video-card"><h3 class="bili-video-card__info--tit"><a href="/video/BV1abcdefghi">公开的视频标题</a></h3><a class="bili-video-card__info--owner" href="https://space.bilibili.com/123">公开作者</a></div></main></body></html>'
        item = parse_html(Response("https://www.bilibili.com/", 200, {}, raw.encode(), True))
        self.assertEqual(item["entries"][0]["authors"], ["公开作者"])

    def test_video_heading_wins_over_thumbnail_controls(self):
        raw = '<html><body><main><a href="/video/BV1abcdefghi">' + '添加至稍后再看播放量和弹幕信息' * 8 + '</a><h3><a href="/video/BV1abcdefghi">实际视频标题</a></h3></main></body></html>'
        item = parse_html(Response("https://www.bilibili.com/", 200, {}, raw.encode(), True))
        self.assertEqual(item["entries"][0]["title"], "实际视频标题")

    def test_failed_generic_render_keeps_better_static_content(self):
        static = '<html><head><title>Study</title></head><body><main>已经取得的有限研究内容和真实信息</main><script></script></body></html>'
        network = StubNetwork(static, '<html><head><title>Study</title></head><body>Study</body></html>')
        item = collect_url("https://example.org/study", network=network)
        self.assertIn("有限研究内容", item["content"])
        self.assertEqual(item["text_status"], "partial")


class DynamicResultTests(unittest.TestCase):
    def test_partial_listing_is_reported_as_list_not_video_text(self):
        item = collect_url("https://www.bilibili.com/", network=StubNetwork(BILI_LIST, CrawlerError("NETWORK_FAILURE", "browser unavailable")))
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            manager = JobManager(store, runner=lambda request, cancel: {"items": [item], "errors": [item["retrieval_error"]]})
            try:
                final = manager.wait(manager.submit({"inputs": [item["url"]]})["id"], 5)
                self.assertEqual(final["state"], "partial")
                self.assertEqual(final["listing_items"], 1)
                self.assertIn("0 with full text/subtitles", final["message"])
            finally:
                manager.close()

    def test_public_entry_redirect_reuses_previous_homepage_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            first = store.save_item({"url": "https://www.douyin.com/", "title": "抖音", "content": "", "text_status": "metadata_only"})
            item = store.save_item({"url": "https://www.douyin.com/jingxuan", "source_urls": [first["url"]], "title": "公开精选", "content": "真实的公开精选内容", "text_status": "listing"})
            self.assertEqual(item["id"], first["id"])
            self.assertEqual(store.count_items(), 1)
            self.assertIn(first["url"], item["source_urls"])
            again = store.save_item({"url": first["url"], "title": "首页刷新"})
            self.assertEqual(again["id"], item["id"])

    def test_incomplete_snapshot_is_partial_job_with_zero_readable_content(self):
        from crawler_system.worker import execute
        incomplete = {"url": "https://www.douyin.com/", "title": "抖音首页", "kind": "web", "content": "", "text_status": "metadata_only", "retrieval_error": {"code": "DYNAMIC_CONTENT_INCOMPLETE", "message": "列表未加载", "action": "重试或确认平台登录"}}
        with tempfile.TemporaryDirectory() as directory, patch("crawler_system.resources.collect_resources", return_value={"items": [incomplete], "errors": [incomplete["retrieval_error"]], "needs_browser": False}):
            store = Store(Path(directory) / "library.sqlite3")
            manager = JobManager(store, runner=lambda request, cancel: execute(request))
            try:
                final = manager.wait(manager.submit({"inputs": [incomplete["url"]]})["id"], 5)
                self.assertEqual(final["state"], "partial")
                self.assertEqual(final["readable_items"], 0)
                self.assertEqual(final["errors"][0]["code"], "DYNAMIC_CONTENT_INCOMPLETE")
            finally:
                manager.close()

    def test_correcting_legacy_title_only_does_not_preserve_false_full_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            first = store.save_item({"url": "https://www.douyin.com/", "title": "抖音-记录美好生活", "content": "抖音-记录美好生活", "text_status": "full_text"})
            corrected = store.save_item({"url": first["url"], "title": first["title"], "content": "", "text_status": "metadata_only", "retrieval_error": {"code": "DYNAMIC_CONTENT_INCOMPLETE"}})
            self.assertEqual(corrected["text_status"], "metadata_only")
            self.assertFalse(corrected["content"])

    def test_legacy_listing_shell_with_url_title_is_corrected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            first = store.save_item({"url": "https://www.douyin.com/", "title": "https://www.douyin.com/", "content": "抖音-记录美好生活", "text_status": "full_text"})
            corrected = store.save_item({"url": first["url"], "title": first["title"], "content": "", "text_status": "metadata_only", "metadata": {"page_type": "listing"}, "retrieval_error": {"code": "DYNAMIC_CONTENT_INCOMPLETE"}})
            self.assertEqual(corrected["text_status"], "metadata_only")
            self.assertFalse(corrected["content"])

    def test_long_legacy_home_navigation_is_not_retained_as_full_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            first = store.save_item({"url": "https://www.bilibili.com/", "title": "B站首页", "content": "旧的导航和封面信息" * 200, "text_status": "full_text"})
            corrected = store.save_item({"url": first["url"], "content": "", "text_status": "metadata_only", "entries": [], "metadata": {"page_type": "listing"}, "retrieval_error": {"code": "DYNAMIC_CONTENT_INCOMPLETE"}})
            self.assertFalse(corrected["content"])
            self.assertEqual(corrected["text_status"], "metadata_only")

    def test_real_saved_listing_survives_an_incomplete_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            first = store.save_item({"url": "https://www.bilibili.com/", "title": "B站", "content": "真实的视频列表与来源", "text_status": "listing", "entries": [{"title": "真实视频", "url": "https://www.bilibili.com/video/BV1abcdefghi"}]})
            refreshed = store.save_item({"url": first["url"], "content": "", "text_status": "metadata_only", "entries": [], "metadata": {"page_type": "listing"}, "retrieval_error": {"code": "DYNAMIC_CONTENT_INCOMPLETE"}})
            self.assertEqual(refreshed["entries"], first["entries"])
            self.assertEqual(refreshed["content"], first["content"])


if __name__ == "__main__":
    unittest.main()
