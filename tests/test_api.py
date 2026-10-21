import asyncio
import tempfile
import time
import unittest
from pathlib import Path
import threading

import httpx

from crawler_system.api import create_app
from crawler_system.errors import CrawlerError
from crawler_system.jobs import JobManager
from crawler_system.store import Store
from crawler_system.exports import export_items


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_partial_failure_and_dedup(self):
        def runner(request, event):
            if "bad" in request["input"]:
                raise CrawlerError("HTTP_ERROR", "Unavailable")
            return {"items": [{"url": request["input"], "title": "Collected", "content": "Evidence"}], "errors": []}
        manager = JobManager(self.store, runner=runner)
        try:
            job = manager.submit({"inputs": ["https://example.org/good", "https://example.org/bad"], "mode": "auto"})
            finished = manager.wait(job["id"], 5)
            self.assertEqual(finished["state"], "partial")
            self.assertEqual(len(finished["item_ids"]), 1)
            self.assertEqual(finished["errors"][0]["code"], "HTTP_ERROR")
        finally:
            manager.close()

    def test_cancel_running_job(self):
        def runner(request, event):
            while not event.wait(0.01):
                pass
            raise CrawlerError("CANCELLED", "Cancelled")
        manager = JobManager(self.store, runner=runner)
        try:
            job = manager.submit({"inputs": ["https://example.org"], "mode": "web"})
            time.sleep(0.02)
            manager.cancel(job["id"])
            self.assertEqual(manager.wait(job["id"], 2)["state"], "cancelled")
        finally:
            manager.close()

    def test_export_formats(self):
        item = {"id": "a", "url": "https://example.org", "title": "=Unsafe CSV", "content": "Evidence", "authors": [], "doi": "", "kind": "web"}
        self.assertIn("Evidence", export_items([item], "markdown")[0])
        self.assertIn("https://example.org", export_items([item], "json")[0])
        self.assertIn("'=Unsafe CSV", export_items([item], "csv")[0])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db.sqlite3")
        self.manager = JobManager(self.store, runner=lambda request, event: {"items": [{"url": request["input"], "title": "Test", "content": "Evidence"}], "errors": []})
        self.app = create_app(store=self.store, manager=self.manager, api_token="test-token")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.manager.close()
        self.temp.cleanup()

    async def test_auth_and_cross_site_origin(self):
        self.assertEqual((await self.client.get("/api/items")).status_code, 401)
        result = await self.client.post("/api/jobs", json={"inputs": ["https://example.org"]}, headers={"Authorization": "Bearer test-token", "Origin": "https://evil.example"})
        self.assertEqual(result.status_code, 403)

    async def test_job_and_shared_item_read(self):
        headers = {"Authorization": "Bearer test-token"}
        response = await self.client.post("/api/jobs", json={"inputs": ["https://example.org"]}, headers=headers)
        self.assertEqual(response.status_code, 200)
        job = self.manager.wait(response.json()["id"], 5)
        item = await self.client.get("/api/items/" + job["item_ids"][0], headers=headers)
        self.assertEqual(item.json()["content"], "Evidence")
        library = await self.client.get("/api/items?q=Evidence", headers=headers)
        self.assertEqual(library.json()["items"][0]["id"], item.json()["id"])

    async def test_input_validation(self):
        response = await self.client.post("/api/jobs", json={"inputs": []}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/api/jobs", json={"inputs": ["https://example.org"], "max_results": 9000}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 422)

    async def test_download_video_entry_does_not_select_cover_asset(self):
        captured = []
        def runner(request, event):
            captured.append(request)
            raise CrawlerError("FIXTURE_STOP", "Request routing checked without downloading")
        self.manager.runner = runner
        parent = self.store.save_item({"url": "https://www.douyin.com/", "entries": [{"url": "https://www.douyin.com/video/1234567890123456789", "title": "Video", "kind": "video"}], "assets": [{"url": "https://example.org/qr.png", "kind": "png"}]})
        response = await self.client.post('/api/downloads', json={"item_id": parent["id"], "entry_index": 0}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.manager.wait(response.json()["id"], 5)
        self.assertEqual(captured[0]["asset_kind"], "media")
        self.assertEqual(captured[0]["url"], parent["entries"][0]["url"])

    async def test_download_video_entry_index_is_checked(self):
        parent = self.store.save_item({"url": "https://example.org", "entries": [], "assets": [{"url": "https://example.org/qr.png", "kind": "png"}]})
        response = await self.client.post('/api/downloads', json={"item_id": parent["id"], "entry_index": 0}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 400)

    async def test_video_download_task_shows_source_page_instead_of_player_parameters(self):
        source = "https://www.douyin.com/video/1234567890123456789"
        media = "https://example.org/play?video_id=fixture"
        parent = self.store.save_item({"url": "https://www.douyin.com/", "entries": [{"url": source, "kind": "video"}], "assets": [{"url": media, "source_url": source, "kind": "video"}]})
        response = await self.client.post('/api/downloads', json={"item_id": parent["id"], "entry_index": 0}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["inputs"], [source])
        self.assertEqual(response.json()["request"]["url"], media)

    async def test_export_respects_library_kind_filter(self):
        self.store.save_item({"url": "https://example.org/page", "title": "Web", "kind": "web"})
        pdf = self.store.save_item({"url": "https://example.org/document.pdf", "title": "Document", "kind": "pdf"})
        response = await self.client.get('/api/export?format=json&kind=pdf', headers={"Authorization": "Bearer test-token"})
        self.assertEqual([item['id'] for item in response.json()], [pdf['id']])

    async def test_background_shutdown_requires_token(self):
        from types import SimpleNamespace
        self.app.state.server = SimpleNamespace(should_exit=False)
        self.assertEqual((await self.client.post('/api/shutdown')).status_code, 401)
        self.assertFalse(self.app.state.server.should_exit)
        response = await self.client.post('/api/shutdown', headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.app.state.server.should_exit)


if __name__ == "__main__":
    unittest.main()
