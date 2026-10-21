from email.message import Message
from io import BytesIO
import html
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
from urllib.parse import urlsplit
import urllib.request

import yt_dlp
from yt_dlp.networking import Response as YtdlpResponse
from yt_dlp.networking.exceptions import HTTPError, TransportError

from .errors import CrawlerError
from .network import Network, validate_url, safe_filename
from .config import settings

MEDIA_CHUNK_BYTES = 10 * 1024 * 1024


class SafeYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(self, options=None, network=None):
        self.network = network or Network()
        super().__init__(options)

    def urlopen(self, request):
        if isinstance(request, str):
            url, method, data, headers = request, "GET", None, {}
        else:
            url = getattr(request, "url", None) or request.full_url
            method = getattr(request, "method", None) or (request.get_method() if hasattr(request, "get_method") else "GET")
            data, headers = request.data, dict(request.headers)
        if url.startswith("data:"):
            return super().urlopen(request)
        # Requests bypass yt-dlp's own handlers, so carry its cookie jar by hand;
        # Bilibili's API answers 403 without the cookies its page just set.
        if not any(key.lower() == "cookie" for key in headers) and (cookie := self.cookiejar.get_cookie_header(url)):
            headers = {**headers, "Cookie": cookie}
        suffix = self.params.get('_iqiyi_segment_suffix')
        parsed = urlsplit(url)
        if suffix and parsed.hostname == 'data.video.iqiyi.com' and parsed.path.lower().endswith(('.ts', '.265ts', '.m4s')):
            # The native player appends this server-issued suffix verbatim.
            # Re-encoding the same values invalidates the dispatch signature.
            url += ('&' if parsed.query else '?') + suffix
        try:
            # Extractors set their own headers; Scrapling's stealth mode would add a
            # Google Referer, which Bilibili's API rejects with HTTP 403.
            raw = self.network.fetch(url, method=method, data=data, headers=headers, allow_errors=True, stealth=False)
        except CrawlerError as error:
            raise TransportError(str(error))
        if set_cookie := raw.headers.get("set-cookie"):
            # Scrapling joins repeated Set-Cookie headers with ", "; split before the next name=.
            message = Message()
            for value in re.split(r",\s*(?=[^;,\s=]+=)", set_cookie):
                message["Set-Cookie"] = value
            self.cookiejar.extract_cookies(SimpleNamespace(info=lambda: message), urllib.request.Request(raw.url))
        if self.params.get('_iqiyi_clear_only') and (re.search(rb'#EXT-X-(?:SESSION-)?KEY:[^\r\n]*METHOD=(?!NONE)[A-Z0-9-]+', raw.body) or b'ContentProtection' in raw.body):
            raise CrawlerError('VIDEO_DRM_PROTECTED', 'Media manifest contains DRM encryption, original video not downloaded', 'Use playback or offline caching provided by the platform')
        response = YtdlpResponse(BytesIO(raw.body), raw.url, raw.headers, status=raw.status)
        if raw.status >= 400:
            raise HTTPError(response)
        return response

    def process_info(self, info_dict):
        if chunk := self.params.get('_media_chunk_bytes'):
            # urlopen holds each response in memory, so fetch single-file formats in
            # Range pieces. HLS/DASH segments are already small and stay unchanged.
            for fmt in [info_dict, *(info_dict.get('requested_formats') or [])]:
                if fmt.get('protocol') in {'http', 'https'}:
                    fmt['downloader_options'] = {'http_chunk_size': chunk, **(fmt.get('downloader_options') or {})}
        return super().process_info(info_dict)


def options():
    node = shutil.which("node")
    return {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True, "noplaylist": True,
            "socket_timeout": 25, "retries": 1, "extractor_retries": 1,
            "js_runtimes": {"node": {"path": node}} if node else {}}


def seconds(value):
    parts = value.replace(",", ".").split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def parse_subtitles(data, extension):
    rows = []
    if extension == "json3":
        for event in json.loads(data).get("events", []):
            text = " ".join("".join(part.get("utf8", "") for part in event.get("segs", [])).split())
            if text:
                start = event.get("tStartMs", 0) / 1000
                rows.append({"start": start, "end": start + event.get("dDurationMs", 0) / 1000, "text": text})
    else:
        text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        for match in re.finditer(r"([\d:.,]+)\s*-->\s*([\d:.,]+)[^\n]*\n(.*?)(?=\n\n|\Z)", text, re.S):
            body = html.unescape(re.sub(r"<[^>]+>", "", match[3]))
            body = " ".join(body.split())
            if body:
                rows.append({"start": seconds(match[1]), "end": seconds(match[2]), "text": body})
    return [row for index, row in enumerate(rows) if not index or row["text"] != rows[index-1]["text"]][:20000]


