import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.network import Response
from crawler_system import iqiyi
from crawler_system.video import download_video


URL = 'https://www.iqiyi.com/v_11mr3ddg90s.html'
TVID = 3907049035032000


def reply(preview=1, identity=TVID, stream=None):
    video = stream or {'duration': 5439, 'ispre': preview, 'drmType': 0,
                      'm3u8Url': 'https://example.org/movie.m3u8', 'bid': 300}
    data = {'tvid': identity, 'boss_ts': {'previewTime': 360, 'previewType': '3',
            'data': {'prv': str(preview)}}, 'program': {'video': [video]}}
    encoded = ''.join(chr(ord(c) ^ 90) for c in json.dumps({'code': 'A00000', 'data': data}))
    return {'tvId': TVID, 'videoInfo': {'tvId': TVID, 'title': '逆行战场'}, 'ev': encoded}


class Reply:
    def __init__(self, value):
        self.value = value
        self.requests = []
        self.config = {'request_timeout': 30}

    def fetch(self, url, **options):
        self.requests.append((url, options))
        return Response(url, 200, {}, json.dumps(self.value).encode())


class IqiyiPlaybackTests(unittest.TestCase):
    def test_preview_with_full_duration_and_manifest_is_not_an_original(self):
        with patch('crawler_system.iqiyi.validate_url'):
            with self.assertRaises(CrawlerError) as caught:
                iqiyi.playback_info(URL, Reply(reply()))
        self.assertEqual(caught.exception.code, 'VIDEO_PREVIEW_ONLY')
        self.assertIn('360', str(caught.exception))
        self.assertIn('5439', str(caught.exception))

    def test_recommended_video_with_wrong_identity_is_rejected(self):
        with patch('crawler_system.iqiyi.validate_url'):
            with self.assertRaises(CrawlerError) as caught:
                iqiyi.playback_info(URL, Reply(reply(preview=0, identity=123)))
        self.assertEqual(caught.exception.code, 'IQIYI_PLAYBACK_UNAVAILABLE')

    def test_advertising_urls_are_not_promoted_to_movie_formats(self):
        data = reply(preview=0, stream={'duration': 5439, 'ispre': 0})
        data['a'] = {'data': {'playUrls': ['https://example.org/advert.mp4']}}
        with patch('crawler_system.iqiyi.validate_url'):
            with self.assertRaises(CrawlerError) as caught:
                iqiyi.playback_info(URL, Reply(data))
        self.assertEqual(caught.exception.code, 'IQIYI_FORMAT_UNSUPPORTED')

    def test_verified_non_trial_clear_manifest_uses_normal_public_headers(self):
        network = Reply(reply(preview=0))
        with patch('crawler_system.iqiyi.validate_url'):
            info = iqiyi.playback_info(URL, network)
        self.assertEqual(info['title'], '逆行战场')
        self.assertEqual(info['manifest_url'], 'https://example.org/movie.m3u8')
        self.assertEqual(info['http_headers']['Referer'], URL)
        self.assertNotIn('Cookie', network.requests[0][1]['headers'])

    def test_unknown_access_and_protected_formats_are_not_assumed_available(self):
        for row in [reply(preview='unknown'), reply(preview=0, stream={
                'duration': 5439, 'ispre': 0, 'drmType': 7,
                'm3u8Url': 'https://example.org/protected.m3u8'})]:
            with self.subTest(row=row), patch('crawler_system.iqiyi.validate_url'):
                with self.assertRaises(CrawlerError):
                    iqiyi.playback_info(URL, Reply(row))

    def test_member_tag_without_playback_data_is_not_permission_proof(self):
        data = {'tvId': TVID, 'videoInfo': {'tvId': TVID, 'title': '逆行战场', 'payMark': 1}}
        with patch('crawler_system.iqiyi.validate_url'):
            with self.assertRaises(CrawlerError) as caught:
                iqiyi.playback_info(URL, Reply(data))
        self.assertEqual(caught.exception.code, 'IQIYI_PLAYBACK_UNAVAILABLE')

    def test_clear_manifest_cannot_target_loopback(self):
        data = reply(preview=0, stream={'duration': 5439, 'ispre': 0, 'drmType': 0,
                                     'm3u8Url': 'http://127.0.0.1/movie.m3u8'})
        with self.assertRaises(CrawlerError) as caught:
            iqiyi.playback_info(URL, Reply(data))
        self.assertEqual(caught.exception.code, 'PRIVATE_URL')

    def test_preview_units_follow_the_player_instead_of_always_using_minutes(self):
        data = reply()
        value = json.loads(''.join(chr(ord(c) ^ 90) for c in data['ev']))
        value['data']['boss_ts'].update(previewTime=6, previewType=1)
        data['ev'] = ''.join(chr(ord(c) ^ 90) for c in json.dumps(value))
        with patch('crawler_system.iqiyi.validate_url'), self.assertRaises(CrawlerError) as caught:
            iqiyi.playback_info(URL, Reply(data))
        self.assertIn('360', str(caught.exception))

    def test_short_download_is_rejected_and_temporary_media_removed(self):
        from crawler_system.bilibili_support.merger import MergerError
        native = {'manifest_url': 'https://example.org/movie.mpd', 'title': 'Film',
                  'id': str(TVID), 'duration': 5439, 'http_headers': {'Referer': URL}}
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), \
                patch('crawler_system.iqiyi.playback_info', return_value=native), \
                patch('crawler_system.video.SafeYoutubeDL') as downloader, \
                patch('crawler_system.bilibili_support.merger.MediaMerger') as merger:
            def complete(info, download):
                target = Path(downloader.call_args.args[0]['outtmpl']).parent / 'movie.mp4'
                target.write_bytes(b'incomplete media fixture')
                downloader.return_value.__enter__.return_value.prepare_filename.return_value = str(target)
                return {'title': 'Film', 'id': str(TVID), 'ext': 'mp4'}
            downloader.return_value.__enter__.return_value.extract_info.return_value = {}
            downloader.return_value.__enter__.return_value.process_ie_result.side_effect = complete
            merger.return_value.verify_mp4.side_effect = MergerError('DURATION_MISMATCH', '仅有试看时长')
            with self.assertRaises(CrawlerError) as caught:
                download_video(URL, directory)
            self.assertEqual(caught.exception.code, 'DURATION_MISMATCH')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_explicit_download_checks_new_interface_before_old_extractor(self):
        network = Reply(reply())
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.video.validate_url'), \
                patch('crawler_system.iqiyi.validate_url'), \
                patch('crawler_system.browser_session.iqiyi_playback_payload', return_value=reply(), create=True), \
                patch('crawler_system.video.Network', return_value=network), \
                patch('crawler_system.video.SafeYoutubeDL') as old:
            with self.assertRaises(CrawlerError) as caught:
                download_video(URL, directory)
            self.assertEqual(caught.exception.code, 'VIDEO_PREVIEW_ONLY')
            old.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_member_page_grant_accepts_vendor_clear_stream_and_dispatch_suffix(self):
        row = {'duration': 5439, 'ispre': 1, 'drmType': 1, 'bid': 100,
               'ml': 'https://example.org/movie.m3u8', 'ms': 'session=fake-test-value', 'vsize': 115396681}
        network = Reply(reply(preview=1))
        with patch('crawler_system.iqiyi.validate_url'), \
                patch('crawler_system.browser_session.iqiyi_playback_payload', return_value=reply(preview=0, stream=row), create=True) as browser:
            info = iqiyi.playback_info(URL, network, use_session=True)
        browser.assert_called_once_with(URL, network.config)
        self.assertEqual(network.requests, [])
        self.assertEqual(info['manifest_url'], row['ml'])
        self.assertEqual(info['extra_param_to_segment_url'], row['ms'])
        self.assertEqual(info['declared_bytes'], row['vsize'])
        self.assertNotIn('Cookie', info['http_headers'])

    def test_clear_alternative_is_used_without_decrypting_selected_stream(self):
        value = reply(preview=0)
        data = json.loads(''.join(chr(ord(c) ^ 90) for c in value['ev']))
        clear = {'duration': 5439, 'ispre': 1, 'drmType': 1, 'bid': 100,
                 'ml': 'https://example.org/clear.m3u8', 'ms': 'session=fake-test-value'}
        encrypted = {**clear, 'bid': 500, '_selected': True, 'uea': 1, 'eak': 'fake-key-must-not-be-used'}
        data['data']['program']['video'] = [encrypted, clear]
        value['ev'] = ''.join(chr(ord(c) ^ 90) for c in json.dumps(data))
        with patch('crawler_system.iqiyi.validate_url'):
            info = iqiyi.playback_info(URL, Reply(value))
        self.assertEqual(info['manifest_url'], clear['ml'])
        self.assertEqual(info['bid'], 100)
        self.assertNotIn('eak', info)

    def test_member_page_preview_is_still_rejected(self):
        with patch('crawler_system.iqiyi.validate_url'), \
                patch('crawler_system.browser_session.iqiyi_playback_payload', return_value=reply(), create=True), \
                self.assertRaises(CrawlerError) as error:
            iqiyi.playback_info(URL, Reply(reply(preview=0)), use_session=True)
        self.assertEqual(error.exception.code, 'VIDEO_PREVIEW_ONLY')

    def test_protected_member_streams_are_rejected_without_exporting_keys(self):
        for markers in [{'drmType': 7}, {'drmType': 8}, {'drmType': 9}, {'eak': 'fake-key'}]:
            row = {'duration': 5439, 'ispre': 1, 'drmType': 1,
                   'ml': 'https://example.org/movie.m3u8', **markers}
            with self.subTest(markers=markers), patch('crawler_system.iqiyi.validate_url'), \
                    self.assertRaises(CrawlerError) as error:
                iqiyi.playback_info(URL, Reply(reply(preview=0, stream=row)))
            self.assertEqual(error.exception.code, 'VIDEO_DRM_PROTECTED')

    def test_iqiyi_download_error_does_not_reveal_signed_urls(self):
        from crawler_system.video import download_error
        error = download_error(URL, '403 https://cdn.example.org/video?qd_uid=private-id&session=private-token')
        self.assertNotIn('private-id', str(error))
        self.assertNotIn('private-token', str(error))

    def test_observed_uea_flag_alone_is_not_a_drm_verdict(self):
        row = {'duration': 5439, 'ispre': 1, 'drmType': 1, 'uea': 1,
               'ml': 'https://example.org/movie.m3u8', 'ms': 'session=fake-test-value'}
        with patch('crawler_system.iqiyi.validate_url'):
            info = iqiyi.playback_info(URL, Reply(reply(preview=0, stream=row)))
        self.assertEqual(info['manifest_url'], row['ml'])

    def test_download_limit_selects_a_fitting_clear_quality(self):
        value = reply(preview=0)
        data = json.loads(''.join(chr(ord(c) ^ 90) for c in value['ev']))
        clear = {'duration': 5439, 'ispre': 1, 'drmType': 1, 'uea': 1,
                 'ml': 'https://example.org/movie.m3u8', 'ms': 'session=fake-test-value'}
        data['data']['program']['video'] = [
            {**clear, 'bid': 600, '_selected': True, 'vsize': 1300000000},
            {**clear, 'bid': 300, 'vsize': 314936718},
            {**clear, 'bid': 100, 'vsize': 115396681}]
        value['ev'] = ''.join(chr(ord(c) ^ 90) for c in json.dumps(data))
        with patch('crawler_system.iqiyi.validate_url'):
            info = iqiyi.playback_info(URL, Reply(value), max_bytes=160*1024*1024)
        self.assertEqual(info['bid'], 100)
        # The server may list its lowest quality before other usable alternatives.
        data['data']['program']['video'].reverse()
        value['ev'] = ''.join(chr(ord(c) ^ 90) for c in json.dumps(data))
        with patch('crawler_system.iqiyi.validate_url'):
            info = iqiyi.playback_info(URL, Reply(value), max_bytes=500*1024*1024)
        self.assertEqual(info['bid'], 300)
        with patch('crawler_system.iqiyi.validate_url'), self.assertRaises(CrawlerError) as error:
            iqiyi.playback_info(URL, Reply(value), max_bytes=100*1024*1024)
        self.assertEqual(error.exception.code, 'SIZE_LIMIT')

    def test_member_hls_key_declarations_stop_before_downloading_segments(self):
        from crawler_system.video import SafeYoutubeDL
        from unittest.mock import Mock
        network = Mock()
        network.fetch.return_value = Response('https://example.org/protected.m3u8', 200, {},
                    b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://example.org/key"\n#EXTINF:4\nsegment.ts\n')
        with SafeYoutubeDL({'quiet': True, '_iqiyi_clear_only': True}, network) as downloader:
            with self.assertRaises(CrawlerError) as error:
                downloader.urlopen('https://example.org/protected.m3u8')
        self.assertEqual(error.exception.code, 'VIDEO_DRM_PROTECTED')
        self.assertEqual(network.fetch.call_count, 1)

    def test_member_dispatch_keeps_server_issued_query_encoding_verbatim(self):
        from crawler_system.video import SafeYoutubeDL
        from unittest.mock import Mock
        network = Mock()
        network.fetch.return_value = Response('https://cdn.example.org/video.ts', 200, {}, b'fixture')
        suffix = 'signature=fake%2Ftest&session=fake+literal'
        url = 'https://data.video.iqiyi.com/video.ts?start=0&end=10'
        with SafeYoutubeDL({'quiet': True, '_iqiyi_segment_suffix': suffix}, network) as downloader:
            downloader.urlopen(url)
            self.assertEqual(network.fetch.call_args.args[0], url + '&' + suffix)
            downloader.urlopen('https://cdn.example.org/video.ts')
            self.assertEqual(network.fetch.call_args.args[0], 'https://cdn.example.org/video.ts')


if __name__ == '__main__':
    unittest.main()
