from contextlib import asynccontextmanager
import hmac
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import APPLICATION, DATA, settings, token, save_config
from .errors import CrawlerError
from .exports import export_items
from .jobs import JobManager
from .store import Store
from .regions import classify_source, source_host, REGION_LABELS
from .storage import artifact_directory, atomic_text, choose_folder, validate_storage_root

LibraryRegion = Literal['all', 'domestic', 'foreign', 'unknown']


class CollectRequest(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["auto", "web", "paper", "video"] = "auto"
    operation: Literal["collect", "crawl"] = "collect"
    render: Literal["auto", "always", "never", "session"] = "auto"
    max_results: int = Field(default=60, ge=1, le=200)
    enrich: bool = False
    strategy: Literal['broad', 'focused'] = 'broad'
    platforms: list[str] = Field(default_factory=list, max_length=120)
    english_query: str = Field(default='', max_length=1000)
    bilingual: bool = True
    enrich_limit: int = Field(default=0, ge=0, le=50)
    request_budget: int = Field(default=24, ge=2, le=48)
    depth: int = Field(default=1, ge=0, le=2)
    max_pages: int = Field(default=20, ge=1, le=50)
    region: Literal['all', 'domestic', 'foreign'] = 'all'

    @field_validator('platforms')
    @classmethod
    def platforms_valid(cls, values):
        from .platforms import validate_platforms
        return validate_platforms(values)

    @field_validator("inputs")
    @classmethod
    def inputs_valid(cls, values):
        if any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("Input cannot be empty and each item must be at most 2000 characters")
        return list(dict.fromkeys(value.strip() for value in values))


class DownloadRequest(BaseModel):
    item_id: str
    asset_index: int = Field(default=0, ge=0)
    entry_index: int | None = Field(default=None, ge=0)
    max_mb: int = Field(default=0, ge=0)


class BatchDownloadRequest(BaseModel):
    downloads: list[DownloadRequest] = Field(min_length=1, max_length=200)


class SourceRegionRequest(BaseModel):
    url: str = Field(max_length=2000)
    region: Literal['auto', 'domestic', 'foreign', 'unknown']


def item_summary(item):
    result = {k: v for k, v in item.items() if k not in {"content", "subtitles", "subtitle_text", "abstract", "links", "entries", "assets", "bibtex"}}
    result["preview"] = (item.get("abstract") or item.get("content") or item.get("subtitle_text") or "")[:220]
    result["asset_count"] = len(item.get("assets", []))
    return result


def create_app(store=None, manager=None, api_token=None):
    store = store or Store(DATA / "library.sqlite3", storage_root=lambda: settings()['storage_root'],
                           region_overrides=lambda: settings().get('source_region_overrides', {}))
    store.region_overrides = lambda: settings().get('source_region_overrides', {})
    own_manager = manager is None
    manager = manager or JobManager(store)
    api_token = api_token or token()

    @asynccontextmanager
    async def lifespan(app):
        store.interrupt_pending()
        yield
        if own_manager:
            manager.close()

    app = FastAPI(title="Universal Crawler", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.manager = store, manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + api_token):
                return JSONResponse({"detail": "Open the desktop application to connect to local service"}, status_code=401)
            origin = request.headers.get("origin")
            if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site access to local API is not permitted"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.exception_handler(CrawlerError)
    async def crawler_error(request, error):
        return JSONResponse({"detail": error.as_dict()}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"application": APPLICATION, "version": __version__, "data_directory": str(DATA), "pid": os.getpid()}

    @app.post("/api/shutdown")
    def shutdown():
        server = getattr(app.state, "server", None)
        if server is None:
            raise HTTPException(503, "This startup mode does not provide a background exit interface")
        server.should_exit = True
        return {"stopping": True}

    @app.get("/api/items")
    def list_items(q: str = "", kind: str = "", limit: int = 100, offset: int = 0, region: LibraryRegion = 'all'):
        return {"items": [item_summary(item) for item in store.list_items(q[:1000], kind, limit, offset, region)],
                "total": store.count_items(q[:1000], kind, region), 'region_counts': store.region_counts(q[:1000], kind)}

    @app.get("/api/items/{item_id}")
    def get_item(item_id: str):
        return store.get_item(item_id)

    @app.post("/api/jobs")
    def submit(request: CollectRequest):
        return manager.submit(request.model_dump())

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": store.list_jobs(limit=250)}

    @app.get('/api/platforms')
    def platforms(region: Literal['all', 'domestic', 'foreign'] = 'all', mode: Literal['auto', 'web', 'paper', 'video'] = 'auto'):
        from .platforms import catalog
        recent = DATA / 'platform-checks.json'
        checks = json.loads(recent.read_text(encoding='utf-8')) if recent.exists() else {}
        return {'platforms': catalog(region, mode), 'recent_checks': checks,
                'note': 'Catalog inclusion indicates public index search; availability of extraction and download is determined by actual checks'}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return store.get_job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return manager.cancel(job_id)

    def download_payload(request: DownloadRequest):
        item = store.get_item(request.item_id)
        assets = item.get("assets", [])
        if request.entry_index is not None:
            entries = item.get("entries", [])
            if request.entry_index >= len(entries) or entries[request.entry_index].get("kind") not in {"video", "audio", "web", "paper", "pdf", "file"}:
                raise HTTPException(400, "No such downloadable resource entry")
            entry = entries[request.entry_index]
            asset = next((asset for asset in assets if asset.get("kind") in {"media", "video", "audio", "mp4", "webm", "mov", "mp3", "wav", "m4a", "ogg"} and (asset.get("source_url") or asset["url"]) == entry["url"]),
                         {"url": entry["url"], "source_url": entry["url"], "name": entry.get("title"), "kind": "media"})
            if entry['kind'] not in {'video', 'audio'}:
                from urllib.parse import urlsplit
                from .parsers import FILE_EXTENSIONS, file_extension
                name = urlsplit(entry['url']).path.rsplit('/', 1)[-1]
                extension = file_extension(entry['url'])
                asset = {'url': entry['url'], 'name': name if extension in FILE_EXTENSIONS else (entry.get('title') or 'webpage') + '.html',
                         'kind': extension if extension in FILE_EXTENSIONS else 'html'}
        else:
            if request.asset_index >= len(assets):
                raise HTTPException(400, "No such attachment")
            asset = assets[request.asset_index]
        source = asset.get('source_url') or item['url']
        from .network import validate_url
        validate_url(asset['url'], resolve=False)
        return {"operation": "download", "inputs": [asset.get("source_url") or asset["url"]], "url": asset["url"], "asset_kind": asset.get("kind", "file"), "item_id": request.item_id, "max_mb": request.max_mb,
                               'source_region': classify_source(source, store.source_overrides()),
                               "filename": asset.get("name"), "download_headers": {"User-Agent": "Mozilla/5.0", "Referer": asset.get("source_url") or item["url"]}}

    @app.post('/api/downloads')
    def download(request: DownloadRequest):
        return manager.submit(download_payload(request))

    @app.post('/api/downloads/batch')
    def download_batch(request: BatchDownloadRequest):
        # Validate the entire selection before creating any download job.
        payloads = [download_payload(row) for row in request.downloads]
        unique = {(row['url'], row['asset_kind']): row for row in payloads}
        return {'jobs': [manager.submit(row) for row in unique.values()], 'requested': len(payloads), 'queued': len(unique)}

    @app.get("/api/downloads")
    def downloads(item_id: str = ""):
        return {"downloads": store.list_downloads(item_id)}

    @app.get("/api/downloads/{download_id}/file")
    def download_file(download_id: str):
        record = store.get_download(download_id)
        path = Path(record["path"]).resolve()
        allowed = (DATA / 'downloads').resolve()
        if record.get('storage_root') and record.get('source_region') in {'domestic', 'foreign', 'unknown'}:
            root = Path(record['storage_root']).resolve()
            directory = (root / REGION_LABELS[record['source_region']] / '下载').resolve()
            if directory.is_relative_to(root):
                allowed = directory
        if not path.is_relative_to(allowed) or not path.is_file():
            raise HTTPException(404, "Attachment file does not exist")
        return FileResponse(path, filename=record["filename"], media_type="application/octet-stream")

    @app.get("/api/export")
    def export(format: Literal["json", "csv", "markdown", "bibtex"] = "markdown", ids: str = "", q: str = "", kind: str = "", region: LibraryRegion = 'all'):
        selected = ids.split(",")[:200] if ids else []
        items = [store.get_item(item_id) for item_id in selected] if selected else store.list_items(q[:1000], kind, limit=500, region=region)
        if region != 'all':
            items = [item for item in items if item.get('source_region') == region]
        text, media_type, extension = export_items(items, format)
        regions = {item.get('source_region', 'unknown') for item in items}
        partition = next(iter(regions)) if len(regions) == 1 else region
        path = artifact_directory(store.storage_root(), partition, '导出') / f'library-{uuid.uuid4().hex}.{extension}'
        atomic_text(path, text)
        return Response(text, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{path.name}"',
                        'X-Artifact-Path': quote(str(path)), 'X-Artifact-Region': partition, 'X-Artifact-Count': str(len(items))})

    @app.get("/api/status")
    def status():
        config = settings()
        tools = []
        for name, description in [("scrapling", "Web resource scanner"), ("playwright", "Direct Edge browser control, no extension required"), ("yt-dlp", "On-demand video downloader"), ("mcp", "MCP interface")]:
            try:
                installed = version(name)
            except PackageNotFoundError:
                installed = None
            tools.append({"name": name, "version": installed, "available": installed is not None, "description": description})
        latest = store.list_items(limit=100)
        return {"application": APPLICATION, "version": __version__, "tools": tools, "items": store.count_items(),
                "browser_configured": Path(config["browser_executable"]).is_file(),
                "browser_profile_configured": Path(config['browser_profile']).is_dir(),
                "platform_note": "Directly controls Edge without browser extensions. The dedicated login window can stay open, and collection will reuse the current session.",
                "recent_extractors": list(dict.fromkeys(item.get("extractor") for item in latest if item.get("extractor"))), "settings": config,
                'storage': {'root': str(store.storage_root()), 'index': str(store.path.resolve()),
                            'regions': {key: str(store.storage_root() / label) for key, label in REGION_LABELS.items()},
                            'region_counts': store.region_counts()}}

    @app.post('/api/storage/choose-folder')
    def folder_picker():
        return {'path': choose_folder(str(store.storage_root()))}

    @app.post('/api/browser/login')
    def browser_login(request: dict):
        from .browser_session import open_login
        return open_login(request.get('url', 'https://www.bilibili.com/'))

    @app.put('/api/source-region')
    def source_region(request: SourceRegionRequest):
        host = source_host(request.url)
        if not request.url.startswith(('https://', 'http://')) or not host:
            raise HTTPException(400, 'Please enter the URL of the source page')
        config = settings()
        overrides = dict(config.get('source_region_overrides', {}))
        if request.region == 'auto':
            overrides.pop(host, None)
        else:
            overrides[host] = request.region
        config['source_region_overrides'] = overrides
        save_config(config)
        changed = store.reclassify_sources()
        return {'saved': True, 'host': host, 'region': classify_source(request.url, overrides), 'updated_items': changed}

    @app.put("/api/settings")
    def save_settings(values: dict):
        config = settings()
        storage_root = None
        if 'storage_root' in values:
            storage_root = validate_storage_root(values['storage_root'])
            config['storage_root'] = str(storage_root)
        for key in ("browser_cache", "browser_executable", "browser_profile"):
            if key in values:
                if not isinstance(values[key], str) or not values[key].strip():
                    raise HTTPException(400, "Specified path is invalid")
                if key == 'browser_executable' and not Path(values[key]).is_file():
                    raise HTTPException(400, "Browser executable does not exist")
                if key != 'browser_executable':
                    Path(values[key]).mkdir(parents=True, exist_ok=True)
                config[key] = values[key]
        for key, low, high in [("domain_interval", 1, 30), ("max_download_mb", 0, None)]:
            if key in values:
                if not isinstance(values[key], int) or values[key] < low or (high is not None and values[key] > high):
                    raise HTTPException(400, "Setting value is out of allowed range")
                config[key] = values[key]
        save_config(config)
        if storage_root:
            store.set_storage_root(storage_root)
        return {"saved": True, 'storage_root': str(store.storage_root())}

    static = Path(__file__).parent / "static"
    if static.exists():
        app.mount("/", StaticFiles(directory=static, html=True), name="desktop")
    return app


if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=settings()["port"])
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