def video_item(info, requested_url=""):
    url = info.get("webpage_url") or info.get("original_url") or info.get("url") or requested_url
    if not url.startswith(("http://", "https://")):
        url = "https://www.youtube.com/watch?v=" + str(info.get("id", ""))
    return {"kind": "video" if info.get("vcodec") != "none" else "audio", "url": url,
            "title": info.get("title") or url, "authors": [info.get("uploader") or info.get("channel") or ""],
            "content": info.get("description") or "", "published": info.get("upload_date") or "",
            "metadata": {"duration": info.get("duration"), "extractor": info.get("extractor_key") or info.get("extractor"), "view_count": info.get("view_count"), "thumbnail": info.get("thumbnail")},
            "extractor": "yt-dlp", "subtitles": [], "subtitle_text": "", "subtitle_status": "missing",
            "text_status": "metadata_only", "assets": [{"url": url, "name": info.get('title') or 'Original Media', "kind": "media"}], "warnings": []}


def fallback_video(url, network, reason):
    from .web import collect_url
    public_douyin = (urlsplit(url).hostname or "").removeprefix("www.") == "douyin.com"
    page = collect_url(url, "web", network, render="always" if public_douyin else "auto")
    if page.get("kind") == "video" and any(asset.get("kind") == "video" for asset in page.get("assets", [])):
        page["warnings"].append("Dedicated extractor did not obtain details, using public media data returned by browser; download original video on demand")
        return page
    objects = page.get("metadata", {}).get("video_objects", [])
    if not objects and not page.get("metadata", {}).get("open_graph_type", "").startswith("video"):
        raise CrawlerError("VIDEO_UNAVAILABLE", "Media information for this page cannot be parsed at this time", "Use auto mode to collect webpage data, or check site compatibility")
    data = objects[0] if objects else {}
    if page.get('content'):
        page['metadata']['page_text_unverified'] = page['content']
    page['content'] = ''
    page.update(kind="video", title=html.unescape(data.get("name") or page["title"]), extractor="video-page-metadata", subtitle_text=data.get("transcript") if isinstance(data.get("transcript"), str) else "", subtitles=[], subtitle_source="page")
    page["subtitle_status"] = "available" if page["subtitle_text"] else "unchecked"
    page["text_status"] = "subtitles" if page["subtitle_text"] else "metadata_only"
    raw_duration = data.get("duration")
    if isinstance(raw_duration, str):
        components = re.findall(r"([0-9.]+)([HMS])", raw_duration)
        duration = sum(float(value) * {"H":3600,"M":60,"S":1}[unit] for value,unit in components) if components else None
    else:
        duration = raw_duration
    page["metadata"].update(duration=duration, video_adapter_warning=reason[:400])
    for metadata in page["metadata"].get("video_objects", []):
        metadata.pop("transcript", None)
    if isinstance(data.get("contentUrl"), str) and data["contentUrl"].startswith(("https://", "http://")):
        page["assets"].insert(0, {"url": data["contentUrl"], "name": "Original Media File", "kind": "media"})
    else:
        page["assets"].insert(0, {"url": url, "name": "Original Video", "kind": "media"})
    page["warnings"].append("Dedicated video extractor incomplete, saved media metadata provided by page; available subtitles and files depend on site availability")
    return page


