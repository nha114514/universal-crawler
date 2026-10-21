import tempfile
import unittest
from pathlib import Path

from crawler_system.store import Store


class StoreTests(unittest.TestCase):
    def test_metadata_refresh_keeps_collected_text_and_captions(self):
        first = self.store.save_item({"url": "https://example.org/video", "title": "Original", "kind": "video", "content": "Saved full text", "text_status": "full_text", "subtitle_text": "Existing captions", "subtitles": [{"start": 0, "end": 1, "text": "Existing captions"}], "subtitle_status": "available"})
        second = self.store.save_item({"url": first["url"], "title": "Refreshed", "kind": "video", "content": "Search snippet", "text_status": "metadata_only", "subtitle_text": "", "subtitles": [], "subtitle_status": "missing"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["content"], "Saved full text")
        self.assertEqual(second["subtitle_text"], "Existing captions")
        self.assertEqual(second["text_status"], "full_text")
        self.assertEqual(second["subtitle_status"], "available")
        self.assertEqual(second["title"], "Refreshed")

    def test_deduplicated_source_url_still_resolves_to_same_item(self):
        first = self.store.save_item({"url": "https://example.org/a", "doi": "10.1000/test", "title": "Paper"})
        second = self.store.save_item({"url": "https://example.org/b", "doi": "10.1000/test", "title": "Paper"})
        third = self.store.save_item({"url": first["url"], "title": "Page metadata"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], third["id"])
        self.assertEqual(self.store.count_items(), 1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "library.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_doi_dedup_and_full_text(self):
        first = self.store.save_item({"kind": "paper", "url": "https://example.org/a", "title": "Paper", "doi": "10.1000/test", "content": "unique evidence"})
        second = self.store.save_item({"kind": "paper", "url": "https://example.org/b", "title": "Updated", "doi": "https://doi.org/10.1000/TEST", "content": "unique evidence"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.list_items(query="evidence")[0]["title"], "Updated")

    def test_empty_content_not_deduplicated(self):
        a = self.store.save_item({"url": "https://example.org/a", "title": "A"})
        b = self.store.save_item({"url": "https://example.org/b", "title": "B"})
        self.assertNotEqual(a["id"], b["id"])

    def test_search_chinese_and_arbitrary_query(self):
        self.store.save_item({"url": "https://example.org", "title": "学术资料", "content": "机器学习 文献摘要"})
        self.assertEqual(len(self.store.list_items(query="机器学习")), 1)
        self.assertEqual(self.store.list_items(query='" SELECT; --'), [])

    def test_job_state_and_restart(self):
        job = self.store.create_job({"inputs": ["hello"]})
        self.store.update_job(job["id"], state="running", progress=1)
        self.assertEqual(self.store.get_job(job["id"])["state"], "running")
        self.store.interrupt_pending()
        self.assertEqual(self.store.get_job(job["id"])["state"], "interrupted")

    def test_url_update_keeps_original_creation(self):
        a = self.store.save_item({"url": "https://example.org/a#first", "title": "First", "content": "original"})
        b = self.store.save_item({"url": "https://example.org/a#second", "title": "Second", "content": "updated"})
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["created_at"], b["created_at"])


if __name__ == "__main__":
    unittest.main()
