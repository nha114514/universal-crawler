import tempfile
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from crawler_system.api import create_app
from crawler_system.config import settings
from crawler_system.errors import CrawlerError
from crawler_system.jobs import JobManager
from crawler_system.network import Response
from crawler_system.store import Store
from crawler_system.worker import execute


class ResourceWorkflowTests(unittest.TestCase):
    def test_keyword_video_is_selectable_without_automatic_media_fetch(self):
        item = {'kind': 'video', 'url': 'https://www.bilibili.com/video/BV1abcdefghij', 'title': '海龟汤', 'assets': [], 'content': 'Metadata description'}
        with patch('crawler_system.router.search', return_value={'items': [item], 'errors': []}), patch('crawler_system.video.download_video', side_effect=AssertionError('Downloads require selection')):
            result = execute({'operation': 'search', 'input': 'bili:海龟汤'})
        self.assertEqual(result['items'][0]['assets'][0]['kind'], 'media')
        self.assertEqual(result['items'][0]['assets'][0]['url'], item['url'])
        self.assertFalse(result['items'][0]['content'])

    def test_arxiv_pdf_without_suffix_is_a_pdf_download_choice(self):
        from crawler_system.parsers import parse_html
        item = parse_html(Response('https://arxiv.org/abs/1706.03762', 200, {'content-type': 'text/html'}, b'<title>Paper</title><a href="/pdf/1706.03762">View PDF</a>'), resources_only=True)
        self.assertEqual(item['assets'][0]['kind'], 'pdf')
        self.assertEqual(item['assets'][0]['url'], 'https://arxiv.org/pdf/1706.03762')
        with patch('crawler_system.resources.validate_url'), patch('crawler_system.network.Network.fetch', return_value=Response('https://arxiv.org/pdf/1706.03762', 200, {'content-type': 'application/pdf'}, b'')) as fetch:
            result = execute({'input': 'https://arxiv.org/pdf/1706.03762', 'render': 'never'})
        fetch.assert_called_once_with('https://arxiv.org/pdf/1706.03762', method='HEAD')
        self.assertEqual(result['items'][0]['kind'], 'pdf')

    def test_direct_hls_and_dash_are_media_resources_without_loading_video(self):
        for extension, content_type in [('mpd', 'application/dash+xml'), ('m3u8', 'audio/mpegurl')]:
            url = 'https://example.org/stream.' + extension
            with self.subTest(extension=extension), patch('crawler_system.resources.validate_url'), patch('crawler_system.network.Network.fetch', return_value=Response(url, 200, {'content-type': content_type}, b'')) as fetch:
                item = execute({'input': url, 'render': 'never'})['items'][0]
            fetch.assert_called_once_with(url, method='HEAD')
            self.assertEqual(item['kind'], 'video')
            self.assertEqual(item['assets'][0]['kind'], 'media')
            self.assertFalse(item.get('content'))

    def test_manifest_links_are_download_choices_separate_from_cover(self):
        from crawler_system.parsers import parse_html
        item = parse_html(Response('https://example.org/player', 200, {'content-type': 'text/html'}, b'<title>Public clip</title><a href="clip.mpd">Video</a><a href="clip.m3u8">HLS</a><img src="cover.png">'), resources_only=True)
        self.assertEqual([asset['kind'] for asset in item['assets']], ['media', 'media', 'png'])

    def test_platform_listing_continues_dynamic_scan_after_static_video(self):
        from crawler_system.resources import collect_resources
        response = Response('https://www.bilibili.com/', 200, {'content-type': 'text/html'}, b'<title>Videos</title><a href="/video/BV1abcdefghij"><h3>Public video card</h3></a>')
        with patch('crawler_system.resources.validate_url'), patch('crawler_system.network.Network.fetch', return_value=response):
            result = collect_resources(response.url)
        self.assertTrue(result['items'][0]['entries'])
        self.assertTrue(result['needs_browser'])

    def test_static_denial_requests_browser_instead_of_stopping(self):
        from crawler_system.resources import collect_resources
        with patch('crawler_system.resources.validate_url'), patch('crawler_system.network.Network.fetch', side_effect=CrawlerError('LOGIN_OR_BLOCKED', 'HTTP 403')):
            result = collect_resources('https://example.org/')
        self.assertTrue(result['needs_browser'])
        self.assertEqual(result['static_attempt_error']['code'], 'LOGIN_OR_BLOCKED')

    def test_selected_file_download_preserves_explicit_filename(self):
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.network.Network.download', return_value={'path': 'research.html'}) as download:
            execute({'operation': 'download', 'url': 'https://example.org/research', 'asset_kind': 'html', 'storage_root': directory, 'filename': 'research.html', 'download_headers': {'Referer': 'https://example.org/'}})
        self.assertEqual(download.call_args.kwargs['filename'], 'research.html')
        self.assertEqual(download.call_args.kwargs['headers']['Referer'], 'https://example.org/')

    def test_static_resources_are_visible_before_dynamic_scan_finishes(self):
        reached, release = threading.Event(), threading.Event()
        first = {'url': 'https://example.org/resources', 'title': 'Files', 'assets': [{'url': 'https://example.org/paper.pdf', 'kind': 'pdf'}], 'metadata': {'resources_only': True}}
        def runner(request, cancel):
            if request.get('resource_stage') == 'browser':
                reached.set()
                release.wait(3)
                return {'items': [{**first, 'assets': [*first['assets'], {'url': 'https://example.org/clip.mp4', 'kind': 'video'}]}], 'errors': []}
            return {'items': [first], 'errors': [], 'needs_browser': True}
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.jobs.settings', return_value={**settings(), 'domain_interval': 0}):
            store = Store(Path(directory) / 'library.sqlite3')
            manager = JobManager(store, runner=runner)
            try:
                job = manager.submit({'inputs': [first['url']]})
                self.assertTrue(reached.wait(2))
                interim = store.get_job(job['id'])
                self.assertEqual(interim['state'], 'running')
                self.assertEqual(store.get_item(interim['item_ids'][0])['assets'][0]['kind'], 'pdf')
                release.set()
                final = manager.wait(job['id'], 3)
                self.assertEqual(final['state'], 'completed')
                self.assertEqual(len(store.get_item(final['item_ids'][0])['assets']), 2)
            finally:
                release.set()
                manager.close()

    def test_iqiyi_mismatched_video_response_is_not_accepted(self):
        from crawler_system.iqiyi import public_metadata
        class Reply:
            def fetch(self, url):
                return Response(url, 200, {}, json.dumps({'code': 'A00000', 'data': {'vn': 'Wrong film', 'vu': 'https://www.iqiyi.com/v_another.html'}}).encode())
        with self.assertRaises(CrawlerError) as error:
            public_metadata('https://www.iqiyi.com/v_11mr3ddg90s.html', Reply())
        self.assertEqual(error.exception.code, 'IQIYI_DETAIL_UNAVAILABLE')

    def test_iqiyi_public_api_resolves_url_id_without_loading_player(self):
        url = 'https://www.iqiyi.com/v_11mr3ddg90s.html?posterid=123'
        calls = []
        def fetch(target, **kwargs):
            calls.append(target)
            if 'playervideoinfo' in target:
                return Response(target, 200, {'content-type': 'application/json'}, json.dumps({'code': 'A00000', 'data': {'tvid': 3907049035032000, 'vn': '逆行战场', 'vu': 'http://www.iqiyi.com/v_11mr3ddg90s.html', 'vpic': 'https://example.org/poster.jpg', 'vipType': ['VIP_TYPE_COMMON_VIP']}}).encode())
            return Response(target, 200, {'content-type': 'text/html'}, b'<html><body>Loading</body></html>')
        with patch('crawler_system.router.validate_url'), patch('crawler_system.network.validate_url'), patch('crawler_system.network.Network.fetch', side_effect=fetch):
            item = execute({'input': url})['items'][0]
        self.assertEqual(item['title'], '逆行战场')
        self.assertEqual(len(calls), 1)
        self.assertIn('id=3907049035032000', calls[0])
        self.assertEqual(item['http_status'], None)
        self.assertEqual(item['metadata']['api_http_status'], 200)
        self.assertEqual(item['metadata']['media_url_status'], 'not_extracted')
        self.assertFalse(item.get('content'))

    def test_url_scan_lists_files_without_extracting_article_text(self):
        url = 'https://example.org/research'
        html = '<html><head><title>Research files</title></head><body><article>' + 'Long article. ' * 200 + '</article><a href="/paper.pdf">Paper PDF</a></body></html>'
        with patch('crawler_system.router.validate_url'), patch('crawler_system.network.validate_url'), patch('crawler_system.router.known_video', return_value=False), patch('crawler_system.network.Network.fetch', return_value=Response(url, 200, {'content-type': 'text/html'}, html.encode())):
            item = execute({'input': url})['items'][0]
        self.assertFalse(item.get('content'))
        self.assertFalse(item.get('subtitle_text'))
        self.assertEqual(item['assets'][0]['url'], 'https://example.org/paper.pdf')
        self.assertTrue(item['metadata']['resources_only'])

    def test_pdf_scan_does_not_parse_pdf_text(self):
        url = 'https://example.org/paper.pdf'
        with patch('crawler_system.router.validate_url'), patch('crawler_system.network.validate_url'), patch('crawler_system.network.Network.fetch', return_value=Response(url, 200, {'content-type': 'application/pdf'}, b'%PDF-1.7')), patch('crawler_system.web.parse_pdf', return_value={'url': url, 'kind': 'pdf', 'content': 'Automatic PDF text', 'assets': []}) as parse:
            result = execute({'input': url})
        parse.assert_not_called()
        self.assertFalse(result['items'][0].get('content'))
        self.assertEqual(result['items'][0]['assets'][0]['kind'], 'pdf')

    def test_video_scan_does_not_request_subtitles_or_ytdlp(self):
        url = 'https://www.youtube.com/watch?v=example'
        html = '<title>Video</title><script type="application/ld+json">{"@type":"VideoObject","name":"Video","contentUrl":"https://example.org/clip.mp4"}</script>'
        with patch('crawler_system.router.validate_url'), patch('crawler_system.network.validate_url'), patch('crawler_system.network.Network.fetch', return_value=Response(url, 200, {'content-type': 'text/html'}, html.encode())), patch('crawler_system.video.collect_video', return_value={'url': url, 'kind': 'video', 'subtitle_text': 'Automatic captions'}) as video:
            result = execute({'input': url})
        video.assert_not_called()
        self.assertFalse(result['items'][0].get('subtitle_text'))

    def test_even_legacy_enrich_parameter_cannot_start_automatic_text_fetches(self):
        calls = []
        def runner(request, cancel):
            calls.append(request['operation'])
            return {'items': [{'url': 'https://example.org/video', 'title': 'Video', 'kind': 'video'}], 'errors': []}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            manager = JobManager(store, runner=runner)
            try:
                final = manager.wait(manager.submit({'inputs': ['keyword'], 'enrich': True})['id'], 5)
                self.assertEqual(final['state'], 'completed')
                self.assertEqual(calls, ['search'])
            finally:
                manager.close()


class BatchDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'library.sqlite3')
        self.queued = []
        owner = self
        class Queue:
            def submit(self, payload):
                owner.queued.append(payload)
                return {'id': str(len(owner.queued)), 'state': 'queued'}
        self.item = self.store.save_item({'url': 'https://www.bilibili.com/', 'title': 'Videos', 'entries': [{'url': 'https://www.bilibili.com/video/BV1a/', 'kind': 'video'}, {'url': 'https://www.bilibili.com/video/BV2b/', 'kind': 'video'}], 'assets': [{'url': 'https://example.org/cover.png', 'kind': 'png'}]})
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.store, Queue(), 'test')), base_url='http://127.0.0.1', headers={'Authorization': 'Bearer test'})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_selection_queues_two_videos_once_and_keeps_cover_unselected(self):
        rows = [{'item_id': self.item['id'], 'entry_index': index} for index in [0, 1, 0]]
        response = await self.client.post('/api/downloads/batch', json={'downloads': rows})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['jobs']), 2)
        self.assertEqual(len(self.queued), 2)
        self.assertTrue(all(row['asset_kind'] == 'media' for row in self.queued))

    async def test_invalid_selection_does_not_partially_submit_a_batch(self):
        response = await self.client.post('/api/downloads/batch', json={'downloads': [{'item_id': self.item['id'], 'entry_index': 0}, {'item_id': self.item['id'], 'entry_index': 99}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.queued, [])

    async def test_pdf_entry_keeps_original_type_instead_of_html(self):
        item = self.store.save_item({'url': 'https://example.org/research', 'entries': [{'url': 'https://example.org/paper.pdf', 'title': 'A paper', 'kind': 'pdf'}]})
        response = await self.client.post('/api/downloads/batch', json={'downloads': [{'item_id': item['id'], 'entry_index': 0}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.queued[0]['asset_kind'], 'pdf')
        self.assertEqual(self.queued[0]['filename'], 'paper.pdf')


if __name__ == '__main__':
    unittest.main()
