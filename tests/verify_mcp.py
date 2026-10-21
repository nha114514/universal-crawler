"""Live MCP acceptance: protocol + real collection + shared API/library reads."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import runtime_env
from crawler_system.service import ensure_service


def payload(result):
    data = getattr(result, "structured_content", None)
    if data is not None:
        return data
    for content in result.content:
        if getattr(content, "type", "") == "text":
            return json.loads(content.text)
    raise AssertionError("Tool did not return JSON")


async def main(live=False):
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": [], "live": live}
    params = StdioServerParameters(command=str(ROOT / ".venv/Scripts/python.exe"), args=["-m", "crawler_system.mcp_server"], cwd=str(ROOT), env=runtime_env())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=180) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            required = {"collect", "crawl_site", "job_status", "cancel_job", "library_search", "get_item", "download_asset", "web_search", "web_fetch", "tool_status", "export_library"}
            assert required <= set(names), names
            evidence["checks"].append({"name": "initialize_and_tools_list", "passed": True, "protocol": initialized.protocol_version, "tools": names})
            print("MCP initialize/tools/list PASS", flush=True)
            cases = [("web", "https://example.com", "auto")]
            if live:
                cases += [("doi", "10.1038/nphys1170", "paper"), ("pdf", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf", "auto"),
                          ("arxiv", "https://arxiv.org/abs/1706.03762", "paper"), ("video", "https://www.ted.com/talks/hans_rosling_the_best_stats_you_ve_ever_seen", "video"),
                          ("rendered_web", "https://quotes.toscrape.com/js/", "auto")]
            for name, value, mode in cases:
                job = payload(await session.call_tool("collect", {"inputs": [value], "mode": mode, "enrich": False}))
                print(f"{name}: submitted {job['id']}", flush=True)
                deadline = time.monotonic() + 180
                while True:
                    result = payload(await session.call_tool("job_status", {"job_id": job["id"]}))
                    if result["state"] not in {"queued", "running"} or time.monotonic() > deadline:
                        break
                    await asyncio.sleep(1)
                check = {"name": name, "input": value, "job_id": job["id"], "state": result["state"], "errors": result["errors"], "passed": bool(result["item_ids"])}
                if result["item_ids"]:
                    item = payload(await session.call_tool("get_item", {"item_id": result["item_ids"][0], "max_chars": 10000}))
                    check.update(item_id=item["id"], title=item["title"], kind=item["kind"], extractor=item["extractor"], text_status=item["text_status"],
                                 content_chars=len(item.get("content", "")), abstract_chars=len(item.get("abstract", "")), subtitle_chars=len(item.get("subtitle_text", "")),
                                 subtitle_status=item.get("subtitle_status"), warnings=item.get("warnings", []))
                    url, secret = ensure_service()
                    api = httpx.get(url + "/api/items/" + item["id"], headers={"Authorization": "Bearer " + secret}, trust_env=False).json()
                    assert api["id"] == item["id"] and api["url"] == item["url"]
                    check["shared_api_item_verified"] = True
                evidence["checks"].append(check)
                print(f"{name}: {result['state']} items={len(result['item_ids'])} errors={len(result['errors'])}", flush=True)
            library = payload(await session.call_tool("library_search", {"query": "Example Domain"}))
            assert library["items"]
            exported = payload(await session.call_tool("export_library", {"format": "markdown", "item_ids": [library["items"][0]["id"]]}))
            assert Path(exported["path"]).exists()
            evidence["checks"].append({"name": "library_search_and_export", "passed": True, "export_bytes": exported["bytes"]})
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    directory = ROOT / "evidence"
    directory.mkdir(exist_ok=True)
    (directory / ("mcp-live.json" if live else "mcp-protocol.json")).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [check["name"] for check in evidence["checks"] if not check["passed"]]
    if failed:
        raise AssertionError("MCP checks did not return evidence: " + ", ".join(failed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.live))
