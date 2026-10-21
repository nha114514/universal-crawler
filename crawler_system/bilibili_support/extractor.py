"""
Bilibili Video Metadata & Playinfo Extractor
Extracts __INITIAL_STATE__ and __playinfo__ from Bilibili video pages
"""

import json
import re
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import urlsplit, parse_qs
from curl_cffi import requests
from .http import safe_get, HttpError


class ExtractionError(Exception):
    def __init__(self, code: str, message: str, remediation: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.remediation = remediation


class BilibiliExtractor:
    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    REFERER = "https://www.bilibili.com"

    def __init__(self, cookie: Optional[str] = None, user_agent: Optional[str] = None, validator=None):
        self.cookie = cookie
        self.user_agent = user_agent or self.DEFAULT_UA
        self.validator = validator

    @staticmethod
    def parse_video_target(target: str) -> Tuple[str, int]:
        """
        Extract case-preserved canonical BVID and part index (1-indexed) from URL or BV string.
        Must never lowercase the entire path!
        """
        target = target.strip()
        page_index = 1
        bvid = None

        if target.startswith(("http://", "https://")):
            parsed = urlsplit(target)
            qs = parse_qs(parsed.query)
            if "p" in qs and qs["p"]:
                try:
                    page_index = max(1, int(qs["p"][0]))
                except ValueError:
                    page_index = 1
            # Match BV ID in path (case-sensitive)
            match = re.search(r"/(BV[0-9a-zA-Z]{10})", parsed.path)
            if match:
                bvid = match.group(1)
        elif target.startswith("BV") and len(target) == 12:
            bvid = target
        else:
            match = re.search(r"(BV[0-9a-zA-Z]{10})", target)
            if match:
                bvid = match.group(1)

        if not bvid:
            raise ExtractionError("INVALID_BVID", f"Cannot parse valid BVID from input: {target}", "Check the input Bilibili video URL or BV ID")

        return bvid, page_index

    def fetch_page_html(self, bvid: str, page_index: int = 1) -> str:
        """
        Request target video page HTML with simulated browser fingerprint.
        """
        url = f"https://www.bilibili.com/video/{bvid}"
        if page_index > 1:
            url += f"?p={page_index}"

        headers = {
            "User-Agent": self.user_agent,
            "Referer": self.REFERER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie

        try:
            resp = safe_get(url, headers=headers, impersonate="chrome120", timeout=20, validator=self.validator)
        except HttpError as exc:
            raise ExtractionError(exc.code, exc.message)
        except Exception as exc:
            raise ExtractionError("NETWORK_FAILURE", f"Failed to request webpage: {exc}", "Check network connection or proxy settings")

        if resp.status_code == 404:
            raise ExtractionError("VIDEO_NOT_FOUND", "Video does not exist or has been deleted (HTTP 404)")
        if resp.status_code >= 400:
            raise ExtractionError("HTTP_ERROR", f"Webpage request returned HTTP {resp.status_code}")

        return resp.text

    @staticmethod
    def parse_initial_state(html: str) -> Dict[str, Any]:
        """
        Extract window.__INITIAL_STATE__ from page HTML.
        """
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html)
        if not match:
            # Try alternate SSR variable pattern
            match = re.search(r"<script>window\.__INITIAL_STATE__\s*=\s*(\{.*?\})</script>", html)
        if not match:
            raise ExtractionError("METADATA_PARSE_ERROR", "__INITIAL_STATE__ structure not found in page", "Page structure may have changed or was redirected")

        try:
            data = json.loads(match.group(1))
        except Exception as exc:
            raise ExtractionError("METADATA_PARSE_ERROR", f"Failed to parse __INITIAL_STATE__ JSON: {exc}")

        video_data = data.get("videoData")
        if not video_data:
            if "error" in data:
                raise ExtractionError("VIDEO_UNAVAILABLE", f"Bilibili returned error status: {data.get('error')}")
            raise ExtractionError("METADATA_PARSE_ERROR", "videoData not found in __INITIAL_STATE__")

        pages = video_data.get("pages", [])
        return {
            "bvid": video_data.get("bvid"),
            "aid": video_data.get("aid"),
            "cid": video_data.get("cid"),
            "title": video_data.get("title", ""),
            "desc": video_data.get("desc", ""),
            "pic": video_data.get("pic", ""),
            "duration": video_data.get("duration", 0),
            "owner": video_data.get("owner", {}),
            "pages": [
                {
                    "page": p.get("page", 1),
                    "cid": p.get("cid"),
                    "part": p.get("part", ""),
                    "duration": p.get("duration", 0),
                }
                for p in pages
            ],
        }

    @staticmethod
    def parse_playinfo(html: str) -> Dict[str, Any]:
        """
        Extract window.__playinfo__ from page HTML.
        """
        # Note: Use full script-closing regex to prevent truncation by internal semicolons
        match = re.search(r"window\.__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.DOTALL)
        if not match:
            match = re.search(r"window\.__playinfo__\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not match:
            raise ExtractionError("PLAYINFO_MISSING", "__playinfo__ stream info not found in page", "May be restricted by region or access permissions")

        try:
            raw_data = json.loads(match.group(1))
        except Exception as exc:
            raise ExtractionError("PLAYINFO_PARSE_ERROR", f"Failed to parse __playinfo__ JSON: {exc}")

        data = raw_data.get("data") or raw_data.get("result") or {}
        dash = data.get("dash")
        durl = data.get("durl")

        if not dash and not durl:
            raise ExtractionError("NO_PLAYABLE_STREAM", "No DASH or durl stream found in playinfo", "Check login status or quality permissions")

        return {
            "code": raw_data.get("code", 0),
            "message": raw_data.get("message", ""),
            "quality": data.get("quality"),
            "format": data.get("format"),
            "dash": dash,
            "durl": durl,
            "accept_quality": data.get("accept_quality", []),
            "accept_description": data.get("accept_description", []),
        }

    @staticmethod
    def select_dash_tracks(dash: Dict[str, Any], prefer_codec: str = "avc") -> Dict[str, Any]:
        """
        Select best video track and best audio track from DASH stream.
        Default video preference is avc (H.264) for maximum compatibility.
        Audio prefers highest bitrate.
        """
        videos: List[Dict[str, Any]] = dash.get("video", [])
        audios: List[Dict[str, Any]] = dash.get("audio", [])

        if not videos:
            raise ExtractionError("NO_VIDEO_TRACK", "No video track found in DASH stream")
        if not audios:
            raise ExtractionError("NO_AUDIO_TRACK", "No audio track found in DASH stream (video may be silent or parsing failed)")

        # Video track selection:
        # Sort descending by quality id, preferring prefer_codec for ties
        def video_sort_key(v):
            qid = v.get("id", 0)
            codecs = (v.get("codecs") or "").lower()
            codec_pref = 1 if prefer_codec in codecs else 0
            bandwidth = v.get("bandwidth", 0)
            return (qid, codec_pref, bandwidth)

        sorted_videos = sorted(videos, key=video_sort_key, reverse=True)
        chosen_video = sorted_videos[0]

        # Audio track selection:
        # Sort descending by bandwidth / id
        def audio_sort_key(a):
            return (a.get("bandwidth", 0), a.get("id", 0))

        sorted_audios = sorted(audios, key=audio_sort_key, reverse=True)
        chosen_audio = sorted_audios[0]

        return {
            "video": {
                "id": chosen_video.get("id"),
                "codecs": chosen_video.get("codecs"),
                "width": chosen_video.get("width"),
                "height": chosen_video.get("height"),
                "frameRate": chosen_video.get("frameRate"),
                "bandwidth": chosen_video.get("bandwidth"),
                "url": chosen_video.get("baseUrl") or (chosen_video.get("backupUrl") or [""])[0],
                "backup_urls": chosen_video.get("backupUrl", []),
            },
            "audio": {
                "id": chosen_audio.get("id"),
                "codecs": chosen_audio.get("codecs"),
                "bandwidth": chosen_audio.get("bandwidth"),
                "url": chosen_audio.get("baseUrl") or (chosen_audio.get("backupUrl") or [""])[0],
                "backup_urls": chosen_audio.get("backupUrl", []),
            },
        }

    def extract(self, target: str, page_index: Optional[int] = None) -> Dict[str, Any]:
        """
        Complete extraction entry point: extracts metadata, part info, and downloadable streams for the selected part.
        """
        bvid, default_page = self.parse_video_target(target)
        p = page_index if page_index is not None else default_page

        html = self.fetch_page_html(bvid, page_index=p)
        metadata = self.parse_initial_state(html)
        playinfo = self.parse_playinfo(html)

        # Determine info for current part
        current_part = metadata["title"]
        current_cid = metadata["cid"]
        current_duration = metadata['duration']
        selected = False
        for item in metadata["pages"]:
            if item["page"] == p:
                current_part = item["part"] or metadata["title"]
                current_cid = item["cid"]
                current_duration = item.get('duration') or current_duration
                selected = True
                break
        if metadata['pages'] and not selected:
            raise ExtractionError('INVALID_PAGE', 'Specified part does not exist', 'Select a part listed on the page')

        result = {
            "bvid": bvid,
            "aid": metadata["aid"],
            "cid": current_cid,
            "title": metadata["title"],
            "part_title": current_part,
            "page": p,
            "total_pages": len(metadata["pages"]),
            "pages": metadata["pages"],
            "duration": current_duration,
            'metadata_duration': metadata['duration'],
            "pic": metadata["pic"],
            "desc": metadata["desc"],
            "owner": metadata["owner"],
            "is_dash": bool(playinfo.get("dash")),
        }

        if playinfo.get("dash"):
            tracks = self.select_dash_tracks(playinfo["dash"])
            result["tracks"] = tracks
            result["stream_type"] = "dash"
        elif playinfo.get("durl"):
            # Single stream durl
            durl_list = playinfo["durl"]
            if len(durl_list) != 1:
                raise ExtractionError('MULTI_SEGMENT_UNSUPPORTED', 'This durl video contains multiple segments; full concatenation is not supported', 'Select a DASH source or use a tool supporting all segments')
            result["durl"] = durl_list
            result["stream_type"] = "durl"
            result["tracks"] = {
                "single": {
                    "url": durl_list[0].get("url"),
                    "size": durl_list[0].get("size"),
                }
            }

        return result
