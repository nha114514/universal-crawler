from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import httpx

from crawler_system.api import DownloadRequest
from crawler_system.config import DEFAULTS
from crawler_system.network import Network


class UnlimitedDownloadsTests(unittest.TestCase):
    def test_default_and_zero_mean_unlimited_and_explicit_limits_remain_valid(self):
        self.assertEqual(DEFAULTS['max_download_mb'], 0)
        self.assertEqual(DownloadRequest(item_id='fixture').max_mb, 0)
        self.assertEqual(DownloadRequest(item_id='fixture', max_mb=0).max_mb, 0)
        self.assertEqual(DownloadRequest(item_id='fixture', max_mb=4000).max_mb, 4000)
        with self.assertRaises(ValueError):
            DownloadRequest(item_id='fixture', max_mb=-1)

    def test_zero_allows_a_stream_larger_than_a_previous_one_mb_limit(self):
        body = b'x' * 1200000
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        with tempfile.TemporaryDirectory() as directory, \
                patch('httpx.Client', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)), \
                patch('crawler_system.network.validate_url'):
            result = Network().download('https://example.org/large.bin', directory, max_mb=0)
            self.assertEqual(result['bytes'], len(body))
            self.assertEqual(Path(result['path']).read_bytes(), body)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_unlimited_video_does_not_abort_at_an_arbitrary_progress_size(self):
        from crawler_system.video import download_video
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), \
                patch('crawler_system.video.SafeYoutubeDL') as factory:
            def extract(url, download):
                factory.call_args.args[0]['progress_hooks'][0]({'downloaded_bytes': 4_000_000_000, 'filename': 'fixture'})
                path = Path(factory.call_args.args[0]['outtmpl']).parent / 'fixture.mp4'
                path.write_bytes(b'fixture')
                factory.return_value.__enter__.return_value.prepare_filename.return_value = str(path)
                return {'id': 'fixture', 'title': 'fixture', 'ext': 'mp4'}
            factory.return_value.__enter__.return_value.extract_info.side_effect = extract
            result = download_video('https://example.org/player', directory, max_mb=0)
            self.assertTrue(Path(result['path']).is_file())
            self.assertIsNone(factory.call_args.args[0]['max_filesize'])

    def test_unlimited_extractor_video_is_not_capped_at_one_page_size_or_paced(self):
        import re
        from crawler_system.network import Response
        from crawler_system.video import MEDIA_CHUNK_BYTES, download_video
        video = b'\x00\x00\x00\x18ftypmp42' + b'x' * (40 * 1024 * 1024)
        page = b'<html><head><title>Clip</title></head><body><video src="https://cdn.example.org/clip.mp4"></video></body></html>'
        served = []
        def static(url, method='GET', data=None, headers=None, stealth=True):
            if not url.startswith('https://cdn.example.org/'):
                return Response(url, 200, {'content-type': 'text/html'}, page)
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', next((v for k, v in (headers or {}).items() if k.lower() == 'range'), ''))
            start, end = (int(match[1]), min(int(match[2] or len(video) - 1), len(video) - 1)) if match else (0, len(video) - 1)
            served.append(end - start + 1)
            return Response(url, 206 if match else 200, {'content-type': 'video/mp4', 'content-length': str(end - start + 1),
                                                         'content-range': f'bytes {start}-{end}/{len(video)}'}, video[start:end + 1])
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), \
                patch('crawler_system.network.validate_url'), patch('crawler_system.network.Network._static', side_effect=static), \
                patch('crawler_system.network.time.sleep') as sleep:
            result = download_video('https://example.org/player', directory, max_mb=0)
            self.assertEqual(Path(result['path']).read_bytes(), video)
        self.assertLessEqual(max(served), MEDIA_CHUNK_BYTES)
        sleep.assert_not_called()

    def test_bilibili_range_downloader_accepts_an_unlimited_budget(self):
        from crawler_system.bilibili_support.downloader import StreamDownloader
        response = SimpleNamespace(status_code=206, headers={'content-range': 'bytes 0-5/6'}, content=b'abcdef')
        downloader = StreamDownloader()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(downloader, 'probe_stream', return_value={'valid_url': 'https://example.org/track', 'total_size': 6}), \
                patch('crawler_system.bilibili_support.downloader.safe_get', return_value=response):
            result = downloader.download_to_file('https://example.org/track', Path(directory) / 'track', max_bytes=None)
            self.assertEqual(result['bytes'], 6)


if __name__ == '__main__':
    unittest.main()
