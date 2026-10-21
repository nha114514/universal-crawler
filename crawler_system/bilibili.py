"""
Bilibili video extraction and download adapter for crawler_system
Reuses independently verified prototype implementation, maintaining public URL validation, private network blocking, hop-by-hop verification, size limits, and error model.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlsplit

from .config import settings
from .errors import CrawlerError
from .network import validate_url, safe_filename

from .bilibili_support.extractor import BilibiliExtractor, ExtractionError
from .bilibili_support.downloader import StreamDownloader, DownloadError
from .bilibili_support.merger import MediaMerger, MergerError


def collect_bilibili(url: str) -> Dict[str, Any]:
    """
    Extracts Bilibili video metadata and part info via webpage SSR, separating video candidate and cover.
    """
    validate_url(url)
    extractor = BilibiliExtractor(validator=validate_url)
    try:
        info = extractor.extract(url)
    except ExtractionError as exc:
        raise CrawlerError(exc.code, exc.message, exc.remediation)
    except Exception as exc:
        raise CrawlerError("VIDEO_UNAVAILABLE", f"Bilibili metadata extraction failed: {exc}")

    bvid = info["bvid"]
    title = info["title"]
    owner = info.get("owner", {})
    author = owner.get("name") or "bilibili_uploader"
    duration = info.get("duration")

    assets = [{"url": url, "name": "Original Video", "kind": "media"}]
    if info.get("pic"):
        assets.append({"url": info["pic"], "name": "Video Cover", "kind": "image"})

    return {
        "kind": "video",
        "url": url,
        "title": title,
        "authors": [author],
        "content": '',
        'abstract': info.get('desc', ''),
        "published": "",
        "metadata": {
            "duration": duration,
            "extractor": "bilibili-reconstruction",
            "bvid": bvid,
            "aid": info.get("aid"),
            "cid": info.get("cid"),
            "pages_count": info.get("total_pages", 1),
            "pages": info.get("pages", []),
            "thumbnail": info.get("pic"),
        },
        "extractor": "bilibili-reconstruction",
        "subtitles": [],
        "subtitle_text": "",
        "subtitle_status": "unchecked",
        "text_status": "abstract" if info.get('desc') else "metadata_only",
        "assets": assets,
        "warnings": ['Only metadata and description were extracted from the video page; subtitles not yet checked'],
    }


def download_bilibili(url: str, directory: Path, max_mb: int = 0) -> Dict[str, Any]:
    """
    Downloads Bilibili original video via DASH audio/video split-stream download and FFmpeg lossless merge.
    Strictly enforces private network blocking and size limits.
    """
    validate_url(url)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    limit = max_mb * 1024 * 1024 if max_mb else None

    extractor = BilibiliExtractor(validator=validate_url)
    try:
        info = extractor.extract(url)
    except ExtractionError as exc:
        raise CrawlerError(exc.code, exc.message, exc.remediation)
    except Exception as exc:
        raise CrawlerError("VIDEO_UNAVAILABLE", f"Failed to retrieve video stream info: {exc}")

    if info.get("stream_type") != "dash" and info.get("stream_type") != "durl":
        raise CrawlerError("VIDEO_UNAVAILABLE", f"Unsupported video stream type: {info.get('stream_type')}")

    downloader = StreamDownloader(validator=validate_url)
    merger = MediaMerger()

    with tempfile.TemporaryDirectory(dir=directory, prefix="bili-merge-") as tmp_dir:
        tmp_path = Path(tmp_dir)

        if info.get("stream_type") == "dash":
            tracks = info["tracks"]
            video_url = tracks["video"]["url"]
            audio_url = tracks["audio"]["url"]

            # Validate media stream CDN domains are non-private
            validate_url(video_url)
            validate_url(audio_url)
            for bu in tracks["video"].get("backup_urls", []):
                if bu:
                    validate_url(bu)
            for bu in tracks["audio"].get("backup_urls", []):
                if bu:
                    validate_url(bu)

            video_tmp = tmp_path / "video.m4s"
            audio_tmp = tmp_path / "audio.m4s"

            try:
                # 1. Download video track
                video_res = downloader.download_to_file(
                    video_url,
                    video_tmp,
                    backup_urls=tracks["video"].get("backup_urls"),
                    max_bytes=limit,
                )

                # 2. Download audio track
                rem_limit = limit - video_res["bytes"] if limit is not None else None
                if rem_limit is not None and rem_limit <= 0:
                    raise CrawlerError("SIZE_LIMIT", f"Video exceeds {max_mb} MB", "Increase download limit and retry")

                audio_res = downloader.download_to_file(
                    audio_url,
                    audio_tmp,
                    backup_urls=tracks["audio"].get("backup_urls"),
                    max_bytes=rem_limit,
                )

                # 3. FFmpeg lossless merge
                merged_tmp = tmp_path / "merged.mp4"
                merger.merge_dash(video_tmp, audio_tmp, merged_tmp)

            except DownloadError as exc:
                if exc.code == "SIZE_LIMIT":
                    raise CrawlerError("SIZE_LIMIT", exc.message, exc.remediation)
                raise CrawlerError("DOWNLOAD_FAILED", exc.message, exc.remediation)
            except MergerError as exc:
                raise CrawlerError("MERGE_FAILED", exc.message, exc.remediation)

            source_file = merged_tmp

        elif info.get("stream_type") == "durl":
            durl_url = info["tracks"]["single"]["url"]
            validate_url(durl_url)
            single_tmp = tmp_path / "stream.mp4"
            try:
                downloader.download_to_file(durl_url, single_tmp, max_bytes=limit)
            except DownloadError as exc:
                if exc.code == "SIZE_LIMIT":
                    raise CrawlerError("SIZE_LIMIT", exc.message, exc.remediation)
                raise CrawlerError("DOWNLOAD_FAILED", exc.message, exc.remediation)
            source_file = single_tmp

        if limit is not None and source_file.stat().st_size > limit:
            raise CrawlerError('SIZE_LIMIT', 'Merged original file exceeds download limit', 'Increase download limit and retry')
        # 4. ffprobe strict inspection
        try:
            merger.verify_mp4(source_file, expected_duration=info.get('duration', 0), max_bytes=limit)
        except MergerError as exc:
            raise CrawlerError(exc.code, exc.message, exc.remediation)

        # 5. Hash and target persistence
        digest = hashlib.sha256()
        with source_file.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        sha256_hex = digest.hexdigest()

        title_stem = safe_filename((info.get("title") or info["bvid"]) + ".mp4")
        target_name = f"{sha256_hex[:10]}-{title_stem}"
        target_path = directory / target_name

        shutil.move(str(source_file), str(target_path))

        return {
            "url": url,
            "filename": target_name,
            "path": str(target_path),
            "bytes": target_path.stat().st_size,
            "sha256": sha256_hex,
            "content_type": "video/mp4",
        }
