import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from crawler_system.errors import CrawlerError
from crawler_system.jobs import JobManager
from crawler_system.router import search
from crawler_system.store import Store


class SearchTests(unittest.TestCase):
    def test_auto_search_diversifies_websites_before_repeated_links(self):
        web = [{"url": f"https://restricted.example/story/{index}", "title": f"海龟汤 {index}", "kind": "web"} for index in range(5)]
        public = {"url": "https://public.example/answers", "title": "海龟汤题目及答案", "kind": "web"}
        web.append(public)
        with patch("crawler_system.router.search_web", side_effect=lambda query, limit: {"items": web[:limit], "errors": []}), \
             patch("crawler_system.router.search_papers", return_value={"items": [], "errors": []}), \
             patch("crawler_system.video.search_video", return_value={"items": [], "errors": []}):
            result = search("海龟汤", limit=4)
        self.assertEqual(len(result["items"]), 4)
        self.assertIn(public, result["items"])

    def test_auto_search_backfills_web_results_instead_of_unrelated_papers(self):
        web = [{"url": f"https://example.org/story/{index}", "title": f"海龟汤题目 {index}", "kind": "web"} for index in range(8)]
        papers = [{"url": "https://doi.org/10.1000/turtle", "title": "海龟蛋的健康威胁", "kind": "paper"},
                  {"url": "https://doi.org/10.1000/image", "title": "Wavelet Image Compression", "kind": "paper"}]
        videos = [{"url": "https://example.org/video/1", "title": "海龟汤游戏", "kind": "video"},
                  {"url": "https://example.org/video/2", "title": "海龟汤合集", "kind": "video"}]
        with patch("crawler_system.router.search_web", side_effect=lambda query, limit: {"items": web[:limit], "errors": []}), \
             patch("crawler_system.router.search_papers", return_value={"items": papers, "errors": []}), \
             patch("crawler_system.video.search_video", return_value={"items": videos, "errors": []}):
            result = search("海龟汤", limit=8)
        self.assertEqual(len(result["items"]), 8)
        self.assertEqual(sum(item["kind"] == "web" for item in result["items"]), 6)
        self.assertFalse(any(item["kind"] == "paper" for item in result["items"]))
        self.assertIn(web[3], result["items"])

    def test_auto_search_retains_related_scientific_sources(self):
        web = {"url": "https://example.org/article", "title": "Attention in Transformers", "kind": "web"}
        paper = {"url": "https://doi.org/10.1000/paper", "title": "An attention mechanism", "abstract": "Transformer models", "kind": "paper"}
        video = {"url": "https://example.org/video", "title": "Attention tutorial", "kind": "video"}
        with patch("crawler_system.router.search_web", return_value={"items": [web], "errors": []}), \
             patch("crawler_system.router.search_papers", return_value={"items": [paper], "errors": []}), \
             patch("crawler_system.video.search_video", return_value={"items": [video], "errors": []}):
            result = search("attention transformer", limit=3)
        self.assertEqual({item["kind"] for item in result["items"]}, {"web", "paper", "video"})


class EnrichmentTests(unittest.TestCase):
    def test_direct_url_retry_clears_previous_failure(self):
        url = "https://public.example/retry"
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            existing = store.save_item({"url": url, "title": "海龟汤", "enrichment_error": {"code": "LOGIN_OR_BLOCKED"}})
            manager = JobManager(store, runner=lambda request, cancel: {"items": [{"url": url, "title": "海龟汤", "content": "重试取得的正文", "text_status": "full_text", "warnings": []}], "errors": []})
            try:
                job = manager.submit({"inputs": [url], "mode": "auto"})
                self.assertEqual(manager.wait(job["id"], 5)["state"], "completed")
                self.assertIsNone(store.get_item(existing["id"]).get("enrichment_error"))
            finally:
                manager.close()

    def test_metadata_discovery_keeps_candidates_without_opening_pages(self):
        rows = [{'url': 'https://example.org/a', 'title': 'Candidate A'}, {'url': 'https://example.org/b', 'title': 'Candidate B'}]
        calls = []
        def runner(request, cancel):
            calls.append(request['operation'])
            return {'items': rows, 'errors': []}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'library.sqlite3')
            manager = JobManager(store, runner=runner)
            try:
                final = manager.wait(manager.submit({'inputs': ['topic'], 'enrich': True})['id'], 5)
                self.assertEqual(final['state'], 'completed')
                self.assertEqual(len(final['item_ids']), 2)
                self.assertEqual(final['readable_items'], 0)
                self.assertEqual(calls, ['search'])
            finally:
                manager.close()

    def test_metadata_discovery_does_not_claim_old_fetch_failure_was_retried(self):
        url = "https://public.example/article"
        def runner(request, cancel):
            if request["operation"] == "search":
                return {"items": [{"url": url, "title": "海龟汤", "kind": "web", "text_status": "search_snippet"}], "errors": []}
            return {"items": [{"url": url, "title": "海龟汤", "content": "公开题目与答案", "text_status": "full_text", "warnings": []}], "errors": []}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            existing = store.save_item({"url": url, "title": "海龟汤", "enrichment_error": {"code": "HTTP_ERROR"}, "warnings": ["此前正文未取得"]})
            manager = JobManager(store, runner=runner)
            try:
                job = manager.submit({"inputs": ["海龟汤"], "mode": "web", "enrich": True})
                final = manager.wait(job["id"], 5)
                item = store.get_item(existing["id"])
                self.assertEqual(final["state"], "completed")
                self.assertEqual(item["enrichment_error"]["code"], "HTTP_ERROR")
                self.assertIn("此前正文未取得", item["warnings"])
                self.assertFalse(item["content"])
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
