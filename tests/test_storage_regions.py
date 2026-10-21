import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from crawler_system.api import create_app
from crawler_system.jobs import JobManager
from crawler_system.router import search
from crawler_system.store import Store


class RegionStoreTests(unittest.TestCase):
    def test_domain_suffix_match_does_not_trust_a_lookalike_host(self):
        from crawler_system.regions import classify_source
        self.assertEqual(classify_source('https://bilibili.com.attacker.example/a'), 'unknown')
        self.assertEqual(classify_source('https://docs.python.org/zh-cn/'), 'foreign')
        self.assertEqual(classify_source('https://research.edu.cn/article'), 'domestic')
        self.assertEqual(classify_source('https://research.com/article', {'research.com': 'foreign'}), 'foreign')

    def test_source_site_decides_region_instead_of_language(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            domestic = store.save_item({'url': 'https://www.bilibili.com/video/BV1Test', 'title': 'English lesson'})
            foreign = store.save_item({'url': 'https://www.youtube.com/watch?v=test', 'title': '中文讲解'})
            unknown = store.save_item({'url': 'https://new-source.example/article', 'title': '中文 English'})
            self.assertEqual(domestic.get('source_region'), 'domestic')
            self.assertEqual(foreign.get('source_region'), 'foreign')
            self.assertEqual(unknown.get('source_region'), 'unknown')
            self.assertEqual(store.list_items(region='domestic')[0]['id'], domestic['id'])
            self.assertEqual(store.count_items(region='foreign'), 1)

    def test_identical_body_on_opposite_sources_stays_in_both_libraries(self):
        body = 'A useful research article. ' * 20
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            a = store.save_item({'url': 'https://example.cn/a', 'title': 'Article', 'content': body})
            b = store.save_item({'url': 'https://www.python.org/a', 'title': 'Article', 'content': body})
            self.assertNotEqual(a['id'], b['id'])
            self.assertEqual(store.count_items(), 2)

    def test_cross_region_provenance_alias_does_not_collapse_two_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            domestic = store.save_item({'url': 'https://example.cn/a', 'source_urls': ['https://python.org/a'], 'content': 'Same body ' * 30})
            foreign = store.save_item({'url': 'https://python.org/a', 'content': 'Same body ' * 30})
            self.assertNotEqual(domestic['id'], foreign['id'])
            refreshed = store.save_item({'url': 'https://python.org/a', 'title': 'Refreshed'})
            self.assertEqual(refreshed['id'], foreign['id'])
            self.assertEqual(store.count_items(region='domestic'), 1)

    def test_constructor_repairs_sql_region_when_payload_already_has_region(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'library.sqlite3'
            item = Store(path).save_item({'url': 'https://python.org/a'})
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE items SET source_region='unknown' WHERE id=?", (item['id'],))
            restored = Store(path)
            self.assertEqual(restored.count_items(region='foreign'), 1)
            self.assertEqual(restored.get_item(item['id'])['source_region'], 'foreign')

    def test_old_database_backfills_region_without_changing_ids_or_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE items(id TEXT PRIMARY KEY,url TEXT,doi TEXT,digest TEXT,kind TEXT,title TEXT,created_at TEXT,updated_at TEXT,payload TEXT)')
                payload = {'id': 'old', 'url': 'https://arxiv.org/abs/1234.5678', 'title': '论文', 'content': 'Old body', 'kind': 'paper'}
                db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)', ('old', payload['url'], '', '', 'paper', '论文', 'before', 'before', json.dumps(payload)))
            store = Store(path)
            self.assertEqual(store.list_items(region='foreign')[0]['id'], 'old')
            self.assertEqual(store.get_item('old')['content'], 'Old body')
            self.assertEqual(Store(path).count_items(region='foreign'), 1)

    def test_saved_material_json_contains_body_captions_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'chosen 保存位置'
            store = Store(Path(directory) / 'index.sqlite3', storage_root=root)
            item = store.save_item({'url': 'https://github.com/example/research', 'title': '资料', 'content': 'Full body', 'subtitle_text': 'Transcript', 'metadata': {'duration': 10}})
            path = Path(item['material_path'])
            self.assertTrue(path.is_relative_to(root / '国外' / '资料'))
            actual = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(actual['content'], 'Full body')
            self.assertEqual(actual['subtitle_text'], 'Transcript')
            self.assertEqual(actual['metadata']['duration'], 10)

    def test_running_job_keeps_directory_selected_when_submitted(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first', Path(directory) / 'second'
            store = Store(Path(directory) / 'index.sqlite3', storage_root=first)
            entered, release = threading.Event(), threading.Event()
            def runner(request, cancel):
                entered.set()
                release.wait(3)
                return {'items': [{'url': request['input'], 'title': 'Evidence', 'content': 'Saved content'}], 'errors': []}
            manager = JobManager(store, runner=runner)
            try:
                job = manager.submit({'inputs': ['https://www.python.org/first']})
                self.assertTrue(entered.wait(2))
                store.set_storage_root(second)
                release.set()
                final = manager.wait(job['id'], 5)
                self.assertEqual(final['state'], 'completed')
                self.assertTrue(Path(store.get_item(final['item_ids'][0])['material_path']).is_relative_to(first))
                new = manager.submit({'inputs': ['https://www.python.org/second']})
                final = manager.wait(new['id'], 5)
                self.assertTrue(Path(store.get_item(final['item_ids'][0])['material_path']).is_relative_to(second))
            finally:
                release.set()
                manager.close()


class RegionSearchTests(unittest.TestCase):
    def test_foreign_scope_filters_before_limiting_and_accepts_overseas_chinese(self):
        candidates = [{'url': 'https://bilibili.com/1', 'title': 'Domestic'},
                      {'url': 'https://zhihu.com/2', 'title': 'Domestic'},
                      {'url': 'https://zh.wikipedia.org/wiki/Example', 'title': '中文资料'},
                      {'url': 'https://www.python.org/3', 'title': 'English'},
                      {'url': 'https://new.example/4', 'title': 'Unknown'}]
        with patch('crawler_system.router.search_web', return_value={'items': candidates, 'errors': []}):
            result = search('资料', mode='web', limit=2, region='foreign')
        self.assertEqual([item['title'] for item in result['items']], ['中文资料', 'English'])
        self.assertEqual(result['scope']['unknown'], 1)

    def test_domestic_auto_scope_does_not_call_foreign_paper_or_youtube_provider(self):
        candidate = {'url': 'https://www.bilibili.com/video/BV1Test', 'title': '研究资料'}
        with patch('crawler_system.router.search_web', return_value={'items': [candidate], 'errors': []}), \
             patch('crawler_system.router.search_papers', side_effect=AssertionError('Foreign source')), \
             patch('crawler_system.video.search_video', side_effect=AssertionError('YouTube source')):
            result = search('研究资料', mode='auto', region='domestic')
        self.assertEqual(len(result['items']), 1)

    def test_explicit_domestic_prefix_cannot_bypass_foreign_scope(self):
        from crawler_system.errors import CrawlerError
        with patch('crawler_system.router.search_web', side_effect=AssertionError('Should reject before search')):
            with self.assertRaises(CrawlerError) as caught:
                search('bili:资料', mode='web', region='foreign')
        self.assertEqual(caught.exception.code, 'SOURCE_SCOPE_MISMATCH')

    def test_scoped_queries_do_not_turn_negative_site_operator_into_platform_route(self):
        from crawler_system.search import search_web
        from types import SimpleNamespace
        with patch('crawler_system.discovery.engine_search', return_value=[]) as engine:
            search_web('topic -site:bilibili.com', region='foreign')
        self.assertIn('-site:bilibili.com', engine.call_args.args[1])
        self.assertNotEqual(engine.call_args.args[0], 'bilibili')


class RegionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'index.sqlite3')
        self.requests = []
        def runner(request, cancel):
            self.requests.append(request)
            return {'items': [{'url': request['input'], 'title': 'Test', 'content': 'Evidence'}], 'errors': []}
        self.manager = JobManager(self.store, runner=runner)
        self.data_patch = patch('crawler_system.api.DATA', self.root)
        self.data_patch.start()
        self.config_patch = patch('crawler_system.config.DATA', self.root)
        self.config_patch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.store, self.manager, 'test-token')), base_url='http://127.0.0.1', headers={'Authorization': 'Bearer test-token'})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.manager.close()
        self.data_patch.stop()
        self.config_patch.stop()
        self.temp.cleanup()

    async def test_region_filters_items_and_total_consistently(self):
        local = self.store.save_item({'url': 'https://www.bilibili.com/a', 'title': '研究资料'})
        self.store.save_item({'url': 'https://www.youtube.com/a', 'title': '研究资料'})
        self.store.save_item({'url': 'https://unknown.example/a', 'title': '研究资料'})
        response = await self.client.get('/api/items?region=domestic&q=研究资料')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['items']], [local['id']])
        self.assertEqual(response.json()['total'], 1)

    async def test_unwritable_or_relative_storage_target_does_not_report_saved(self):
        response = await self.client.put('/api/settings', json={'storage_root': 'relative-folder'})
        self.assertEqual(response.status_code, 400)
        file = self.root / 'file.txt'
        file.write_text('Keep me')
        response = await self.client.put('/api/settings', json={'storage_root': str(file)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(file.read_text(), 'Keep me')

    async def test_setting_path_is_used_for_next_collection_and_export(self):
        chosen = self.root / '材料 保存'
        response = await self.client.put('/api/settings', json={'storage_root': str(chosen)})
        self.assertEqual(response.status_code, 200)
        response = await self.client.post('/api/jobs', json={'inputs': ['https://www.python.org/'], 'region': 'foreign'})
        final = self.manager.wait(response.json()['id'], 5)
        item = self.store.get_item(final['item_ids'][0])
        self.assertTrue(Path(item['material_path']).is_relative_to(chosen / '国外'))
        response = await self.client.get('/api/export?format=json&region=foreign')
        from urllib.parse import unquote
        path = Path(unquote(response.headers['x-artifact-path']))
        self.assertTrue(path.is_relative_to(chosen / '国外' / '导出'))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))[0]['id'], item['id'])

    async def test_source_override_updates_existing_items_and_future_classification(self):
        item = self.store.save_item({'url': 'https://research.example/article', 'title': 'Unknown site'})
        response = await self.client.put('/api/source-region', json={'url': item['url'], 'region': 'foreign'})
        self.assertEqual(response.status_code, 200)
        saved = (await self.client.get('/api/items/' + item['id'])).json()
        self.assertEqual(saved['source_region'], 'foreign')
        response = await self.client.post('/api/jobs', json={'inputs': ['https://research.example/new']})
        final = self.manager.wait(response.json()['id'], 5)
        self.assertEqual(self.store.get_item(final['item_ids'][0])['source_region'], 'foreign')

    async def test_download_uses_source_page_region_and_keeps_old_files_readable(self):
        source = 'https://www.bilibili.com/video/BV1Test'
        item = self.store.save_item({'url': source, 'assets': [{'url': 'https://cdn.foreign.example/video.mp4', 'source_url': source, 'kind': 'video'}]})
        response = await self.client.post('/api/downloads', json={'item_id': item['id']})
        self.manager.wait(response.json()['id'], 5)
        self.assertEqual(self.requests[0]['source_region'], 'domestic')
        old = self.root / 'downloads' / 'old.mp4'
        old.parent.mkdir()
        old.write_bytes(b'Old file')
        record = self.store.save_download(item['id'], {'path': str(old), 'filename': old.name})
        response = await self.client.get('/api/downloads/' + record['id'] + '/file')
        self.assertEqual(response.content, b'Old file')

    async def test_file_endpoint_allows_recorded_new_root_but_rejects_escape(self):
        new = self.root / 'external' / '国外' / '下载'
        new.mkdir(parents=True)
        file = new / 'test.mp4'
        file.write_bytes(b'Media')
        record = self.store.save_download('', {'path': str(file), 'filename': file.name, 'storage_root': str(self.root / 'external'), 'source_region': 'foreign'})
        response = await self.client.get('/api/downloads/' + record['id'] + '/file')
        self.assertEqual(response.content, b'Media')
        outside = self.root / 'outside.txt'
        outside.write_text('Private')
        record = self.store.save_download('', {'path': str(outside), 'filename': outside.name, 'storage_root': str(self.root / 'external'), 'source_region': 'foreign'})
        self.assertEqual((await self.client.get('/api/downloads/' + record['id'] + '/file')).status_code, 404)

    async def test_invalid_region_is_rejected_in_collection_and_library(self):
        self.assertEqual((await self.client.post('/api/jobs', json={'inputs': ['topic'], 'region': 'Chinese'})).status_code, 422)
        self.assertEqual((await self.client.get('/api/items?region=Chinese')).status_code, 422)

    async def test_folder_picker_uses_current_root_and_requires_authentication(self):
        with patch('crawler_system.api.choose_folder', return_value=str(self.root / 'selected')) as picker:
            response = await self.client.post('/api/storage/choose-folder', headers={'Authorization': ''})
            self.assertEqual(response.status_code, 401)
            picker.assert_not_called()
            response = await self.client.post('/api/storage/choose-folder')
            self.assertEqual(response.json()['path'], str(self.root / 'selected'))
            picker.assert_called_once_with(str(self.store.storage_root()))

    async def test_filtered_export_does_not_leak_selected_ids_from_other_partition(self):
        local = self.store.save_item({'url': 'https://bilibili.com/a', 'title': 'Local'})
        foreign = self.store.save_item({'url': 'https://python.org/a', 'title': 'Foreign'})
        response = await self.client.get('/api/export', params={'format': 'json', 'ids': ','.join([local['id'], foreign['id']]), 'region': 'domestic'})
        self.assertEqual([item['id'] for item in response.json()], [local['id']])


