"""Google Search Adapter:
Observable query processing, operators support, bot challenge detection,
and suggestions API retrieval.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

from ..models import CandidateItem, ChannelOutcome, SearchOrigin, SearchStatus, SubtitleStatus, TextStatus, utc_now


def decode_google_redirect(url: str) -> str:
    """Decode Google /url?q=... redirect links if present."""
    if "/url?" in url:
        try:
            parsed = urllib.parse.urlsplit(url)
            params = urllib.parse.parse_qs(parsed.query)
            target = params.get("q", params.get("url", [""]))[0]
            if target.startswith(("http://", "https://")):
                return target
        except Exception:
            pass
    return url


class GoogleAdapter:
    def __init__(self, timeout: float = 12.0, proxy: Optional[str] = None):
        self.timeout = timeout
        self.proxy = proxy
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, follow_redirects=True, headers=self.headers, proxy=self.proxy)

    def search(self, query: str, limit: int = 10, start: int = 0,
               hl: str = "en", gl: str = "us", udm: int = 14,
               site: Optional[str] = None, filetype: Optional[str] = None) -> ChannelOutcome:
        """Execute Google search probe and extract SERP or capture bot verification."""
        start_time = time.monotonic()
        actual_query = query
        if site:
            actual_query += f" site:{site}"
        if filetype:
            actual_query += f" filetype:{filetype}"

        params = {
            "q": actual_query,
            "hl": hl,
            "gl": gl,
            "num": limit,
            "start": start
        }
        if udm:
            params["udm"] = udm  # udm=14 for pure web-only search

        url = f"https://www.google.com/search?{urllib.parse.urlencode(params)}"
        try:
            with self._client() as client:
                res = client.get(url)
            latency = (time.monotonic() - start_time) * 1000

            # Direct verification/bot detection analysis
            if ("/sorry/index" in str(res.url) or
                "enablejs?sei=" in res.text or
                "recaptcha" in res.text.lower() or
                "unusual traffic from your computer network" in res.text):
                return ChannelOutcome(
                    channel="google",
                    status=SearchStatus.VERIFICATION_REQUIRED,
                    query=actual_query,
                    count=0,
                    message="Google botguard challenge triggered (HTTP 200 + reCAPTCHA/Botguard landing page); verification status recorded",
                    error_code="GOOGLE_BOTGUARD_CHALLENGE",
                    latency_ms=latency,
                    items=[]
                )

            if res.status_code != 200:
                return ChannelOutcome(
                    channel="google",
                    status=SearchStatus.FAILED,
                    query=actual_query,
                    message=f"HTTP {res.status_code}",
                    error_code=f"HTTP_{res.status_code}",
                    latency_ms=latency
                )

            # Extract normal Google SERP items if available
            soup = BeautifulSoup(res.text, "html.parser")
            items: List[CandidateItem] = []
            cards = soup.select("div.g, div[data-hveid]")
            for idx, card in enumerate(cards):
                h3 = card.select_one("h3")
                a_tag = card.select_one("a[href]")
                if not h3 or not a_tag:
                    continue
                href = a_tag.get("href", "")
                if not href or href.startswith(("#", "/")):
                    continue
                target_url = decode_google_redirect(href)
                title = h3.get_text(separator=" ", strip=True)
                snippet_elem = card.select_one(".VwiC3b, .yXK7lf, span.aCOpRe")
                snippet = snippet_elem.get_text(separator=" ", strip=True) if snippet_elem else ""

                item = CandidateItem(
                    id=f"google_{int(time.time()*1000)}_{idx}",
                    url=target_url,
                    title=title or target_url,
                    kind="web",
                    abstract=snippet,
                    text_status=TextStatus.SEARCH_SNIPPET,
                    subtitle_status=SubtitleStatus.NONE,
                    metadata={
                        "raw_href": href,
                        "engine": "google",
                        "hl": hl,
                        "gl": gl
                    },
                    search_origins=[
                        SearchOrigin(engine="google", query=actual_query, scope="global", raw_rank=idx + 1)
                    ],
                    source_urls=[target_url]
                )
                items.append(item)

            status = SearchStatus.COMPLETED if items else SearchStatus.PARTIAL
            return ChannelOutcome(
                channel="google",
                status=status,
                query=actual_query,
                count=len(items),
                message=f"Google retrieved {len(items)} results",
                latency_ms=latency,
                items=items[:limit]
            )
        except Exception as e:
            latency = (time.monotonic() - start_time) * 1000
            return ChannelOutcome(
                channel="google",
                status=SearchStatus.FAILED,
                query=actual_query,
                message=str(e),
                error_code="REQUEST_ERROR",
                latency_ms=latency
            )

    def get_suggestions(self, query: str, hl: str = "en") -> List[str]:
        """Fetch search suggestions from Google Suggest Queries API."""
        url = f"https://suggestqueries.google.com/complete/search?{urllib.parse.urlencode({'client': 'chrome', 'q': query, 'hl': hl})}"
        try:
            with self._client() as client:
                res = client.get(url)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    return [str(s) for s in data[1] if s]
        except Exception:
            pass
        return []
