import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

from crawler_system.discovery import engine_search, expand_queries, merge_results
from crawler_system.network import Network, Response
from crawler_system.store import Store


class FixtureNetwork:
    def __init__(self, html):
        self.html = html
        self.calls = []

    def _pace(self, url):
        pass

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        body = self.html
        if 'suggestqueries.google.com' in url:
            body = json.dumps(['海龟汤', ['海龟汤 入门']])
        elif '/AS/Suggestions' in url:
            body = '<li class="sa_sg">海龟汤 推理</li>'
        return Response(url, 200, {}, body.encode())


class ReconstructedSearchTests(unittest.TestCase):
    def test_bing_uses_international_market_for_english_query(self):
        from urllib.parse import parse_qs, urlsplit
        network = FixtureNetwork('<li class="b_algo"><h2><a href="https://example.org/Guide">Public guide</a></h2></li>')
        engine_search('bing', 'guide', 5, network)
        parameters = parse_qs(urlsplit(network.calls[0]).query)
        self.assertEqual(parameters['cc'], ['US'])
        self.assertEqual(parameters['setlang'], ['en'])

    def test_google_uses_delivered_parser_with_selected_transport(self):
        network = FixtureNetwork('<div class="g"><a href="/url?q=https%3A%2F%2Fexample.org%2FMixedCase"><h3>Puzzle Guide</h3></a><div class="VwiC3b">Useful puzzles</div></div>')
        rows = engine_search('google', 'puzzles', 5, network)
        self.assertEqual(rows[0]['url'], 'https://example.org/MixedCase')
        self.assertEqual(rows[0]['abstract'], 'Useful puzzles')
        self.assertNotIn('id', rows[0])
        self.assertEqual(rows[0]['subtitle_status'], 'unchecked')
        self.assertIn('search_logic', rows[0]['metadata']['reconstruction_source'])

    def test_bing_preserves_case_through_delivered_parser(self):
        network = FixtureNetwork('<li class="b_algo"><h2><a href="https://example.org/Guide">Public guide</a></h2><div class="b_caption"><p>Guide text</p></div></li>')
        rows = engine_search('bing', 'guide', 5, network)
        self.assertEqual(rows[0]['url'], 'https://example.org/Guide')
        self.assertEqual(rows[0]['metadata']['search_origins'][0]['raw_rank'], 1)

    def test_expansion_runs_both_live_suggestions_without_dictionary_starvation(self):
        network = FixtureNetwork('')
        expanded = expand_queries('海龟汤', network=network)
        self.assertTrue(any('suggestqueries.google.com' in url for url in network.calls))
        self.assertTrue(any('/AS/Suggestions' in url for url in network.calls))
        self.assertTrue(any(row['origin'] == 'google_suggestions' for row in expanded['variants']))

    def test_link_ranking_uses_real_edges_without_rewriting_urls(self):
        rows = [{'url': 'https://example.org/Source/', 'title': 'Source', 'links': [{'url': 'https://other.example/Target/'}]},
                {'url': 'https://other.example/Target/', 'title': 'Target', 'links': []}]
        result = merge_results([rows], 'topic', 10)
        indexed = {row['url']: row for row in result}
        self.assertIn('local_pagerank', indexed[rows[1]['url']]['metadata'])
        self.assertGreater(indexed[rows[1]['url']]['metadata']['local_pagerank'], indexed[rows[0]['url']]['metadata']['local_pagerank'])
        self.assertEqual(set(indexed), {row['url'] for row in rows})

    def test_independent_browser_prefers_edge(self):
        from scrapling.fetchers import DynamicFetcher
        captured = {}
        def fetch(url, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(url=url, status=200, headers={}, body=b'<article>Public fixture</article>', captured_xhr=[])
        with patch.object(DynamicFetcher, 'fetch', side_effect=fetch), patch('crawler_system.network.validate_url'):
            response = Network()._dynamic('https://example.org/')
        self.assertEqual(Path(captured['executable_path']).name, 'msedge.exe')
        self.assertEqual(response.render_diagnostics['browser'], 'edge-temporary-profile')

    def test_decoded_json_is_not_decompressed_twice(self):
        from crawler_system.reconstruction import SelectedClient
        network = FixtureNetwork('{"code": 0}')
        with patch.object(network, 'fetch', return_value=Response('https://api.bilibili.com/x/test', 200, {'content-encoding': 'gzip'}, b'{"code": 0}')):
            response = SelectedClient(network, 'bilibili', {}).get('https://api.bilibili.com/x/test')
        self.assertEqual(response.json()['code'], 0)

    def test_new_search_snippet_keeps_real_outgoing_links_for_scheduling(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            store.save_item({'url': 'https://example.org/article', 'title': 'Actual article', 'text_status': 'full_text', 'content': 'Actual body ' * 30, 'links': [{'url': 'https://example.org/related'}]})
            item = store.save_item({'url': 'https://example.org/article', 'title': 'Search result', 'text_status': 'search_snippet', 'links': []})
            self.assertEqual(item['links'], [{'url': 'https://example.org/related'}])
