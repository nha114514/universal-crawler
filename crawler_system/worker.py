"""One bounded worker; stdout contains only the final JSON result."""
import contextlib
import json
import os
import sys


def own_process_job():
    if os.name != "nt":
        return None
    import win32api
    import win32job
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    return job


def execute(request):
    from .config import DATA
    from .network import Network
    from .router import collect, search, crawl_site
    operation = request.get("operation", "collect")
    if operation == "search":
        result = search(request["input"], request.get("mode", "auto"), request.get("max_results", 60),
                      region=request.get('region', 'all'), strategy=request.get('strategy', 'broad'),
                      platforms=request.get('platforms'), english_query=request.get('english_query', ''),
                      bilingual=request.get('bilingual', True), request_budget=request.get('request_budget', 24))
        from .resources import resource_only
        result['items'] = [resource_only(item) for item in result.get('items', [])]
        return result
    if operation == "crawl":
        return crawl_site(request["input"], request.get("depth", 1), request.get("max_pages", 20), request.get("render", "auto"), resources_only=True)
    if operation == "download":
        from .regions import classify_source
        from .storage import artifact_directory
        root = request.get('storage_root')
        region = request.get('source_region') or classify_source(request.get('input') or request['url'])
        directory = artifact_directory(root, region, '下载') if root else DATA / 'downloads'
        if request.get("asset_kind") == "media":
            from .video import download_video
            return {"download": download_video(request["url"], directory, request.get("max_mb", 0), headers=request.get('download_headers'))}
        if request.get("asset_kind") in {"video", "mp4", "webm", "mov"}:
            return {"download": Network().download(request["url"], directory, request.get("max_mb", 0),
                                                   headers=request.get("download_headers"), expected_media="video", filename=request.get("filename"))}
        if request.get('asset_kind') in {'audio', 'mp3', 'wav', 'm4a', 'ogg'}:
            return {'download': Network().download(request['url'], directory, request.get('max_mb', 0),
                                                   headers=request.get('download_headers'), expected_media='audio', filename=request.get('filename'))}
        return {"download": Network().download(request["url"], directory, request.get("max_mb", 0),
                                               headers=request.get('download_headers'), filename=request.get('filename'),
                                               expected_media='pdf' if request.get('asset_kind') == 'pdf' else None)}
    from .resources import collect_resources
    return collect_resources(request['input'], request.get('render', 'auto'))


def main():
    job = own_process_job()
    try:
        request = json.loads(sys.stdin.readline().lstrip("\ufeff"))
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request)
    except Exception as error:
        from .errors import CrawlerError
        result = {"error": error.as_dict() if isinstance(error, CrawlerError) else {"code": "WORKER_FAILED", "message": f"{type(error).__name__}: {str(error)[:500]}", "action": "Check tool status and target URL, then try again"}}
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
