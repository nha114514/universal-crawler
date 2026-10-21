"""
Bilibili Stream Downloader
Supports Range requests, anti-hotlinking Referer header, size limit checks, hash calculation, chunked resume, and CDN fallback node switching
"""

import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from curl_cffi import requests
from .http import safe_get, HttpError


class DownloadError(Exception):
    def __init__(self, code: str, message: str, remediation: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.remediation = remediation


class StreamDownloader:
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    REFERER = "https://www.bilibili.com"
    CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunk Range

    def __init__(self, user_agent: Optional[str] = None, validator=None):
        self.user_agent = user_agent or self.DEFAULT_UA
        self.validator = validator

    def probe_stream(self, url: str, backup_urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Send Range: bytes=0-1 probe to media stream to get Content-Range and total size.
        If error or 532 is encountered, automatically try backup_urls.
        """
        candidate_urls = [url] + [u for u in (backup_urls or []) if u and u != url]
        headers = {
            "User-Agent": self.user_agent,
            "Referer": self.REFERER,
            "Range": "bytes=0-1",
        }

        last_error = None
        for cand_url in candidate_urls:
            for attempt in range(2):
                try:
                    resp = safe_get(cand_url, headers=headers, impersonate="chrome120", timeout=15, validator=self.validator)
                    if resp.status_code == 403:
                        last_error = DownloadError("ACCESS_DENIED", "Media server returned 403; attempting backup URL")
                        break

                    if resp.status_code in (200, 206):
                        content_range = resp.headers.get("content-range", "")
                        total_size = None
                        if "/" in content_range:
                            try:
                                total_size = int(content_range.split("/")[-1])
                            except ValueError:
                                pass

                        if total_size is None and "content-length" in resp.headers:
                            try:
                                total_size = int(resp.headers["content-length"])
                            except ValueError:
                                pass

                        if total_size:
                            return {
                                "valid_url": cand_url,
                                "status_code": resp.status_code,
                                "content_type": resp.headers.get("content-type", "video/mp4"),
                                "content_range": content_range,
                                "total_size": total_size,
                            }
                except HttpError as error:
                    last_error = DownloadError(error.code, error.message)
                except Exception as error:
                    last_error = error
                time.sleep(0.3)

        raise DownloadError("PROBE_FAILED", "Failed to probe media stream size via Range" + (f": {last_error}" if last_error else ''))

    def download_to_file(
        self,
        url: str,
        dest_path: Path,
        backup_urls: Optional[List[str]] = None,
        max_bytes: Optional[int] = None,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download media stream to target file using precise Range chunk requests.
        Features resume, chunk validation, size limit circuit-breaker, and automatic CDN fallback.
        """
        dest_path = Path(dest_path).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")

        # 1. Probe size and available nodes
        probe = self.probe_stream(url, backup_urls=backup_urls)
        working_url = probe["valid_url"]
        total_size = probe["total_size"]

        if max_bytes is not None and total_size > max_bytes:
            raise DownloadError(
                "SIZE_LIMIT",
                f"Media stream size ({total_size / 1024 / 1024:.2f} MB) exceeds limit ({max_bytes / 1024 / 1024:.2f} MB)",
                "Please increase download limit and retry",
            )

        candidate_urls = [working_url] + [u for u in ([url] + (backup_urls or [])) if u != working_url]

        # 2. Check existing .part file resume point
        downloaded = 0
        if part_path.exists():
            downloaded = part_path.stat().st_size
            if downloaded > total_size:
                # Corrupted file, reset
                part_path.unlink()
                downloaded = 0

        # 3. Loop download by chunk Range
        while downloaded < total_size:
            chunk_end = min(downloaded + self.CHUNK_SIZE - 1, total_size - 1)
            range_header = f"bytes={downloaded}-{chunk_end}"
            expected_chunk_bytes = chunk_end - downloaded + 1

            chunk_success = False
            last_err = None

            for cand_url in candidate_urls:
                headers = {
                    "User-Agent": self.user_agent,
                    "Referer": self.REFERER,
                    "Range": range_header,
                }
                for attempt in range(2):
                    try:
                        resp = safe_get(cand_url, headers=headers, impersonate="chrome120", timeout=25, validator=self.validator)
                        expected_range = f'bytes {downloaded}-{chunk_end}/{total_size}'
                        if resp.status_code == 206 and resp.headers.get('content-range') == expected_range and len(resp.content) == expected_chunk_bytes:
                            with open(part_path, "ab" if downloaded > 0 else "wb") as f:
                                f.write(resp.content)
                            downloaded += len(resp.content)
                            if progress_callback:
                                progress_callback(downloaded, total_size)
                            chunk_success = True
                            break
                        else:
                            last_err = f"HTTP {resp.status_code}, len={len(resp.content)} expected={expected_chunk_bytes}"
                    except Exception as exc:
                        last_err = str(exc)
                    time.sleep(0.3)

                if chunk_success:
                    break

            if not chunk_success:
                raise DownloadError("DOWNLOAD_FAILED", f"Downloading chunk {range_header} failed: {last_err}")

        # 4. Verify total length and compute final SHA-256
        if downloaded != total_size:
            raise DownloadError("DOWNLOAD_INCOMPLETE", f"Downloaded bytes ({downloaded}) does not match declared ({total_size})")

        sha256 = hashlib.sha256()
        with open(part_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)

        # 5. Atomic rename
        if dest_path.exists():
            dest_path.unlink()
        part_path.rename(dest_path)

        return {
            "path": str(dest_path),
            "bytes": downloaded,
            "sha256": sha256.hexdigest(),
            "content_type": probe.get("content_type", "video/mp4"),
        }
