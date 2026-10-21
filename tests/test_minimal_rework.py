import unittest

from bs4 import BeautifulSoup

from crawler_system.parsers import content_entries, video_content_link


class MinimalReworkTests(unittest.TestCase):
    def test_iqiyi_tvg_to_page_url_recognition_with_parse_qs(self):
        valid_url = "https://www.iqiyi.com/tvg/to_page_url?album_id=AAA&tv_id=BBB&sign=123"
        invalid_url = "https://www.iqiyi.com/tvg/to_page_url?foo=bar"
        self.assertTrue(video_content_link(valid_url, "iqiyi"))
        self.assertFalse(video_content_link(invalid_url, "iqiyi"))
        self.assertFalse(video_content_link(valid_url.replace("album_id=", "not_album_id=").replace("tv_id=", "not_tv_id="), "iqiyi"))

    def test_iqiyi_content_deduplication_by_entity_id(self):
        html = """
        <html><body>
            <a href="https://www.iqiyi.com/tvg/to_page_url?album_id=ALB1&tv_id=TV1&sign=SIG1&track=sub">
                <span>30集全</span>
            </a>
            <a href="https://www.iqiyi.com/tvg/to_page_url?album_id=ALB1&tv_id=TV1&sign=SIG2&track=main">
                <h2>迎风的青春 弹幕破2000万</h2>
            </a>
            <a href="https://www.iqiyi.com/tvg/to_page_url?album_id=ALB2&tv_id=TV2&sign=SIG3">
                <h2>无所畏惧 最高热度破9000</h2>
            </a>
        </body></html>
        """
        entries = content_entries(BeautifulSoup(html, "html.parser"), "https://www.iqiyi.com/", "iqiyi")
        self.assertEqual(len(entries), 2)
        entry = next(e for e in entries if "ALB1" in e["url"])
        self.assertEqual(entry["title"], "迎风的青春 弹幕破2000万")
        self.assertIn("sign=SIG2", entry["url"])
        self.assertEqual(entry["kind"], "video")

    def test_tiktok_metric_fallback_to_image_alt(self):
        html = """
        <html><body>
            <a href="https://www.tiktok.com/@creator/video/1234567890">
                <picture><img alt="真实完整视频描述 #tiktok" src="https://example.org/thumb.jpg" /></picture>
                <span>348.8K</span>
            </a>
        </body></html>
        """
        entries = content_entries(BeautifulSoup(html, "html.parser"), "https://www.tiktok.com/explore", "tiktok")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "真实完整视频描述 #tiktok")
        self.assertEqual(entries[0]["url"], "https://www.tiktok.com/@creator/video/1234567890")
        self.assertEqual(entries[0]["kind"], "video")

    def test_tiktok_metric_without_alt_is_skipped(self):
        html = '<a href="https://www.tiktok.com/@creator/video/1234567890"><span>12.5K</span></a>'
        entries = content_entries(BeautifulSoup(html, "html.parser"), "https://www.tiktok.com/explore", "tiktok")
        self.assertEqual(entries, [])
