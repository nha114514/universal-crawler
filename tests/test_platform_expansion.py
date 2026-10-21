import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from crawler_system.errors import CrawlerError
from crawler_system.jobs import JobManager
from crawler_system.store import Store


class PlatformExpansionTests(unittest.TestCase):
    def setUp(self):
        from crawler_system.reconstruction import source
        suggestions = patch('crawler_system.reconstruction.expand', side_effect=lambda query, network=None: source('query.expansion').QueryExpander(use_live_suggestions=False).expand(query, max_variants=5))
        suggestions.start()
        self.addCleanup(suggestions.stop)
    def test_catalog_covers_domestic_foreign_categories_and_aliases(self):
        from crawler_system.platforms import catalog, parse_prefix
        rows = catalog()
        self.assertGreaterEqual(len(rows), 60)
        for region in ('domestic', 'foreign'):
            self.assertEqual({row['category'] for row in rows if row['region'] == region},
                             {'news', 'social', 'video', 'audio', 'paper', 'tech', 'reference', 'commerce'})
        self.assertEqual(parse_prefix('Reddit: lateral thinking')[0]['id'], 'reddit')
        self.assertEqual(parse_prefix('B站：海龟汤')[0]['native_prefix'], 'bili')
        self.assertEqual(parse_prefix('知乎: ')[1], '')
        self.assertIsNone(parse_prefix('https://arbitrary.example/page'))

    def test_source_regions_include_new_platforms_without_suffix_spoofing(self):
        from crawler_system.regions import classify_source, check_scope
        self.assertEqual(classify_source('https://www.dailymotion.com/video/x'), 'foreign')
        self.assertEqual(classify_source('https://www.toutiao.com/article/1'), 'domestic')
        self.assertEqual(classify_source('https://reddit.com.evil.example/'), 'unknown')
        with self.assertRaises(CrawlerError):
            check_scope('reddit: puzzles', 'domestic')

    def test_bilingual_original_alias_and_translation_failure(self):
        from crawler_system.discovery import expand_queries
        expanded = expand_queries('海龟汤', translator=lambda *args: 'turtle soup')
        self.assertIn('lateral thinking puzzles', [row['query'] for row in expanded['variants']])
        self.assertIn('海龟汤', [row['query'] for row in expanded['variants']])
        def unavailable(*args):
            raise CrawlerError('NETWORK_FAILURE', 'offline')
        failed = expand_queries('资料检索', translator=unavailable)
        self.assertEqual(failed['variants'][0]['query'], '资料检索')
        self.assertEqual(failed['errors'][0]['channel'], 'translation')

    def test_explicit_new_prefix_preserves_scope_and_platform(self):
        from crawler_system.discovery import expand_queries, query_plan
        expanded = expand_queries('reddit: 科学', english_query='science')
        plan = query_plan(expanded['variants'], platforms=['reddit'], region='foreign')
        self.assertTrue(all('site:reddit.com' in row['query'] for row in plan))
        arbitrary = expand_queries('site:unlisted.example topic')
        self.assertTrue(all('site:unlisted.example' in row['query'] for row in query_plan(arbitrary['variants'])))

    def test_plan_targets_every_catalog_platform_with_global_entry(self):
        from crawler_system.discovery import query_plan
        from crawler_system.platforms import catalog
        plan = query_plan([{'query': 'topic', 'language': 'en'}])
        targeted = {key for row in plan for key in row['platforms']}
        self.assertEqual(targeted, {row['id'] for row in catalog()})
        self.assertTrue(any(row['scope'] == 'global' for row in plan))

    def test_limited_plan_does_not_starve_english_variant(self):
        from crawler_system.discovery import query_plan
        plan=query_plan([{'query':'海龟汤','language':'zh'},{'query':'lateral thinking puzzles','language':'en'}])
        self.assertEqual({row['language'] for row in plan[:2]}, {'zh','en'})

    def test_search_rejects_ignored_site_scope_and_irrelevant_batch(self):
        from crawler_system.discovery import search_broad
        calls=[]
        def engine(name,query,limit,network=None):
            calls.append(name)
            if len(calls) == 1:
                return [{'url':'https://www.w3schools.com/python/','title':'Python Tutorial','abstract':'Learn Python'}]
            return [{'url':'https://www.reddit.com/r/python/comments/a','title':'Python tutorial recommendations'}]
        with patch('crawler_system.discovery.engine_search',side_effect=engine):
            result=search_broad('Python tutorial',platforms=['reddit'],mode='web',request_budget=4)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['url'],'https://www.reddit.com/r/python/comments/a')
        self.assertTrue(any(error['code']=='SEARCH_SCOPE_IGNORED' for error in result['errors']))

    def test_bing_redirect_preserves_path_case_and_blocks_private(self):
        from crawler_system.discovery import decode_search_url
        url = 'https://public.example/Mixed/BV1AbC?q=Case'
        encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
        self.assertEqual(decode_search_url('https://www.bing.com/ck/a?u=a1' + encoded), url)
        self.assertIsNone(decode_search_url('http://127.0.0.1/private'))
        self.assertIsNone(decode_search_url('https://user:secret@public.example/'))

    def test_video_routes_do_not_treat_spoofed_hosts_or_profiles_as_media(self):
        from crawler_system.discovery import kind_for_url
        self.assertEqual(kind_for_url('https://notbilibili.com/video/1'), 'web')
        self.assertEqual(kind_for_url('https://www.tiktok.com/@author'), 'web')
        self.assertEqual(kind_for_url('https://www.tiktok.com/@author/video/123'), 'video')
        self.assertEqual(kind_for_url('https://vimeo.com/76979871'), 'video')

    def test_selected_native_route_keeps_budget_after_empty_index(self):
        from crawler_system.discovery import search_broad
        def candidates(engine, *args):
            return [{'url':'https://www.bilibili.com/video/BV1AbC','title':'Video'}] if engine == 'bilibili' else []
        with patch('crawler_system.discovery.engine_search', side_effect=candidates) as native:
            result=search_broad('bili:topic', limit=8, mode='video', request_budget=2)
        self.assertTrue(native.called)
        self.assertEqual(result['items'][0]['kind'], 'video')
        self.assertLessEqual(result['discovery']['requests'], 2)

    def test_later_topic_keeps_previous_query_in_shared_library(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite3')
            url='https://www.python.org/article'
            one=store.save_item({'url':url,'title':'English','metadata':{'collection_queries':['主题一']}})
            two=store.save_item({'url':url,'title':'English','metadata':{'collection_queries':['主题二']}})
            self.assertEqual(one['id'],two['id'])
            self.assertEqual(store.count_items('主题一'),1)
            self.assertEqual(store.count_items('主题二'),1)

    def test_merge_retains_origins_without_losing_english_searchability(self):
        from crawler_system.discovery import merge_results
        one = {'url': 'https://reddit.com/A?utm_source=x', 'title': 'English', 'metadata': {'search_origins': [{'engine': 'bing', 'query': 'topic'}]}}
        two = {'url': 'https://reddit.com/A', 'title': 'English', 'metadata': {'search_origins': [{'engine': 'duckduckgo', 'query': '主题'}]}}
        other = {'url': 'https://zhihu.com/B', 'title': '中文'}
        rows = merge_results([[one], [two, other]], '主题', 20)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]['metadata']['search_origins']), 2)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            store.save_item(rows[0])
            self.assertEqual(store.count_items('主题'), 1)

    def test_one_engine_failure_isolated_and_budget_is_reported(self):
        from crawler_system.discovery import search_broad
        calls = []
        def engine(name, query, limit, network=None):
            calls.append(name)
            if name == 'google':
                raise CrawlerError('WEB_SEARCH_BLOCKED', 'verify')
            return [{'url': 'https://reddit.com/A', 'title': 'Topic Candidate', 'kind': 'web'}]
        with patch('crawler_system.discovery.engine_search', side_effect=engine):
            result = search_broad('topic', limit=10, mode='web', platforms=['reddit'], request_budget=8)
        self.assertTrue(result['items'])
        self.assertEqual(calls.count('google'), 1)
        self.assertTrue(any(error['channel'] == 'google' for error in result['errors']))
        self.assertTrue(result['discovery']['plan'])

    def test_empty_public_index_reports_actionable_reason(self):
        from crawler_system.discovery import search_broad
        with patch('crawler_system.discovery.engine_search',return_value=[]):
            result=search_broad('topic',mode='web',platforms=['reddit'],request_budget=2)
        self.assertEqual(result['items'],[])
        self.assertEqual(result['errors'][0]['code'],'SEARCH_EMPTY')
        self.assertTrue(result['errors'][0]['action'])

    def test_broad_discovery_retains_candidates_without_text_enrichment(self):
        candidates = [{'url': f'https://www.python.org/story/{i}', 'title': str(i)} for i in range(9)]
        calls = []
        def runner(request, cancel):
            calls.append(request)
            if request['operation'] == 'search':
                return {'items': candidates, 'errors': [], 'discovery': {'plan': [{'query': 'topic'}], 'channels': []}}
            return {'items': [{'url': request['input'], 'content': 'body', 'text_status': 'full_text'}], 'errors': []}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db.sqlite3')
            manager = JobManager(store, runner=runner)
            try:
                job = manager.submit({'inputs': ['topic'], 'strategy': 'broad', 'enrich': True, 'enrich_limit': 2})
                final = manager.wait(job['id'], 15)
                self.assertEqual(len(final['item_ids']), 9)
                self.assertEqual(len([call for call in calls if call['operation'] == 'collect']), 0)
                self.assertEqual(final['events'][0]['discovery']['plan'][0]['query'], 'topic')
            finally:
                manager.close()


class ExpansionApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_accepts_wide_budget_and_exposes_catalog(self):
        from crawler_system.api import create_app
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db.sqlite3')
            manager = JobManager(store, runner=lambda req, cancel: {'items': [], 'errors': []})
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store, manager, 'test')), base_url='http://127.0.0.1', headers={'Authorization': 'Bearer test'}) as client:
                    response = await client.post('/api/jobs', json={'inputs': ['topic'], 'max_results': 60, 'platforms': ['reddit'], 'english_query': 'topic', 'enrich_limit': 6})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['request']['strategy'], 'broad')
                    response = await client.get('/api/platforms')
                    self.assertEqual(response.status_code, 200)
                    self.assertGreaterEqual(len(response.json()['platforms']), 60)
                    self.assertEqual((await client.post('/api/jobs', json={'inputs': ['topic'], 'platforms': ['invented']})).status_code, 422)
            finally:
                manager.close()
