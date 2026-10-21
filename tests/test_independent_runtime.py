import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawler_system.config import ROOT, settings
from crawler_system.network import Response


class FixtureNetwork:
    def __init__(self, body):
        self.body = body.encode()
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        return Response(url, 200, {'content-type': 'text/html'}, self.body)

    def _pace(self, url):
        pass


class IndependentRuntimeTests(unittest.TestCase):
    def test_keyword_fallback_rejects_totally_unrelated_engine_results(self):
        from crawler_system.search import search_web
        def results(engine, *args):
            return [{'url': 'https://example.org/food', 'title': 'McDonald restaurants'}] if engine == 'google' else [{'url': 'https://example.org/Turtles', 'title': 'Turtle puzzles'}]
        with patch('crawler_system.search.settings', return_value={'search_engines': ['google', 'bing']}), patch('crawler_system.discovery.engine_search', side_effect=results):
            result = search_web('turtle puzzles')
        self.assertEqual(result['items'][0]['url'], 'https://example.org/Turtles')
        self.assertEqual(result['errors'][0]['code'], 'SEARCH_QUERY_UNRELIABLE')

    def test_search_logic_is_packaged_without_adjacent_source_project(self):
        from crawler_system.reconstruction import source
        source.cache_clear()
        with patch('crawler_system.config.DATA', Path(tempfile.gettempdir()) / 'crawler-independent-test'):
            module = source('adapters.google')
        self.assertTrue(Path(module.__file__).is_relative_to(ROOT / 'crawler_system'))

    def test_keyword_search_does_not_load_personal_crawlers_scripts(self):
        from crawler_system.router import search
        with tempfile.TemporaryDirectory() as directory:
            config = {**settings(), 'legacy_config': str(Path(directory) / 'missing.json'), 'search_engines': ['google']}
            Path(directory, 'settings.json').write_text(json.dumps(config), encoding='utf-8')
            network = FixtureNetwork('<div class="g"><a href="https://example.org/Turtles"><h3>Turtle puzzles</h3></a></div>')
            with patch('crawler_system.config.DATA', Path(directory)), patch('crawler_system.discovery.Network', return_value=network):
                result = search('turtle puzzles', mode='web')
        self.assertEqual(result['items'][0]['url'], 'https://example.org/Turtles')
        self.assertTrue(network.calls)

    def test_duckduckgo_fallback_uses_local_parser_and_transport(self):
        from crawler_system.discovery import engine_search
        network = FixtureNetwork('<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2FMixed">Puzzle</a><div class="result__snippet">Public guide</div></div>')
        rows = engine_search('duckduckgo', 'puzzles', 5, network)
        self.assertEqual(rows[0]['url'], 'https://example.org/Mixed')
        self.assertIn('duckduckgo.com', network.calls[0])

    def test_explicit_browser_mode_works_without_extension_daemon(self):
        from crawler_system.resources import collect_resources
        response = Response('https://example.org/files', 200, {'content-type': 'text/html'}, b'<title>Files</title><a href="paper.pdf">Paper</a>', True)
        with tempfile.TemporaryDirectory() as directory, patch('crawler_system.config.DATA', Path(directory)), patch('crawler_system.network.settings', return_value={**settings(), 'browser_profile': str(Path(directory) / 'edge')}), patch('crawler_system.network.validate_url'), patch('crawler_system.resources.validate_url'), patch('crawler_system.network.Network.fetch', return_value=response):
            result = collect_resources(response.url, render='session')
        self.assertEqual(result['items'][0]['assets'][0]['kind'], 'pdf')
        self.assertFalse(result['items'][0]['content'])


if __name__ == '__main__':
    unittest.main()