class RegionWorkerTests(unittest.TestCase):
    def test_original_media_is_written_under_parent_site_region_in_selected_root(self):
        from crawler_system.network import Network
        from crawler_system.worker import execute
        original_client = httpx.Client
        content = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 500
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content, headers={'content-type': 'video/mp4'}))
        with tempfile.TemporaryDirectory() as directory:
            with patch('httpx.Client', side_effect=lambda **options: original_client(transport=transport, **options)), \
                 patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
                result = execute({'operation': 'download', 'url': 'https://cdn.example/movie.mp4', 'input': 'https://bilibili.com/video/test',
                                  'asset_kind': 'video', 'source_region': 'domestic', 'storage_root': directory, 'max_mb': 1})
            path = Path(result['download']['path'])
            self.assertTrue(path.is_relative_to(Path(directory) / '国内' / '下载'))
            self.assertEqual(path.read_bytes(), content)

    def test_parent_region_with_no_explicit_root_retains_worker_compatibility(self):
        from crawler_system.worker import execute
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.config.DATA', Path(directory)), \
             patch('crawler_system.video.download_video', return_value={'path': 'not-downloaded'}) as downloader:
            execute({'operation': 'download', 'url': 'https://bilibili.com/video/test', 'asset_kind': 'media'})
            self.assertEqual(downloader.call_args.args[1], Path(directory) / 'downloads')


if __name__ == '__main__':
    unittest.main()