def collect_video(url, network=None):
    validate_url(url)
    network = network or Network()
    direct_path = urlsplit(url).path.lower()
    if direct_path.endswith((".mp4", ".webm", ".mp3", ".wav", ".m4a", ".ogg", ".mov")):
        response = network.fetch(url, method="HEAD")
        item = video_item({"webpage_url": response.url, "title": direct_path.rsplit("/", 1)[-1], "vcodec": "none" if direct_path.endswith((".mp3", ".wav", ".m4a")) else "unknown"})
        item["metadata"].update(content_type=response.headers.get("content-type", ""), bytes=int(response.headers.get("content-length", "0")) or None)
        item["assets"][0]["kind"] = direct_path.rsplit(".", 1)[-1]
        item["warnings"].append("Original media link does not provide subtitles; metadata saved, download original file on demand")
        return item
    try:
        with SafeYoutubeDL(options(), network) as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get("entries") is not None:
            entries = [entry for entry in info["entries"] if entry][:20]
            return {"kind": "playlist", "url": url, "title": info.get("title") or url, "content": "\n".join(entry.get("title") or "" for entry in entries),
                    "links": [{"url": entry.get("webpage_url") or entry.get("url", ""), "title": entry.get("title", "")} for entry in entries],
                    "assets": [], "metadata": {"listed_entries": len(entries)}, "extractor": "yt-dlp", "text_status": "metadata_only", "warnings": ["Playlist candidates listed, full video download/collection not performed automatically"]}
        item = video_item(info, url)
        languages = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "en", "en-US", "en-GB", "ja"]
        selected = None
        for source, pool in [("human", info.get("subtitles") or {}), ("automatic", info.get("automatic_captions") or {})]:
            if pool:
                language = next((lang for lang in languages if lang in pool), next(iter(pool)))
                formats = pool[language]
                selected = (source, language, next((fmt for ext in ("json3", "vtt", "srt") for fmt in formats if fmt.get("ext") == ext), formats[0])) if formats else None
                if selected:
                    break
        if selected:
            source, language, fmt = selected
            try:
                raw = network.fetch(fmt["url"]).body
                rows = parse_subtitles(raw, fmt.get("ext", "vtt"))
                item.update(subtitles=rows, subtitle_language=language, subtitle_source=source,
                            subtitle_status="available" if rows else "empty", subtitle_text="\n".join(f"[{row['start']:.1f}s] {row['text']}" for row in rows),
                            text_status="subtitles" if rows else "metadata_only")
            except Exception as error:
                item["subtitle_status"] = "failed"
                item["warnings"].append(f"Failed to obtain subtitles: {error}")
        else:
            item["warnings"].append("No subtitles available for this video; metadata saved")
        return item
    except CrawlerError:
        raise
    except Exception as error:
        message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))[:600]
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        if host in {"bilibili.com", "b23.tv"}:
            try:
                from .bilibili import collect_bilibili
                return collect_bilibili(url)
            except Exception:
                pass
        needs_login = any(word in message.lower() for word in ("sign in", "login", "cookies", "private", "members-only", "age-restricted", "bot"))
        if not needs_login or host == "douyin.com":
            try:
                return fallback_video(url, network, message)
            except Exception:
                pass
        raise CrawlerError("VIDEO_LOGIN_REQUIRED" if needs_login else "VIDEO_UNAVAILABLE", message, "Log in or complete verification on corresponding platform and retry" if needs_login else "Check URL, network, or site compatibility")


def search_video(query, limit=5):
    with SafeYoutubeDL({**options(), "extract_flat": True}) as ydl:
        result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return {"items": [video_item(info) for info in result.get("entries", []) if info], "errors": []}


def download_error(url, error):
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))[:300]
    lowered = message.lower()
    host = (urlsplit(url).hostname or '').removeprefix('www.')
    if host == 'iqiyi.com' and "can't find any video" in lowered:
        return CrawlerError('VIDEO_EXTRACTOR_INCOMPATIBLE', 'iQIYI player incompatible with current extractor, original video not obtained; this error does not indicate VIP status was verified', 'Retain page resources and wait for updated player adapter; VIP and encryption status require standard session verification')
    if any(marker in lowered for marker in ('drm protected', 'drm-protected')):
        return CrawlerError('VIDEO_DRM_PROTECTED', 'Extractor explicitly reports DRM protection, original video not saved', 'Use playback or offline caching provided by platform')
    if any(marker in lowered for marker in ('members-only', 'premium subscription required')):
        return CrawlerError('VIDEO_PERMISSION_REQUIRED', 'Extractor explicitly reports VIP permission required, original video not saved', 'Log in with an authorized account; current extraction request did not confirm required privileges')
    if host == 'iqiyi.com':
        return CrawlerError('VIDEO_UNAVAILABLE', 'iQIYI media download failed, complete original video not saved', 'Verify dedicated Edge session plays successfully and check network before retrying')
    return CrawlerError('VIDEO_UNAVAILABLE', 'Platform did not return downloadable video stream: ' + message, 'Confirm session, permissions, or platform support and try again')


