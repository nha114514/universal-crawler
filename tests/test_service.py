"""Regression: a short-lived MCP connection must not own the shared API."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from crawler_system.config import ROOT, runtime_env, python_command


@unittest.skipUnless(os.name == "nt", "Windows process lifetime regression")
class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_survives_mcp_session_exit_and_is_reused(self):
        with tempfile.TemporaryDirectory(prefix="crawler-service-test-") as directory:
            env = {**runtime_env(), "CRAWLER_DATA_DIR": directory}
            params = StdioServerParameters(command=python_command(), args=[str(ROOT / "run_mcp.py")], env=env)
            lease = None
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
                        await session.initialize()
                        result = await session.call_tool("tool_status", {})
                        self.assertFalse(result.is_error, result)
                        lease = json.loads((Path(directory) / "service.json").read_text())
                await asyncio.sleep(0.5)
                async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                    response = await client.get(lease["url"] + "/api/health")
                    self.assertEqual(Path(response.json()["data_directory"]), Path(directory))
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
                        await session.initialize()
                        result = await session.call_tool("tool_status", {})
                        self.assertFalse(result.is_error, result)
                        reused = json.loads((Path(directory) / "service.json").read_text())
                        self.assertEqual(reused["pid"], lease["pid"])
            finally:
                if lease:
                    try:
                        response = httpx.get(lease["url"] + "/api/health", trust_env=False, timeout=1)
                        if Path(response.json()["data_directory"]) == Path(directory):
                            secret = (Path(directory) / "api-token").read_text().strip()
                            stopped = httpx.post(lease["url"] + "/api/shutdown", headers={"Authorization": "Bearer " + secret}, trust_env=False, timeout=3)
                            self.assertEqual(stopped.status_code, 200)
                            for _ in range(30):
                                await asyncio.sleep(0.1)
                                try:
                                    httpx.get(lease["url"] + "/api/health", trust_env=False, timeout=1)
                                except httpx.RequestError:
                                    break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.3)
