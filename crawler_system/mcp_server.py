import asyncio
import logging
import time
from urllib.parse import unquote

import httpx
from mcp.server.mcpserver import MCPServer

from .service import ensure_service

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer("crawler-system", instructions="Personal learning and research collection for public websites, papers, videos and attachments. Calls installed Python libraries directly: Scrapling/Playwright with Edge, local Google/Bing/Bilibili protocol parsers, and yt-dlp for selected downloads. No browser extension or agent skill scripts are required. Desktop and MCP share a local library. Original files are downloaded only on request; no automatic body or caption extraction. Long operations return a job ID; inspect job_status for results and actual errors.")


async def request(method, path, **kwargs):
    url, secret = await asyncio.to_thread(ensure_service)
    async with httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + secret}, timeout=30, trust_env=False) as client:
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


async def wait_job(job_id, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = await request("GET", "/api/jobs/" + job_id)
        if job["state"] in {"completed", "partial", "failed", "cancelled", "interrupted"}:
            return job
        await asyncio.sleep(0.5)
    return await request("GET", "/api/jobs/" + job_id)


@mcp.tool()
async def collect(inputs: list[str], mode: str = "auto", max_results: int = 60, enrich: bool = False, render: str = "auto", region: str = 'all',
                  strategy: str = 'broad', platforms: list[str] | None = None, english_query: str = '',
                  bilingual: bool = True, enrich_limit: int = 0, request_budget: int = 24) -> dict:
    """Discover resource links from URLs/DOIs/keywords. No automatic body, PDF text or caption extraction. Legacy enrich parameters are ignored. Static resources appear first and dynamic pages are supplemented through Edge. region is by source site. Downloads require download_asset or download_batch; inspect job_status for actual failures."""
    return await request("POST", "/api/jobs", json={"inputs": inputs, "mode": mode, "max_results": max_results, "enrich": enrich, "render": render, 'region': region,
                         'strategy': strategy, 'platforms': platforms or [], 'english_query': english_query, 'bilingual': bilingual,
                         'enrich_limit': enrich_limit, 'request_budget': request_budget})


@mcp.tool()
async def platform_catalog(region: str = 'all', mode: str = 'auto') -> dict:
    """List domestic/foreign public discovery platforms, IDs, aliases/routes and last actual checks. Registration is not proof of live collection/download support. Use IDs in collect(platforms=[...])."""
    return await request('GET', '/api/platforms', params={'region': region, 'mode': mode})


@mcp.tool()
async def crawl_site(url: str, max_pages: int = 20, depth: int = 1, render: str = "auto") -> dict:
    """Follow a bounded set of same-domain public links, respecting robots.txt. Up to 50 pages, depth up to 2. Returns a shared-library job ID."""
    return await request("POST", "/api/jobs", json={"inputs": [url], "operation": "crawl", "mode": "web", "max_pages": max_pages, "depth": depth, "render": render})


@mcp.tool()
async def job_status(job_id: str) -> dict:
    """Read progress, actual per-input errors, item IDs and original-download results for a collection job."""
    return await request("GET", "/api/jobs/" + job_id)


@mcp.tool()
async def cancel_job(job_id: str) -> dict:
    """Cancel a collection job and terminate only that job's worker/browser processes."""
    return await request("POST", "/api/jobs/" + job_id + "/cancel")


@mcp.tool()
async def library_search(query: str = "", kind: str = "", limit: int = 30, region: str = 'all') -> dict:
    """Search the saved local library. region: all/domestic/foreign/unknown by source website, regardless of language. kind: web/paper/pdf/video/audio/feed/text/file."""
    return await request("GET", "/api/items", params={"q": query, "kind": kind, "limit": limit, 'region': region})


@mcp.tool()
async def get_item(item_id: str, max_chars: int = 30000) -> dict:
    """Read saved metadata, body, abstract, timed captions and attachment candidates. Output text is bounded, full data remains in the library."""
    item = await request("GET", "/api/items/" + item_id)
    maximum = max(100, min(300000, max_chars))
    item["response_truncated"] = False
    for field in ("content", "abstract", "subtitle_text"):
        text = item.get(field) or ""
        item["response_truncated"] |= len(text) > maximum
        item[field] = text[:maximum]
    item["subtitles"] = item.get("subtitles", [])[:500]
    return item


@mcp.tool()
async def download_asset(item_id: str, asset_index: int = 0, max_mb: int = 0, entry_index: int | None = None) -> dict:
    """Explicitly download a listed original PDF/video/file; max_mb=0 (default) means unlimited. Returns a job ID. Completed results include local path, size and SHA-256. No automatic original downloads."""
    return await request("POST", "/api/downloads", json={"item_id": item_id, "asset_index": asset_index, "max_mb": max_mb, 'entry_index': entry_index})


@mcp.tool()
async def download_batch(downloads: list[dict]) -> dict:
    """Download selected resources as one validated batch. Each entry has item_id and asset_index or entry_index, plus optional max_mb. At most 200 selections; duplicate resources are queued once. Returns per-file job IDs; no files are downloaded without this explicit selection."""
    return await request('POST', '/api/downloads/batch', json={'downloads': downloads})


@mcp.tool()
async def tool_status() -> dict:
    """Read installed capabilities, configured runtime paths and recent successful collectors. Installation/configuration does not prove platform login."""
    return await request("GET", "/api/status")


@mcp.tool()
async def open_login_browser(url: str = 'https://www.bilibili.com/') -> dict:
    """Open or reuse this system's direct Edge login window. Keep it open; render=session reuses its current browser context. No extension is required."""
    return await request('POST', '/api/browser/login', json={'url': url})


@mcp.tool()
async def set_storage_location(path: str) -> dict:
    """Choose an absolute writable directory for future material JSON, requested downloads and exports. Files are partitioned by source region. Old files and the shared index remain accessible at their old paths."""
    return await request('PUT', '/api/settings', json={'storage_root': path})


@mcp.tool()
async def set_source_region(url: str, region: str) -> dict:
    """Assign a SOURCE WEBSITE to domestic/foreign/unknown, or auto to restore built-in classification. Updates existing items and future discovery; never infers geography from text language."""
    return await request('PUT', '/api/source-region', json={'url': url, 'region': region})


@mcp.tool()
async def web_fetch(url: str) -> dict:
    """Discover public URL resource links in the shared library, without automatic text or caption extraction. Originals are downloaded only after selection."""
    job = await collect([url], enrich=False)
    result = await wait_job(job["id"])
    if result["item_ids"]:
        item = await get_item(result["item_ids"][0])
        return {"url": item["url"], "statusCode": item.get("http_status"),
                "body": {"kind": "resources", "title": item['title'], 'entries': item.get('entries', []), 'assets': item.get('assets', [])},
                "truncated": item.get("response_truncated", False), "item_id": item["id"], "job_id": job["id"], "warnings": item.get("warnings", [])}
    return {"url": url, "statusCode": 202 if result["state"] in {"running", "queued"} else 500, "job_id": job["id"], "state": result["state"], "errors": result["errors"]}


@mcp.tool()
async def web_search(queries: list[str], max_results: int = 8, region: str = 'all', strategy: str = 'broad',
                     platforms: list[str] | None = None, english_query: str = '', request_budget: int = 12) -> dict:
    """Discovery using local protocol parsers, installed libraries and platform prefixes. Returns real sources and per-query errors; stores discovered metadata."""
    if not 1 <= len(queries) <= 4:
        raise ValueError("Provide one to four queries")
    output = []
    for query in queries:
        job = await collect([query], mode="web", max_results=max_results, enrich=False, region=region,
                            strategy=strategy, platforms=platforms, english_query=english_query, request_budget=request_budget)
        result = await wait_job(job["id"])
        sources = []
        for item_id in result["item_ids"]:
            item = await get_item(item_id, 1500)
            sources.append({"url": item["url"], "title": item["title"], "snippet": item.get("abstract", ""), "item_id": item_id})
        output.append({"query": query, "sources": sources, "errors": result["errors"], "state": result["state"], "job_id": job["id"]})
    return {"results": output}


@mcp.tool()
async def export_library(format: str = "markdown", item_ids: list[str] | None = None, query: str = "", region: str = 'all') -> dict:
    """Export saved items to a local JSON/CSV/Markdown/BibTeX file, returning its absolute path."""
    url, secret = await asyncio.to_thread(ensure_service)
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        response = await client.get(url + "/api/export", params={"format": format, "ids": ",".join(item_ids or []), "q": query, 'region': region}, headers={"Authorization": "Bearer " + secret})
        response.raise_for_status()
    return {"path": unquote(response.headers['x-artifact-path']), "bytes": len(response.content), "format": format,
            'region': response.headers['x-artifact-region'], 'items': int(response.headers['x-artifact-count'])}


if __name__ == "__main__":
    mcp.run(transport="stdio")
