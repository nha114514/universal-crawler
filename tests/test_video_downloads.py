import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from crawler_system.errors import CrawlerError
from crawler_system.network import Response
from crawler_system.parsers import parse_html
from crawler_system.store import Store
from crawler_system.worker import execute


class VideoAssetTests(unittest.TestCase):
    def test_extractor_requests_keep_cookies_set_by_earlier_responses(self):
        from crawler_system.video import SafeYoutubeDL
        page_cookies = ('buvid3=abc; path=/; expires=Sat, 14 Jul 2029 05:44:41 GMT; domain=.bilibili.com, '
                        'b_nut=17; path=/; expires=Mon, 18 Oct 2027 05:44:41 GMT; domain=.bilibili.com')
        sent = []
        def fetch(url, method="GET", data=None, headers=None, allow_errors=False, stealth=True):
            self.assertFalse(stealth)
            sent.append(headers or {})
            return Response(url, 200, {"set-cookie": page_cookies} if len(sent) == 1 else {}, b"{}")
        with patch("crawler_system.network.Network.fetch", side_effect=fetch), SafeYoutubeDL({"quiet": True}) as ydl:
            ydl.urlopen("https://www.bilibili.com/video/BV12DHk69E3H")
            ydl.urlopen("https://api.bilibili.com/x/web-interface/nav")
        self.assertNotIn("Cookie", sent[0])
        self.assertEqual(sorted(sent[1]["Cookie"].split("; ")), ["b_nut=17", "buvid3=abc"])

    def test_ready_video_listing_is_not_reprocessed_as_single_video(self):
        from crawler_system.router import collect
        listing = {"kind": "playlist", "entries": [{"url": "https://www.douyin.com/video/1234567890123456789", "kind": "video"}], "assets": [{"url": "https://example.org/video.mp4", "kind": "video"}]}
        with patch("crawler_system.router.validate_url"), patch("crawler_system.router.known_video", return_value=False), patch("crawler_system.router.collect_url", return_value=listing), patch("crawler_system.video.collect_video", side_effect=AssertionError("A homepage is not a single video")):
            self.assertIs(collect("https://www.douyin.com/"), listing)

    def test_homepage_video_is_downloadable_before_covers(self):
        raw = '<html><body><img src="qr.png"><main><a href="/video/BV1abcdefghij"><h3>真实的视频条目</h3></a></main></body></html>'
        item = parse_html(Response("https://www.bilibili.com/", 200, {}, raw.encode(), True))
        self.assertEqual(item["kind"], "playlist")
        self.assertEqual(item["assets"][0]["kind"], "media")
        self.assertEqual(item["assets"][0]["url"], item["entries"][0]["url"])

    def test_video_metadata_fallback_keeps_original_video_before_page_png(self):
        from crawler_system.video import fallback_video
        url = "https://www.bilibili.com/video/BV12DHk69E3H"
        page = {"url": url, "title": "Page", "kind": "web", "assets": [{"url": "https://example.org/512.png", "kind": "png"}], "metadata": {"video_objects": [{"name": "Video", "duration": "PT12S"}]}, "warnings": []}
        with patch("crawler_system.web.collect_url", return_value=page):
            item = fallback_video(url, None, "HTTP 403")
        self.assertEqual(item["kind"], "video")
        self.assertEqual(item["assets"][0]["kind"], "media")
        self.assertEqual(item["assets"][0]["url"], url)

    def test_refresh_puts_video_before_existing_png(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            old = store.save_item({"url": "https://www.douyin.com/", "assets": [{"url": "https://example.org/qr.png", "kind": "png"}]})
            updated = store.save_item({"url": old["url"], "assets": [{"url": "https://www.douyin.com/video/1234567890123456789", "kind": "media"}]})
            self.assertEqual(updated["assets"][0]["kind"], "media")
            self.assertEqual(len(updated["assets"]), 2)

    def test_public_player_response_provides_original_media_not_cover(self):
        response = Response("https://www.douyin.com/jingxuan", 200, {}, b'<html><body><img src="qr.png"></body></html>', True)
        response.media_entries = [{"url": "https://www.douyin.com/video/1234567890123456789", "title": "公开的视频", "kind": "video", "authors": ["作者"], "media_url": "https://www.douyin.com/aweme/v1/play/?video_id=fixture", "duration": 12}]
        item = parse_html(response)
        self.assertEqual(item["text_status"], "listing")
        self.assertEqual(item["assets"][0]["kind"], "video")
        self.assertEqual(item["assets"][0]["url"], response.media_entries[0]["media_url"])
        self.assertEqual(item["entries"][0]["authors"], ["作者"])


class MediaFileTests(unittest.TestCase):
    def test_media_download_preserves_public_player_referer(self):
        content = b'\x00\x00\x00\x18ftypisom' + b'fixture' * 20
        client = httpx.Client
        def reply(request):
            return httpx.Response(200 if request.headers.get('referer') == 'https://example.org/player' else 403, content=content, headers={'content-type': 'video/mp4'})
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.config.DATA', Path(directory)), patch('httpx.Client', side_effect=lambda **options: client(transport=httpx.MockTransport(reply), **options)), patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            result = execute({'operation': 'download', 'url': 'https://example.org/clip.mp4', 'asset_kind': 'media', 'max_mb': 1, 'download_headers': {'Referer': 'https://example.org/player'}})
        self.assertEqual(result['download']['bytes'], len(content))

    def test_pdf_selection_rejects_a_login_page_even_with_pdf_content_type(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CrawlerError):
                self.run_download(directory, b'<html>Please log in</html>', 'application/pdf', kind='pdf')
            self.assertEqual(list((Path(directory) / 'downloads').iterdir()), [])

    def test_separate_tracks_share_the_download_size_limit(self):
        from crawler_system.video import download_video
        def extract(url, download):
            progress = downloader.call_args.args[0]['progress_hooks'][0]
            progress({'filename': 'video.mp4', 'downloaded_bytes': 700000})
            progress({'filename': 'audio.m4a', 'downloaded_bytes': 700000})
            raise AssertionError('Two tracks exceeded the combined limit')
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), patch('crawler_system.video.SafeYoutubeDL') as downloader:
            downloader.return_value.__enter__.return_value.extract_info.side_effect = extract
            with self.assertRaises(CrawlerError) as caught:
                download_video('https://example.org/clip.mpd', directory, max_mb=1)
            self.assertEqual(caught.exception.code, 'SIZE_LIMIT')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_current_iqiyi_parser_failure_is_not_reported_as_member_denial(self):
        import yt_dlp
        from crawler_system.video import download_error
        error = download_error('https://www.iqiyi.com/pcb.html', yt_dlp.utils.DownloadError("ERROR: [iqiyi] Can't find any video"))
        self.assertEqual(error.code, 'VIDEO_EXTRACTOR_INCOMPATIBLE')

    def test_explicit_permission_or_encryption_error_leaves_no_movie_file(self):
        import yt_dlp
        from crawler_system.video import download_video
        for message, expected in [('This video is members-only', 'VIDEO_PERMISSION_REQUIRED'), ('This video is DRM protected', 'VIDEO_DRM_PROTECTED')]:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), patch('crawler_system.video.SafeYoutubeDL') as downloader:
                downloader.return_value.__enter__.return_value.extract_info.side_effect = yt_dlp.utils.DownloadError(message)
                with self.assertRaises(CrawlerError) as caught:
                    download_video('https://v.qq.com/x/cover/example.html', directory)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unknown_http_denial_does_not_claim_member_permission_required(self):
        import yt_dlp
        from crawler_system.video import download_video
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), patch('crawler_system.video.SafeYoutubeDL') as downloader:
            downloader.return_value.__enter__.return_value.extract_info.side_effect = yt_dlp.utils.DownloadError('HTTP Error 403: Forbidden')
            with self.assertRaises(CrawlerError) as caught:
                download_video('https://v.qq.com/x/cover/example.html', directory)
            self.assertEqual(caught.exception.code, 'VIDEO_UNAVAILABLE')

    def run_download(self, directory, content, content_type, kind='video', filename=None, url='https://example.org/play?id=fixture'):
        client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content, headers={"content-type": content_type}))
        with patch("crawler_system.config.DATA", Path(directory)), patch("httpx.Client", side_effect=lambda **options: client(transport=transport, **options)), patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            return execute({"operation": "download", "url": url, "asset_kind": kind, "max_mb": 1, "filename": filename})

    def test_direct_mp4_media_selection_rejects_image_response(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CrawlerError) as caught:
                self.run_download(directory, b'\x89PNG\r\n\x1a\n' + b'x' * 50, 'image/png', kind='media', url='https://example.org/clip.mp4')
            self.assertEqual(caught.exception.code, 'MEDIA_TYPE_MISMATCH')

    def test_pdf_download_label_keeps_file_extension(self):
        content = b'%PDF-1.7\nfixture'
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_download(directory, content, 'application/pdf', kind='pdf', filename='论文 PDF')['download']
            self.assertEqual(Path(result['path']).suffix, '.pdf')
            self.assertEqual(Path(result['path']).read_bytes(), content)

    def test_png_is_rejected_as_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CrawlerError) as caught:
                self.run_download(directory, b'\x89PNG\r\n\x1a\n' + b'x' * 50, 'image/png', kind='audio')
            self.assertEqual(caught.exception.code, 'MEDIA_TYPE_MISMATCH')

    def test_real_wave_audio_is_saved_as_wave(self):
        from io import BytesIO
        import wave
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(8000)
            file.writeframes(b'\0\0' * 8000)
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_download(directory, buffer.getvalue(), 'audio/wav', kind='audio')['download']
            self.assertTrue(result['path'].endswith('.wav'))
            with wave.open(result['path'], 'rb') as file:
                self.assertEqual(file.getnframes(), 8000)

    def test_png_is_rejected_as_video_and_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CrawlerError) as caught:
                self.run_download(directory, b"\x89PNG\r\n\x1a\n" + b"x" * 50, "image/png")
            self.assertEqual(caught.exception.code, "MEDIA_TYPE_MISMATCH")
            self.assertEqual(list((Path(directory) / "downloads").iterdir()), [])

    def test_mp4_from_extensionless_url_is_saved_with_video_extension(self):
        content = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2" + b"fixture" * 50
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_download(directory, content, "video/mp4")["download"]
            self.assertEqual(Path(result["path"]).suffix, ".mp4")
            self.assertEqual(Path(result["path"]).read_bytes(), content)

    def test_html_error_with_video_content_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CrawlerError):
                self.run_download(directory, b"<!doctype html><html>Log in</html>", "video/mp4")

    def test_bilibili_download_invokes_bilibili_adapter(self):
        from crawler_system.video import download_video
        mock_res = {
            "url": "https://www.bilibili.com/video/BV12DHk69E3H",
            "filename": "mock-video.mp4",
            "path": "/mock/path/mock-video.mp4",
            "bytes": 5822414,
            "sha256": "a15a48b1c0143e33eafe0ed98ef79886c5d9c0a3bb6719117632287c675b2b95",
            "content_type": "video/mp4",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch("crawler_system.video.validate_url"), \
                 patch("crawler_system.bilibili.download_bilibili", return_value=mock_res) as mock_bili:
                result = download_video("https://www.bilibili.com/video/BV12DHk69E3H", Path(directory), max_mb=100)
                mock_bili.assert_called_once()
                self.assertEqual(result["sha256"], mock_res["sha256"])
                self.assertEqual(result["content_type"], "video/mp4")