def download_video(url, directory, max_mb=0, headers=None):
    validate_url(url)
    if urlsplit(url).path.lower().endswith((".mp4", ".webm", ".mp3", ".wav", ".m4a", ".ogg", ".mov")):
        kind = 'audio' if urlsplit(url).path.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg')) else 'video'
        return Network().download(url, directory, max_mb, headers=headers, expected_media=kind)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if host in {"bilibili.com", "b23.tv"}:
        from .bilibili import download_bilibili
        return download_bilibili(url, directory, max_mb)
    iqiyi_info = None
    if host == 'iqiyi.com' and re.fullmatch(r'/[vw]_[0-9a-z]+\.html', urlsplit(url).path, re.I):
        from .iqiyi import playback_info
        network = Network()
        network.config['request_timeout'] = min(10, network.config['request_timeout'])
        iqiyi_info = playback_info(url, network, use_session=True, max_bytes=max_mb * 1024 * 1024 if max_mb else None)
    import tempfile
    import hashlib
    limit = max_mb * 1024 * 1024 if max_mb else None
    received = {}
    def progress(event):
        received[event.get('filename', '')] = event.get('downloaded_bytes') or 0
        if limit is not None and sum(received.values()) > limit:
            raise CrawlerError("SIZE_LIMIT", f"Video exceeds {max_mb} MB", "Increase download limit and retry")
    with tempfile.TemporaryDirectory(dir=directory, prefix="video-") as temp:
        config = {**options(), "skip_download": False, "format": "best[protocol=https]/best[protocol=http]/best[protocol=m3u8_native]/bestvideo+bestaudio/best",
                  "outtmpl": str(Path(temp) / "%(id)s.%(ext)s"), "max_filesize": limit, "progress_hooks": [progress],
                  "restrictfilenames": True, "hls_prefer_native": True, 'merge_output_format': 'mp4', 'http_headers': headers or {},
                  '_media_chunk_bytes': MEDIA_CHUNK_BYTES}
        if iqiyi_info:
            config['http_headers'].update(iqiyi_info['http_headers'])
            config.update(concurrent_fragment_downloads=8, fragment_retries=2,
                          skip_unavailable_fragments=False, _iqiyi_clear_only=True,
                          _iqiyi_segment_suffix=iqiyi_info.get('extra_param_to_segment_url', ''))
        # The total is enforced by max_filesize/progress; without a limit a response must
        # not fall back to the 32 MB page-parsing cap. A server that ignores Range still
        # sends the whole file as one response.
        media_network = Network(max_bytes=limit or float('inf'))
        # Segment requests belong to one selected file, not page crawling.
        media_network.config['domain_interval'] = 0
        try:
            with SafeYoutubeDL(config, media_network) as ydl:
                if iqiyi_info:
                    info = ydl.extract_info(iqiyi_info['manifest_url'], download=False)
                    info.update({key: value for key, value in iqiyi_info.items() if key not in {'manifest_url', 'extra_param_to_segment_url'}})
                    info = ydl.process_ie_result(info, download=True)
                else:
                    info = ydl.extract_info(url, download=True)
                path = Path(ydl.prepare_filename(info))
        except yt_dlp.utils.DownloadError as error:
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            if host == "douyin.com":
                page = fallback_video(url, Network(), str(error))
                media = next((asset for asset in page.get("assets", []) if asset.get("kind") == "video"), None)
                if media:
                    return Network().download(media["url"], directory, max_mb, headers={"User-Agent": "Mozilla/5.0", "Referer": url}, expected_media="video", filename=page["title"])
            if host in {"bilibili.com", "b23.tv"}:
                from .bilibili import download_bilibili
                return download_bilibili(url, directory, max_mb)
            raise download_error(url, error) from error
        if not path.exists():
            raise CrawlerError("VIDEO_DOWNLOAD_LIMIT", "File not downloaded; may exceed size limit or single-file format unavailable", "Adjust limit or use platform-supported download method")
        if limit is not None and path.stat().st_size > limit:
            raise CrawlerError("SIZE_LIMIT", "Download exceeds size limit")
        if iqiyi_info:
            from .bilibili_support.merger import MediaMerger, MergerError
            try:
                MediaMerger().verify_mp4(path, expected_duration=iqiyi_info['duration'], max_bytes=limit)
            except MergerError as error:
                raise CrawlerError(error.code, error.message, 'Integrity check failed, file not saved; please confirm full playback permissions on platform') from error
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
        name = safe_filename((info.get("title") or info.get("id") or "video") + path.suffix)
        target = directory / (digest.hexdigest()[:10] + "-" + name)
        path.replace(target)
        return {"url": url, "filename": name, "path": str(target), "bytes": target.stat().st_size, "sha256": digest.hexdigest(), "content_type": "video/" + path.suffix.lstrip(".")}
