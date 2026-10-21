from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from crawler_system.bilibili import collect_bilibili, download_bilibili
from crawler_system.errors import CrawlerError


class BilibiliIntegrationIntegrityTests(unittest.TestCase):
    def test_adapter_import_is_independent_of_other_prototypes_src_package(self):
        code = "import sys,types;sys.modules['src']=types.ModuleType('src');import crawler_system.bilibili"
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_container_and_duration_must_match_claimed_mp4_before_saving(self):
        from crawler_system.bilibili_support.merger import MediaMerger, MergerError
        merger = object.__new__(MediaMerger)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'movie.mp4'
            path.write_bytes(b'Fixture')
            for probe in [{'has_video': True, 'has_audio': True, 'format_name': 'flv', 'duration': 12},
                          {'has_video': True, 'has_audio': True, 'format_name': 'mov,mp4', 'duration': 3}]:
                with self.subTest(probe=probe), patch.object(merger, 'probe', return_value=probe):
                    with self.assertRaises(MergerError):
                        merger.verify_mp4(path, expected_duration=12, max_bytes=1000)

    def test_metadata_fallback_does_not_claim_subtitles_were_checked(self):
        with patch('crawler_system.bilibili.validate_url'), patch('crawler_system.bilibili.BilibiliExtractor') as extractor:
            extractor.return_value.extract.return_value = {'bvid': 'BV12DHk69E3H', 'title': 'Video', 'desc': 'Description', 'duration': 12}
            item = collect_bilibili('https://bilibili.com/video/BV12DHk69E3H')
        self.assertEqual(item['subtitle_status'], 'unchecked')
        self.assertEqual(item['abstract'], 'Description')
        self.assertEqual(item['content'], '')

    def test_merged_output_size_is_bounded_even_if_tracks_fit(self):
        info = {'bvid': 'BV12DHk69E3H', 'title': 'Video', 'duration': 12, 'stream_type': 'dash',
                'tracks': {'video': {'url': 'https://cdn.example/video'}, 'audio': {'url': 'https://cdn.example/audio'}}}
        def merge(video, audio, output):
            output.write_bytes(b'X' * (1024 * 1024 + 1))
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.bilibili.validate_url'), \
             patch('crawler_system.bilibili.BilibiliExtractor') as extractor, \
             patch('crawler_system.bilibili.StreamDownloader') as downloader, patch('crawler_system.bilibili.MediaMerger') as merger:
            extractor.return_value.extract.return_value = info
            downloader.return_value.download_to_file.return_value = {'bytes': 100}
            merger.return_value.merge_dash.side_effect = merge
            merger.return_value.probe.return_value = {'has_video': True, 'has_audio': True, 'duration': 12, 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'}
            with self.assertRaises(CrawlerError) as caught:
                download_bilibili('https://bilibili.com/video/BV12DHk69E3H', Path(directory), max_mb=1)
            self.assertEqual(caught.exception.code, 'SIZE_LIMIT')
            self.assertFalse(list(Path(directory).glob('*.mp4')))

    def test_legacy_description_is_reclassified_as_abstract_without_losing_id(self):
        from crawler_system.store import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db.sqlite3')
            old = store.save_item({'url': 'https://bilibili.com/video/BV12DHk69E3H', 'content': 'Video description', 'extractor': 'bilibili-reconstruction', 'text_status': 'metadata_only'})
            updated = store.save_item({'url': old['url'], 'content': '', 'abstract': 'Video description', 'extractor': 'bilibili-reconstruction', 'text_status': 'abstract'})
            self.assertEqual(updated['id'], old['id'])
            self.assertEqual(updated['content'], '')
            self.assertEqual(updated['abstract'], 'Video description')


if __name__ == '__main__':
    unittest.main()
