"""Live regression for component DOI pollution in paper keyword results."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import runtime_env
from tests.verify_mcp import payload


async def main():
    config = tomllib.loads((Path.home() / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["crawler-system"]
    params = StdioServerParameters(command=config["command"], args=config["args"], env={**runtime_env(), **config.get("env", {})})
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "registered_command_verified": True, "query": "attention transformer", "items": []}
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=180) as session:
            await session.initialize()
            job = payload(await session.call_tool("collect", {"inputs": [evidence["query"]], "mode": "paper", "enrich": False, "max_results": 3}))
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                job = payload(await session.call_tool("job_status", {"job_id": job["id"]}))
                if job["state"] not in {"queued", "running"}:
                    break
                await asyncio.sleep(.5)
            evidence.update(job_id=job["id"], state=job["state"], errors=job["errors"])
            for item_id in job["item_ids"]:
                item = payload(await session.call_tool("get_item", {"item_id": item_id, "max_chars": 1000}))
                evidence["items"].append({"id": item_id, "title": item["title"], "doi": item.get("doi"), "extractor": item["extractor"], "crossref_type": item.get("metadata", {}).get("type")})
    evidence["passed"] = (evidence["state"] == "completed" and len(evidence["items"]) == 3
                          and {item["extractor"] for item in evidence["items"]} == {"arxiv-api", "crossref-api"}
                          and all(item["crossref_type"] != "component" for item in evidence["items"]))
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    (ROOT / "evidence/paper-search.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    assert evidence["passed"], "Paper keyword regression failed; inspect evidence/paper-search.json"


if __name__ == "__main__":
    asyncio.run(main())
