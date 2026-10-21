from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import logging
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .config import DATA, ROOT, python_command, runtime_env, settings
from .errors import CrawlerError
from .router import classify
from .processes import own_worker, stop_worker

TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}


@contextmanager
def worker_directory(work):
    directory = Path(tempfile.mkdtemp(dir=work, prefix='worker-')).resolve()
    if not directory.is_relative_to(Path(work).resolve()):
        raise CrawlerError('INVALID_PATH', 'Worker temporary directory out of bounds')
    try:
        yield str(directory)
    finally:
        # Windows may briefly keep diagnostic handles after a job has stopped.
        # Retry only this owned directory; retain the original timeout/cancel error.
        deadline = time.monotonic() + 3
        while directory.exists():
            try:
                shutil.rmtree(directory)
            except PermissionError:
                if time.monotonic() >= deadline:
                    logging.warning('Owned worker temporary cleanup deferred: %s', directory)
                    break
                time.sleep(0.05)


def run_worker(request, cancel):
    work = DATA / "work"
    work.mkdir(parents=True, exist_ok=True)
    config = settings()
    timeout = config["worker_timeout"]
    if request.get('operation', 'collect') == 'collect':
        timeout = min(timeout, 40)
    if request.get("operation") == "download":
        # A full-length film can take longer than 15 minutes; users can still cancel.
        timeout = config["download_timeout"]
    elif request.get("operation") == "crawl":
        timeout = min(900, 60 + request.get("max_pages", 20) * 30)
    elif request.get('operation') == 'search' and request.get('strategy', 'broad') == 'broad':
        timeout = min(900, 120 + request.get('request_budget', 24) * 30)
    with worker_directory(work) as directory:
        output, log = Path(directory) / "result.json", Path(directory) / "stderr.log"
        with output.open("wb") as stdout, log.open("wb") as stderr:
            process = subprocess.Popen([python_command(), "-m", "crawler_system.worker"], cwd=ROOT,
                                       env=runtime_env(), stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | (4 if os.name == "nt" else 0))
            job = own_worker(process) if os.name == "nt" else None
            try:
                process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                process.stdin.close()
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if cancel.wait(0.1):
                        raise CrawlerError("CANCELLED", "Task cancelled", "You can resubmit the task")
                    if time.monotonic() > deadline:
                        raise CrawlerError("TIMEOUT", "Collection timed out", "Reduce collection scope or try again later")
                    if output.stat().st_size > 80 * 1024 * 1024:
                        raise CrawlerError("OUTPUT_LIMIT", "Returned data exceeds single-task limit", "Reduce number of pages")
            finally:
                stop_worker(process, job)
        if process.returncode != 0:
            raise CrawlerError("WORKER_EXITED", "Worker process exited abnormally", "Check Python environment and browser dependencies")
        try:
            result = json.loads(output.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise CrawlerError("WORKER_OUTPUT", "Worker process returned no valid results", "Check local tool environment")
        if result.get("error"):
            raise CrawlerError(**result["error"])
        return result


class JobManager:
    def __init__(self, store, runner=None):
        self.store = store
        self.runner = runner or run_worker
        self.pool = ThreadPoolExecutor(max_workers=settings()["concurrency"], thread_name_prefix="collection")
        self.events = {}
        self.lock = threading.Lock()
        self.host_times = {}
        self.host_locks = {}

    def _call(self, request, cancel):
        host = urlsplit(request.get("input", "")).hostname or "discovery:" + request.get("mode", "auto")
        with self.lock:
            gate = self.host_locks.setdefault(host, threading.Lock())
        while not gate.acquire(timeout=0.1):
            if cancel.is_set():
                raise CrawlerError("CANCELLED", "Task cancelled")
        try:
            delay = settings()["domain_interval"] - (time.monotonic() - self.host_times.get(host, 0))
            if delay > 0 and cancel.wait(delay):
                raise CrawlerError("CANCELLED", "Task cancelled")
            if cancel.is_set():
                raise CrawlerError("CANCELLED", "Task cancelled")
            self.host_times[host] = time.monotonic()
            return self.runner(request, cancel)
        finally:
            gate.release()

    def submit(self, request):
        # A later setting change must not redirect files from an active job.
        request = {**request, 'storage_root': str(self.store.storage_root())}
        job = self.store.create_job(request)
        cancel = threading.Event()
        with self.lock:
            self.events[job["id"]] = cancel
        self.pool.submit(self._run, job["id"], request, cancel)
        return job

    def _run(self, job_id, request, cancel):
        item_ids, errors, downloads, events = [], [], [], []
        try:
            if cancel.is_set():
                return
            self.store.update_job(job_id, state="running")
            for index, value in enumerate(request.get("inputs", [])):
                if cancel.is_set():
                    break
                operation = request.get("operation", "collect")
                if operation == "collect" and classify(value) == "query":
                    operation = "search"
                payload = {**request, "operation": operation, "input": value}
                self.store.update_job(job_id, message=f"Processing: {value[:160]}")
                try:
                    result = self._call(payload, cancel)
                    if result.get("download"):
                        downloads.append(self.store.save_download(request.get("item_id", ""), {**result["download"], 'storage_root': request['storage_root'], 'source_region': request.get('source_region', 'unknown')}))
                    candidates = result.get("items", [])
                    candidate_ids = {}
                    for item in candidates:
                        if operation != "search":
                            item["enrichment_error"] = item.get("retrieval_error")
                        saved = self.store.save_item(item, request['storage_root'])
                        candidate_ids[item["url"]] = saved["id"]
                        if saved["id"] not in item_ids:
                            item_ids.append(saved["id"])
                    errors.extend(result.get("errors", []))
                    self.store.update_job(job_id, item_ids=item_ids, errors=errors, downloads=downloads)
                    if operation == 'collect' and result.get('needs_browser') and not cancel.is_set():
                        self.store.update_job(job_id, message='Static resources displayed; identifying resources in dynamic pages' if item_ids else 'Identifying resources in dynamic pages')
                        next_render = 'session'
                        try:
                            dynamic = self._call({**payload, 'render': next_render, 'resource_stage': 'browser'}, cancel)
                        except CrawlerError as error:
                            if next_render != 'session' or error.code in {'PRIVATE_URL', 'INVALID_URL', 'SIZE_LIMIT', 'CANCELLED'}:
                                raise
                            dynamic = self._call({**payload, 'render': 'always', 'resource_stage': 'browser'}, cancel)
                            for item in dynamic.get('items', []):
                                item.setdefault('metadata', {})['session_attempt_error'] = error.as_dict()
                        for item in dynamic.get('items', []):
                            if result.get('static_attempt_error'):
                                item.setdefault('metadata', {})['static_attempt_error'] = result['static_attempt_error']
                            saved = self.store.save_item(item, request['storage_root'])
                            if saved['id'] not in item_ids:
                                item_ids.append(saved['id'])
                        errors.extend(dynamic.get('errors', []))
                        self.store.update_job(job_id, item_ids=item_ids, errors=errors)
                    events.append({"input": value, "operation": operation, "found": len(candidates), 'scope': result.get('scope'),
                                   'discovery': result.get('discovery'),
                                   'enrich_budget': 0})
                except CrawlerError as error:
                    if error.code == "CANCELLED":
                        cancel.set()
                        break
                    errors.append({"input": value, **error.as_dict()})
                self.store.update_job(job_id, progress=index + 1, item_ids=item_ids, errors=errors, events=events, downloads=downloads)
            state = "cancelled" if cancel.is_set() else "partial" if errors and (item_ids or downloads) else "failed" if errors or not (item_ids or downloads) else "completed"
            def quality(item):
                if item.get("content") and item.get("text_status") in {"full_text", "available", "partial", "listing"}:
                    return 3
                if item.get("subtitle_text"):
                    return 2
                return 1 if item.get("abstract") else 0
            saved_items = {item_id: self.store.get_item(item_id) for item_id in item_ids}
            qualities = {item_id: quality(item) for item_id, item in saved_items.items()}
            readable_items = sum(value >= 2 for value in qualities.values())
            listing_items = sum(qualities[item_id] >= 2 and (item.get("text_status") == "listing" or (bool(item.get("entries")) and item.get("metadata", {}).get("page_type") == "listing")) for item_id, item in saved_items.items())
            if any(event["operation"] == "search" for event in events):
                item_ids.sort(key=lambda item_id: qualities[item_id], reverse=True)
            message = (f"Saved {len(item_ids)} item(s), {readable_items - listing_items} with full text/subtitles" + (f", {listing_items} with content list" if listing_items else "")
                       if item_ids else "Processing finished")
            if saved_items and all(item.get('metadata', {}).get('resources_only') for item in saved_items.values()):
                resource_count = sum(len(item.get('assets', [])) + len(item.get('entries', [])) for item in saved_items.values())
                message = f'Identified {resource_count} resource(s), download on demand'
            self.store.update_job(job_id, state=state, item_ids=item_ids, errors=errors, events=events, downloads=downloads,
                                  readable_items=readable_items, listing_items=listing_items, message="Task cancelled" if state == "cancelled" else message)
        except Exception as error:
            errors.append({"code": "JOB_FAILED", "message": f"{type(error).__name__}: {str(error)[:300]}", "action": "Check local service and try again"})
            self.store.update_job(job_id, state="cancelled" if cancel.is_set() else "failed", errors=errors)
        finally:
            with self.lock:
                self.events.pop(job_id, None)

    def cancel(self, job_id):
        job = self.store.get_job(job_id)
        if job["state"] in TERMINAL:
            return job
        with self.lock:
            event = self.events.get(job_id)
            if event:
                event.set()
        return self.store.update_job(job_id, state="cancelled", message="Terminating collection process for this task")

    def wait(self, job_id, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.store.get_job(job_id)
            if job["state"] in TERMINAL:
                return job
            time.sleep(0.05)
        return self.store.get_job(job_id)

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
