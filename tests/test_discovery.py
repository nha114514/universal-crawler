import json
import unittest
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.network import Response


class DiscoveryTests(unittest.TestCase):
    def test_bilingual_topic_and_meaning_are_preserved(self):
        from crawler_system.discovery import expand_queries
        result = expand_queries("海龟汤", translator=lambda text, target: "turtle soup")
        queries = [entry["query"] for entry in result["variants"]]
        self.assertIn("海龟汤", queries)
        self.assertIn("lateral thinking puzzles", queries)
        self.assertIn("turtle soup", queries)
        english = expand_queries("marine biology", translator=lambda text, target: "海洋生物学")
        self.assertIn("海洋生物学", [entry["query"] for entry in english["variants"]])

    def test_translation_failure_does_not_discard_original(self):
        from crawler_system.discovery import expand_queries
        def unavailable(text, target):
            raise CrawlerError("NETWORK_FAILURE", "offline")
        result = expand_queries("新的研究话题", translator=unavailable)
        self.assertEqual(result["variants"][0]["query"], "新的研究话题")
        self.assertEqual(result["errors"][0]["channel"], "translation")

    def test_user_related_terms_and_arbitrary_site_are_not_a_whitelist(self):
        from crawler_system.discovery import expand_queries, query_plan
        expanded = expand_queries("海龟汤", english_query="situation puzzles", translator=lambda *args: "turtle soup")
        plan = query_plan(expanded["variants"])
        self.assertTrue(any("situation puzzles" in entry["query"] for entry in plan))
        self.assertTrue(any(entry["scope"] == "global" and "site:" not in entry["query"] for entry in plan))
        self.assertTrue(any("reddit.com" in entry["query"] for entry in plan))
        self.assertTrue(any("bilibili.com" in entry["query"] for entry in plan))
        site = expand_queries("site:unlisted.example 话题", translator=lambda *args: "topic")
        self.assertTrue(all("site:unlisted.example" in item["query"] for item in site["variants"]))
        reddit = expand_queries("reddit: lateral thinking", translator=lambda *args: "横向思维")
        self.assertTrue(all("site:reddit.com" in item["query"] for item in reddit["variants"]))

    def test_translate_response_joins_all_segments(self):
        from crawler_system.discovery import translate_query
        class Network:
            def fetch(self, url):
                self.url = url
                return Response(url, 200, {}, json.dumps([[["deep ", "深度"], ["learning", "学习"]], None, "zh-CN"]).encode())
        network = Network()
        self.assertEqual(translate_query("深度学习", "en", network), "deep learning")
        self.assertIn("tl=en", network.url)

    def test_search_parsers_decode_links_and_reject_private_candidates(self):
        from crawler_system.discovery import parse_results
        bing = b'<li class="b_algo"><h2><a href="https://outside.example/post">English puzzle</a></h2><div class="b_caption"><p>A situation mystery</p></div></li>'
        result = parse_results("bing", Response("https://www.bing.com/search", 200, {}, bing), "puzzles")
        self.assertEqual(result[0]["abstract"], "A situation mystery")
        ddg = b'<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Foutside.example%2Fstory">Story</a><a class="result-link" href="http://127.0.0.1/">Private</a>'
        self.assertEqual([item["url"] for item in parse_results("duckduckgo", Response("https://lite.duckduckgo.com/lite/", 200, {}, ddg), "puzzles")], ["https://outside.example/story"])
        baidu = '<div class="result"><h3><a href="https://outside.example/cn">中文谜题</a></h3><div class="c-abstract">公开介绍</div></div>'.encode()
        self.assertEqual(parse_results("baidu", Response("https://www.baidu.com/s", 200, {}, baidu), "谜题")[0]["title"], "中文谜题")

    def test_http_200_verification_is_not_a_search_success(self):
        from crawler_system.discovery import parse_results
        for engine, page in [("bing", "Please verify you are a human"), ("duckduckgo", "Unfortunately, bots use DuckDuckGo too"), ("baidu", "百度安全验证")]:
            with self.subTest(engine=engine), self.assertRaises(CrawlerError) as raised:
                parse_results(engine, Response("https://engine.example/", 200, {}, page.encode()), "topic")
            self.assertEqual(raised.exception.code, "SEARCH_VERIFICATION_REQUIRED")

    def test_merge_retains_all_origins_and_alternates_sources(self):
        from crawler_system.discovery import merge_results
        a = {"url": "https://same.example/a?utm_source=one", "title": "One", "metadata": {"search_origins": [{"engine": "bing", "query": "海龟汤", "scope": "global"}]}}
        b = {"url": "https://same.example/a", "title": "One", "metadata": {"search_origins": [{"engine": "duckduckgo", "query": "lateral thinking", "scope": "global"}]}}
        c = {"url": "https://foreign.example/b", "title": "English related concept"}
        d = {"url": "https://same.example/c", "title": "Another"}
        result = merge_results([[a, d], [b, c]], "海龟汤", 3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["url"], c["url"])
        self.assertEqual(len(result[0]["metadata"]["search_origins"]), 2)
        self.assertIn("海龟汤", result[1]["metadata"]["collection_queries"])

    def test_engine_failure_stops_only_that_engine(self):
        from crawler_system.discovery import search_broad
        calls = []
        def engine(engine, query, limit, network=None):
            calls.append((engine, query))
            if engine == "bing":
                raise CrawlerError("SEARCH_VERIFICATION_REQUIRED", "verify")
            return [{"url": f"https://{engine}.example/{len(query)}", "title": "Related English material", "kind": "web"}]
        with patch("crawler_system.discovery.translate_query", return_value="topic"), patch("crawler_system.discovery.engine_search", side_effect=engine):
            result = search_broad("主题", limit=12, mode="web")
        self.assertTrue(result["items"])
        self.assertEqual(sum(name == "bing" for name, query in calls), 1)
        self.assertTrue(any(error["channel"] == "bing" for error in result["errors"]))
        self.assertTrue(result["discovery"]["plan"])
        self.assertTrue(any(outcome["state"] == "completed" for outcome in result["discovery"]["engines"]))

    def test_default_broad_search_keeps_non_exact_paper_matches(self):
        from crawler_system.router import search
        paper = {"url": "https://doi.org/10.1000/puzzle", "title": "Lateral thinking in education", "kind": "paper"}
        with patch("crawler_system.discovery.translate_query", return_value="turtle soup"), \
             patch("crawler_system.discovery.engine_search", return_value=[]), \
             patch("crawler_system.papers.search_papers", return_value={"items": [paper], "errors": []}), \
             patch("crawler_system.video.search_video", return_value={"items": [], "errors": []}):
            result = search("海龟汤", limit=8)
        self.assertTrue(any(item["url"] == paper["url"] for item in result["items"]))


if __name__ == "__main__":
    unittest.main()
