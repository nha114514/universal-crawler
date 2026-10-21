"""Bilibili Search and Subtitle Specialized Adapter:
Implements WBI signing, multi-modal search, video SSR details, and player subtitle retrieval.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import httpx

from ..models import CandidateItem, ChannelOutcome, SearchOrigin, SearchStatus, SubtitleItem, SubtitleStatus, TextStatus, utc_now

# Observable 64-element WBI mixin permutation table
OE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52
]


class BilibiliAdapter:
    def __init__(self, timeout: float = 12.0, proxy: Optional[str] = None):
        self.timeout = timeout
        self.proxy = proxy
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        self._mixin_key: Optional[str] = None
        self._mixin_key_expires: float = 0.0

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, follow_redirects=True, headers=self.headers, proxy=self.proxy)

    def get_mixin_key(self) -> str:
        """Fetch nav API and calculate dynamic mixin key."""
        now = time.monotonic()
        if self._mixin_key and now < self._mixin_key_expires:
            return self._mixin_key
        try:
            with self._client() as client:
                res = client.get("https://api.bilibili.com/x/web-interface/nav")
                data = res.json()
                wbi_img = data.get("data", {}).get("wbi_img", {})
                img_url = wbi_img.get("img_url", "")
                sub_url = wbi_img.get("sub_url", "")
                img_key = img_url.rsplit("/", 1)[1].split(".")[0]
                sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
                raw_key = img_key + sub_key
                mixin_key = "".join(raw_key[i] for i in OE if i < len(raw_key))[:32]
                self._mixin_key = mixin_key
                self._mixin_key_expires = now + 1800  # cache 30 mins
                return mixin_key
        except Exception as e:
            # Fallback static mixin key observed if nav is temporarily unreachable.
            # Explicitly emit runtime warning so fallback is visible and not silently masked.
            import warnings
            warnings.warn(f"Failed to fetch live WBI mixin key from nav API ({e}), using fallback key.", RuntimeWarning)
            return "ea1db124af3c7062474693fa704f4ff8"

    def sign_wbi(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sign request parameters with mixin key."""
        mixin_key = self.get_mixin_key()
        signed = dict(params)
        signed["wts"] = int(time.time())
        sorted_params = dict(sorted(signed.items()))
        filtered_params = {
            k: "".join(c for c in str(v) if c not in "!'()*")
            for k, v in sorted_params.items()
        }
        query_str = urllib.parse.urlencode(filtered_params)
        w_rid = hashlib.md5((query_str + mixin_key).encode()).hexdigest()
        signed["w_rid"] = w_rid
        return signed

    def search(self, keyword: str, limit: int = 10, page: int = 1,
               order: str = "totalrank", duration: int = 0) -> ChannelOutcome:
        """Search Bilibili video catalog using signed WBI endpoint."""
        start_time = time.monotonic()
        params = {
            "keyword": keyword,
            "page": page,
            "order": order
        }
        if duration > 0:
            params["duration"] = duration

        signed_params = self.sign_wbi(params)
        url = "https://api.bilibili.com/x/web-interface/wbi/search/all/v2"

        try:
            with self._client() as client:
                res = client.get(url, params=signed_params)
            latency = (time.monotonic() - start_time) * 1000

            if res.status_code != 200:
                return ChannelOutcome(
                    channel="bilibili",
                    status=SearchStatus.FAILED,
                    query=keyword,
                    message=f"HTTP {res.status_code}",
                    error_code=f"HTTP_{res.status_code}",
                    latency_ms=latency
                )

            data = res.json()
            code = data.get("code", -1)
            if code != 0:
                msg = data.get("message", "Unknown Bilibili API error")
                status = SearchStatus.VERIFICATION_REQUIRED if code == -412 else SearchStatus.FAILED
                return ChannelOutcome(
                    channel="bilibili",
                    status=status,
                    query=keyword,
                    message=f"Bilibili code {code}: {msg}",
                    error_code=f"BILI_{code}",
                    latency_ms=latency
                )

            items: List[CandidateItem] = []
            results = data.get("data", {}).get("result", [])
            for group in results:
                if isinstance(group, dict) and group.get("result_type") == "video":
                    for idx, v in enumerate(group.get("data", [])):
                        bvid = v.get("bvid", "")
                        if not bvid:
                            continue
                        clean_title = re.sub(r"<[^>]+>", "", v.get("title", ""))
                        video_url = f"https://www.bilibili.com/video/{bvid}/"
                        raw_pubdate = str(v.get("pubdate", ""))
                        duration_str = str(v.get("duration", ""))
                        tags = [t.strip() for t in str(v.get("tag", "")).split(",") if t.strip()]

                        item = CandidateItem(
                            id=f"bili_{bvid}_{idx}",
                            url=video_url,
                            title=clean_title or video_url,
                            kind="video",
                            abstract=clean_title,
                            text_status=TextStatus.SEARCH_SNIPPET,
                            subtitle_status=SubtitleStatus.UNCHECKED,
                            authors=[v.get("author", "")] if v.get("author") else [],
                            pubdate=raw_pubdate,
                            duration=duration_str,
                            metadata={
                                "bvid": bvid,
                                "author": v.get("author", ""),
                                "mid": v.get("mid"),
                                "play": v.get("play"),
                                "danmaku": v.get("video_review"),
                                "tags": tags,
                                "engine": "bilibili"
                            },
                            search_origins=[
                                SearchOrigin(engine="bilibili", query=keyword, scope="platform", raw_rank=idx + 1)
                            ],
                            source_urls=[video_url]
                        )
                        items.append(item)

            status = SearchStatus.COMPLETED if items else SearchStatus.PARTIAL
            return ChannelOutcome(
                channel="bilibili",
                status=status,
                query=keyword,
                count=len(items),
                message=f"Bilibili search successfully retrieved {len(items)} video candidates",
                latency_ms=latency,
                items=items[:limit]
            )
        except Exception as e:
            latency = (time.monotonic() - start_time) * 1000
            return ChannelOutcome(
                channel="bilibili",
                status=SearchStatus.FAILED,
                query=keyword,
                message=str(e),
                error_code="REQUEST_ERROR",
                latency_ms=latency
            )

    def extract_video_details(self, bvid: str) -> Dict[str, Any]:
        """Fetch video SSR HTML and extract window.__INITIAL_STATE__ details."""
        url = f"https://www.bilibili.com/video/{bvid}/"
        try:
            with self._client() as client:
                res = client.get(url)
            if res.status_code != 200:
                return {"error": f"HTTP {res.status_code}"}
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', res.text)
            if not match:
                return {"error": "SSR initial state not found"}
            state = json.loads(match.group(1))
            v_data = state.get("videoData", {})
            return {
                "bvid": v_data.get("bvid", bvid),
                "aid": v_data.get("aid"),
                "cid": v_data.get("cid"),
                "title": v_data.get("title", ""),
                "desc": v_data.get("desc", ""),
                "pubdate": str(v_data.get("pubdate", "")),
                "duration": v_data.get("duration", 0),
                "owner": v_data.get("owner", {}),
                "stat": v_data.get("stat", {}),
                "tname": v_data.get("tname", ""),
                "pages": [
                    {
                        "cid": p.get("cid"),
                        "page": p.get("page"),
                        "part": p.get("part"),
                        "duration": p.get("duration")
                    } for p in v_data.get("pages", [])
                ]
            }
        except Exception as e:
            return {"error": str(e)}

    def fetch_subtitles(self, bvid: str, cid: int) -> Tuple[SubtitleStatus, List[SubtitleItem], str]:
        """Fetch subtitle track for a video and partition cid using WBI player API.

        Returns:
            (SubtitleStatus, list of SubtitleItem, combined subtitle text)
        """
        params = self.sign_wbi({"bvid": bvid, "cid": cid})
        url = "https://api.bilibili.com/x/player/wbi/v2"
        try:
            with self._client() as client:
                res = client.get(url, params=params)
                if res.status_code != 200:
                    return SubtitleStatus.FAILED, [], ""
                data = res.json()
                code = data.get("code", 0)
                if code != 0:
                    msg = str(data.get("message", "")).lower()
                    if code == -101 or "login" in msg or "not logged in" in msg or "登录" in msg:
                        return SubtitleStatus.LOGIN_REQUIRED, [], ""
                    return SubtitleStatus.FAILED, [], ""

                sub_info = data.get("data", {}).get("subtitle", {})
                raw_subs = sub_info.get("subtitles", [])
                if not raw_subs:
                    return SubtitleStatus.NONE, [], ""

                items: List[SubtitleItem] = []
                combined_texts: List[str] = []

                for s in raw_subs:
                    sub_url = s.get("subtitle_url", "")
                    if not sub_url:
                        continue
                    full_url = "https:" + sub_url if sub_url.startswith("//") else sub_url
                    try:
                        sub_res = client.get(full_url)
                        if sub_res.status_code == 200:
                            sub_json = sub_res.json()
                            lines = sub_json.get("body", [])
                            line_texts = [line.get("content", "").strip() for line in lines if line.get("content")]
                            body_str = "\n".join(line_texts)
                            combined_texts.append(body_str)
                            items.append(SubtitleItem(
                                language=s.get("lan", ""),
                                language_doc=s.get("lan_doc", ""),
                                format="json",
                                url=full_url,
                                lines=lines,
                                raw_text=body_str
                            ))
                    except Exception:
                        continue

                if items:
                    return SubtitleStatus.AVAILABLE, items, "\n\n".join(combined_texts)
                return SubtitleStatus.NONE, [], ""
        except Exception:
            return SubtitleStatus.FAILED, [], ""
