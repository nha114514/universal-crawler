from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid

from .regions import classify_source, REGIONS
from .storage import write_material


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url):
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(query), ""))


def normalize_doi(doi):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(doi or "").strip(), flags=re.I).lower()


class Store:
    def __init__(self, path, storage_root=None, region_overrides=None):
        self.path = Path(path)
        self._storage_root = storage_root or self.path.parent
        self.region_overrides = region_overrides or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, doi TEXT, digest TEXT,
                    kind TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS item_url ON items(url);
                CREATE INDEX IF NOT EXISTS item_doi ON items(doi);
                CREATE INDEX IF NOT EXISTS item_digest ON items(digest);
                CREATE TABLE IF NOT EXISTS item_urls (url TEXT PRIMARY KEY, item_id TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(id UNINDEXED, title, body);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS downloads (id TEXT PRIMARY KEY, item_id TEXT,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(items)')}
            if 'source_region' not in columns:
                db.execute("ALTER TABLE items ADD COLUMN source_region TEXT NOT NULL DEFAULT 'unknown'")
            db.execute('CREATE INDEX IF NOT EXISTS item_region ON items(source_region)')
            for row in db.execute("SELECT id,url,payload,source_region FROM items"):
                payload = json.loads(row['payload'])
                region = classify_source(row['url'], self.source_overrides())
                if payload.get('source_region') != region or row['source_region'] != region:
                    payload['source_region'] = region
                    db.execute('UPDATE items SET source_region=?,payload=? WHERE id=?', (region, json.dumps(payload, ensure_ascii=False), row['id']))
                sources = [row["url"], *payload.get("source_urls", [])]
                aliases = [canonical_url(url) for url in sources if classify_source(url, self.source_overrides()) == region]
                db.executemany("INSERT OR IGNORE INTO item_urls VALUES(?,?)", [(url, row['id']) for url in aliases])
                db.execute('INSERT INTO item_urls VALUES(?,?) ON CONFLICT(url) DO UPDATE SET item_id=excluded.item_id', (canonical_url(row['url']), row['id']))

    def storage_root(self):
        return Path(self._storage_root() if callable(self._storage_root) else self._storage_root).resolve()

    def set_storage_root(self, root):
        self._storage_root = Path(root)

    def source_overrides(self):
        return self.region_overrides() if callable(self.region_overrides) else self.region_overrides

    def reclassify_sources(self):
        updated = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT id,url,payload,source_region FROM items').fetchall():
                region = classify_source(row['url'], self.source_overrides())
                if region == row['source_region']:
                    continue
                item = json.loads(row['payload'])
                item['source_region'] = region
                write_material(item, self.storage_root())
                db.execute('UPDATE items SET source_region=?,payload=? WHERE id=?', (region, json.dumps(item, ensure_ascii=False), row['id']))
                updated += 1
        return updated

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_item(self, item, storage_root=None):
        url = canonical_url(item["url"])
        region = classify_source(url, self.source_overrides())
        doi = normalize_doi(item.get("doi"))
        body = "\n".join(str(item.get(k) or "") for k in ("content", "abstract", "subtitle_text"))
        digest = hashlib.sha256(body.encode()).hexdigest() if len(body.strip()) > 100 else ""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM items WHERE (source_region=? AND id IN (SELECT item_id FROM item_urls WHERE url=?)) OR url=? OR (source_region=? AND ((doi<>'' AND doi=?) OR (digest<>'' AND digest=?))) LIMIT 1", (region, url, url, region, doi, digest)).fetchone()
            if not row:
                for source in item.get("source_urls", [])[:20]:
                    row = db.execute("SELECT * FROM items WHERE source_region=? AND (id IN (SELECT item_id FROM item_urls WHERE url=?) OR url=?) LIMIT 1", (region, canonical_url(source), canonical_url(source))).fetchone()
                    if row:
                        break
            saved = json.loads(row["payload"]) if row else {
                "id": uuid.uuid4().hex, "created_at": now(), "kind": "web", "content": "",
                "abstract": "", "subtitle_text": "", "subtitles": [], "assets": [],
                "authors": [], "metadata": {}, "warnings": [], "title": url,
            }
            sources = list(dict.fromkeys([*saved.get("source_urls", []), *([saved["url"]] if saved.get("url") else []), *[canonical_url(source) for source in item.get("source_urls", [])[:20]], url]))
            incoming = {k: v for k, v in item.items() if k not in {"id", "created_at"}}
            if incoming.get('metadata', {}).get('verification_page'):
                from .web import verification_page
                if verification_page(saved):
                    saved['content'] = ''
            if incoming.get('metadata', {}).get('login_page'):
                from .web import login_page
                if login_page(saved):
                    saved['content'] = ''
            if (incoming.get('extractor') == 'video-page-metadata' and not incoming.get('content')
                    and saved.get('extractor') == 'video-page-metadata' and saved.get('text_status') == 'metadata_only'):
                if saved.get('content'):
                    saved.setdefault('metadata', {})['page_text_unverified'] = saved['content']
                saved['content'] = ''
            # Earlier Bilibili snapshots placed descriptions in the body field.
            if (incoming.get('extractor') == 'bilibili-reconstruction' and saved.get('text_status') == 'metadata_only'
                    and incoming.get('abstract') and saved.get('content') == incoming['abstract'] and not incoming.get('content')):
                saved['content'] = ''
            # Older snapshots counted titles or homepage navigation as full text.
            # Preserve real content, but allow the corrected parser to clear that shell.
            legacy_shell = re.sub(r"\W+", "", saved.get("content", "")) == re.sub(r"\W+", "", saved.get("title", "")) or (
                incoming.get("metadata", {}).get("page_type") == "listing" and saved.get("text_status") == "full_text" and not saved.get("entries") and not saved.get("subtitle_text"))
            if incoming.get("retrieval_error") and not incoming.get("content") and legacy_shell:
                saved["content"] = ""
            quality = {"full_text": 3, "available": 3, "listing": 3, "subtitles": 2, "abstract": 1, "metadata_only": 0}
            old_quality = quality.get(saved.get("text_status"), 3 if saved.get("content") else 0)
            new_quality = quality.get(incoming.get("text_status"), 3 if incoming.get("content") else 0)
            retained = []
            if saved.get("content") and (not incoming.get("content") or old_quality > new_quality):
                incoming.pop("content", None)
                incoming["text_status"] = saved.get("text_status", "available")
                incoming["content_fetched_at"] = saved.get("content_fetched_at") or saved.get("fetched_at") or saved["created_at"]
                if saved.get("entries") and not incoming.get("entries"):
                    incoming.pop("entries", None)
                if saved.get('links') and not incoming.get('links'):
                    incoming.pop('links', None)
                retained.append("Full Text")
            if saved.get("subtitle_text") and not incoming.get("subtitle_text"):
                for key in ("subtitle_text", "subtitles", "subtitle_status", "subtitle_source", "subtitle_language"):
                    incoming.pop(key, None)
                if saved.get("text_status") == "subtitles":
                    incoming["text_status"] = "subtitles"
                incoming["subtitles_fetched_at"] = saved.get("subtitles_fetched_at") or saved.get("fetched_at") or saved["created_at"]
                retained.append("Subtitles")
            for key in ("abstract", "authors"):
                if saved.get(key) and not incoming.get(key):
                    incoming.pop(key, None)
            incoming["metadata"] = {**saved.get("metadata", {}), **incoming.get("metadata", {})}
            for field in ('collection_queries', 'search_origins'):
                incoming['metadata'][field] = list(saved.get('metadata', {}).get(field, []))
                for value in item.get('metadata', {}).get(field, []):
                    if value not in incoming['metadata'][field]:
                        incoming['metadata'][field].append(value)
            assets = {asset["url"]: asset for asset in [*saved.get("assets", []), *incoming.get("assets", [])] if asset.get("url")}
            media_kinds = {"media", "video", "audio", "mp4", "webm", "mov", "mp3", "wav", "m4a", "ogg"}
            incoming_urls = {asset["url"] for asset in incoming.get("assets", []) if asset.get("kind") in media_kinds}
            incoming["assets"] = sorted(assets.values(), key=lambda asset: 0 if asset["url"] in incoming_urls else 1 if asset.get("kind") in media_kinds else 2)
            if retained:
                incoming["warnings"] = list(dict.fromkeys([*incoming.get("warnings", []), "This refresh retains previously collected " + ", ".join(retained)]))
            saved.update(incoming)
            saved["source_urls"] = sources
            saved.update(url=url, doi=doi or saved.get("doi", ""), updated_at=now(), fetched_at=item.get("fetched_at", now()))
            saved['source_region'] = region
            if not saved.get("title"):
                saved["title"] = url
            saved.setdefault("extractor", "unknown")
            saved.setdefault("text_status", "available" if saved.get("content") else "metadata_only")
            full_body = "\n".join(str(saved.get(k) or "") for k in ("content", "abstract", "subtitle_text"))
            full_digest = hashlib.sha256(full_body.encode()).hexdigest() if len(full_body.strip()) > 100 else ""
            write_material(saved, storage_root or self.storage_root())
            db.execute("INSERT OR REPLACE INTO items(id,url,doi,digest,kind,title,created_at,updated_at,payload,source_region) VALUES(?,?,?,?,?,?,?,?,?,?)", (
                saved["id"], url, saved["doi"], full_digest, saved["kind"], saved["title"],
                saved["created_at"], saved["updated_at"], json.dumps(saved, ensure_ascii=False), region))
            db.execute("DELETE FROM item_search WHERE id=?", (saved["id"],))
            search_body = full_body + '\n' + '\n'.join(saved.get('metadata', {}).get('collection_queries', []))
            db.execute("INSERT INTO item_search VALUES(?,?,?)", (saved["id"], saved["title"], search_body))
            aliases = [source for source in sources if classify_source(source, self.source_overrides()) == region]
            db.executemany("INSERT OR IGNORE INTO item_urls VALUES(?,?)", [(source, saved['id']) for source in aliases])
            db.execute('INSERT INTO item_urls VALUES(?,?) ON CONFLICT(url) DO UPDATE SET item_id=excluded.item_id', (url, saved['id']))
        return saved

    def get_item(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError(item_id)
            return json.loads(row[0])

    def _filters(self, query='', kind='', region='all'):
        where, args = [], []
        if region and region != 'all':
            if region not in REGIONS:
                raise ValueError('Invalid source region')
            where.append('source_region=?')
            args.append(region)
        if kind and kind != "all":
            where.append("kind=?")
            args.append(kind)
        if query.strip():
            # Quoting protects FTS syntax; LIKE supports unsegmented Chinese phrases.
            phrase = '"' + query.strip().replace('"', '""') + '"'
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(id IN (SELECT id FROM item_search WHERE item_search MATCH ?) OR title LIKE ? ESCAPE '\\' OR payload LIKE ? ESCAPE '\\')")
            args.extend([phrase, "%" + escaped + "%", "%" + escaped + "%"])
        return (' WHERE ' + ' AND '.join(where) if where else ''), args

    def list_items(self, query="", kind="", limit=100, offset=0, region='all'):
        where, args = self._filters(query, kind, region)
        sql = "SELECT payload FROM items" + where
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(sql, [*args, min(500, max(1, limit)), max(0, offset)])]

    def count_items(self, query='', kind='', region='all'):
        where, args = self._filters(query, kind, region)
        with self.connect() as db:
            return db.execute('SELECT count(*) FROM items' + where, args).fetchone()[0]

    def region_counts(self, query='', kind=''):
        where, args = self._filters(query, kind)
        with self.connect() as db:
            counts = {row[0]: row[1] for row in db.execute('SELECT source_region,count(*) FROM items' + where + ' GROUP BY source_region', args)}
        return {region: counts.get(region, 0) for region in REGIONS}

    def create_job(self, request):
        job = {"id": uuid.uuid4().hex, "request": request, "state": "queued", "created_at": now(),
               "updated_at": now(), "progress": 0, "total": len(request.get("inputs", [])),
               "item_ids": [], "errors": [], "events": [], "downloads": []}
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?)", (job["id"], job["state"], job["created_at"], job["updated_at"], json.dumps(job, ensure_ascii=False)))
        return job

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            return json.loads(row[0])

    def update_job(self, job_id, **changes):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            job = json.loads(row[0])
            job.update(changes, updated_at=now())
            db.execute("UPDATE jobs SET state=?,updated_at=?,payload=? WHERE id=?", (job["state"], job["updated_at"], json.dumps(job, ensure_ascii=False), job_id))
        return job

    def list_jobs(self, limit=50):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def interrupt_pending(self):
        for job in self.list_jobs(10000):
            if job["state"] in {"queued", "running"}:
                self.update_job(job["id"], state="interrupted", message="Service restarted; you can resubmit the task")

    def save_download(self, item_id, result):
        record = {**result, "id": uuid.uuid4().hex, "item_id": item_id, "created_at": now()}
        with self.connect() as db:
            db.execute("INSERT INTO downloads VALUES(?,?,?,?)", (record["id"], item_id, record["created_at"], json.dumps(record, ensure_ascii=False)))
        return record

    def list_downloads(self, item_id=""):
        with self.connect() as db:
            query = "SELECT payload FROM downloads" + (" WHERE item_id=?" if item_id else "") + " ORDER BY created_at DESC"
            return [json.loads(row[0]) for row in db.execute(query, (item_id,) if item_id else ())]

    def get_download(self, download_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM downloads WHERE id=?", (download_id,)).fetchone()
            if not row:
                raise KeyError(download_id)
            return json.loads(row[0])
