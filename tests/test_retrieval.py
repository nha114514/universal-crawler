from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.network import Network, Response, validate_url, safe_filename
from crawler_system.parsers import parse_html, parse_pdf, parse_feed


class RetrievalTests(unittest.TestCase):
    def test_private_addresses_and_credentials_blocked(self):
        for url in ["http://127.0.0.1", "http://[::1]", "http://10.1.2.3", "file:///C:/secret", "https://user:pass@example.org", "http://host.local", "http://169.254.169.254/latest"]:
            with self.subTest(url=url), self.assertRaises(CrawlerError):
                validate_url(url, resolve=False)

    def test_dns_resolution_private_blocked(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.10", 80))]):
            with self.assertRaises(CrawlerError):
                validate_url("https://public-looking.example")

    def test_fake_ip_proxy_dns_allowed_but_not_real_private_answers(self):
        fake = [(2, 1, 6, "", ("198.18.0.151", 443)), (23, 1, 6, "", ("fc00::8e", 443, 0, 0))]
        with patch("socket.getaddrinfo", return_value=fake):
            self.assertEqual(validate_url("https://www.bilibili.com/"), "https://www.bilibili.com/")
        for mixed in ([*fake, (2, 1, 6, "", ("192.168.1.10", 443))], [(23, 1, 6, "", ("fc00::8e", 443, 0, 0))]):
            with self.subTest(mixed=mixed), patch("socket.getaddrinfo", return_value=mixed), self.assertRaises(CrawlerError):
                validate_url("https://public-looking.example")
        with self.assertRaises(CrawlerError):
            validate_url("http://198.18.0.1/", resolve=False)

    def test_redirect_cannot_escape_to_private(self):
        network = Network()
        with patch.object(network, "_static", return_value=Response("https://example.org", 302, {"location": "http://127.0.0.1/private"}, b"")), patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with self.assertRaises(CrawlerError):
                network.fetch("https://example.org")

    def test_main_content_and_assets(self):
        raw = b'<html><head><title>Title</title><meta name="author" content="A"></head><body><nav>Menu</nav><article><h1>Research</h1><p>Actual evidence</p><script>dangerous()</script><a href="paper.pdf">PDF</a></article></body></html>'
        item = parse_html(Response("https://example.org/article", 200, {"content-type": "text/html"}, raw))
        self.assertIn("Actual evidence", item["content"])
        self.assertNotIn("dangerous", item["content"])
        self.assertNotIn("Menu", item["content"])
        self.assertEqual(item["authors"], ["A"])
        self.assertEqual(item["assets"][0]["url"], "https://example.org/paper.pdf")

    def test_scan_pdf_reports_ocr(self):
        from pypdf import PdfWriter
        data = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.write(data)
        item = parse_pdf(data.getvalue(), "https://example.org/a.pdf")
        self.assertEqual(item["text_status"], "needs_ocr")
        self.assertEqual(item["metadata"]["pages"], 1)

    def test_rss_and_atom(self):
        rss = b'<rss><channel><title>Feed</title><item><title>A</title><link>https://example.org/a</link><description>Text</description></item></channel></rss>'
        feed = parse_feed(rss, "https://example.org/rss")
        self.assertEqual(feed["entries"][0]["url"], "https://example.org/a")
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title><entry><title>B</title><link href="https://example.org/b"/><summary>Summary</summary></entry></feed>'
        self.assertIn("Summary", parse_feed(atom, "https://example.org/atom")["content"])

    def test_xml_entity_rejected(self):
        with self.assertRaises(CrawlerError):
            parse_feed(b'<!DOCTYPE feed [<!ENTITY secret SYSTEM "file:///secret">]><feed>&secret;</feed>', "https://example.org/feed")

    def test_safe_filename(self):
        for name in ["../../outside.exe", "CON", "a:b?.pdf", "..‏/secret"]:
            safe = safe_filename(name)
            self.assertNotIn("/", safe)
            self.assertNotIn("\\", safe)
            self.assertNotEqual(safe.upper(), "CON")

    def test_response_size_limit(self):
        network = Network(max_bytes=3)
        with patch.object(network, "_static", return_value=Response("https://example.org", 200, {}, b"1234")), patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with self.assertRaises(CrawlerError):
                network.fetch("https://example.org")

    def test_large_binary_collects_headers_without_get(self):
        from crawler_system.web import collect_url
        network = Network()
        with patch.object(network, "fetch", return_value=Response("https://example.org/large.zip", 200, {"content-type":"application/zip", "content-length":"1000000000"}, b"")) as fetch:
            item = collect_url("https://example.org/large.zip", network=network)
        fetch.assert_called_once_with("https://example.org/large.zip", method="HEAD")
        self.assertEqual(item["metadata"]["bytes"], 1000000000)
        self.assertEqual(item["content"], "")

    def test_jsonld_media_metadata(self):
        data = b'<html><head><title>Page</title><script type="application/ld+json">{"@type":"VideoObject","name":"Video title","duration":"PT2M","contentUrl":"https://example.org/video.mp4","transcript":"Real transcript"}</script></head><body><main>Video</main></body></html>'
        item = parse_html(Response("https://example.org/video",200,{"content-type":"text/html"},data))
        self.assertEqual(item["metadata"]["video_objects"][0]["name"], "Video title")
        self.assertNotIn("VideoObject", item["content"])

    def test_download_stream_limit_cleans_partial_file(self):
        import httpx
        class Chunks(httpx.SyncByteStream):
            def __iter__(self):
                yield b"x" * 600000
                yield b"x" * 600000
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200,stream=Chunks()))
        with tempfile.TemporaryDirectory() as directory, patch("httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport,**kwargs)), patch("socket.getaddrinfo",return_value=[(2,1,6,"",("93.184.216.34",443))]):
            with self.assertRaises(CrawlerError):
                Network().download("https://example.org/large.bin", directory, max_mb=1)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
