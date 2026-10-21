"""Bing Search Adapter: observable query processing, offline /ck/a decoding, and DOM extraction."""
from __future__ import annotations

import base64
import time
import urllib.parse
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

from ..models import CandidateItem, ChannelOutcome, SearchOrigin, SearchStatus, TextStatus, SubtitleStatus, utc_now


def decode_bing_redirect(url: str) -> str:
    """Offline decoding of Bing /ck/a? tracking redirect links.

    The target URL is encoded in the 'u' query parameter with prefix 'a1'
    followed by URL-safe Base64 encoded target URL.
    Validates that decoded output is a valid public HTTP/HTTPS URL.
    """
    if "/ck/a?" not in url and "/ck/a!" not in url:
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parsed.query)
        u_val = params.get("u", [""])[0]
        if u_val.startswith("a1"):
            raw_b64 = u_val[2:]
            padded = raw_b64 + "=" * (-len(raw_b64) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="ignore")
            target_parsed = urllib.parse.urlsplit(decoded)
            if target_parsed.scheme in ("http", "https") and target_parsed.netloc:
                return decoded
    except Exception:
        pass
    return url


class BingAdapter:
    def __init__(self, timeout: float = 15.0, proxy: Optional[str] = None):
        self.timeout = timeout
        self.proxy = proxy
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": "SRCHHPGUSR=ADLT=OFF&NRSLT=10;"
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, follow_redirects=True, headers=self.headers, proxy=self.proxy)

    def search(self, query: str, limit: int = 10, page: int = 1,
               market: str = "zh-CN", country: str = "CN") -> ChannelOutcome:
        start_time = time.monotonic()
        first = 1 + (page - 1) * limit
        params = {
            "q": query,
            "first": first,
            "count": limit,
            "setlang": market,
            "cc": country,
            "form": "QBLH"
        }
        url = f"https://www.bing.com/search?{urllib.parse.urlencode(params)}"
        try:
            with self._client() as client:
                res = client.get(url)
            latency = (time.monotonic() - start_time) * 1000

            if res.status_code != 200:
                return ChannelOutcome(
                    channel="bing",
                    status=SearchStatus.FAILED,
                    query=query,
                    message=f"HTTP {res.status_code}",
                    error_code=f"HTTP_{res.status_code}",
                    latency_ms=latency
                )

            # Verification / CAPTCHA check
            if "verify you are a human" in res.text or "百度安全验证" in res.text or "recaptcha" in res.text.lower():
                return ChannelOutcome(
                    channel="bing",
                    status=SearchStatus.VERIFICATION_REQUIRED,
                    query=query,
                    message="Bing verification required (Verification Required)",
                    error_code="SEARCH_VERIFICATION_REQUIRED",
                    latency_ms=latency
                )

            soup = BeautifulSoup(res.text, "html.parser")
            items: List[CandidateItem] = []
            algos = soup.select("li.b_algo")
            for idx, algo in enumerate(algos):
                h2 = algo.select_one("h2 a")
                if not h2:
                    continue
                raw_href = h2.get("href", "")
                if not raw_href:
                    continue
                target_url = decode_bing_redirect(raw_href)
                title = h2.get_text(separator=" ", strip=True)
                caption = algo.select_one(".b_caption p, .b_algoSlug, .b_lineclamp2, .b_lineclamp3")
                snippet = caption.get_text(separator=" ", strip=True) if caption else ""

                # Extract date if present
                cite = algo.select_one(".b_attribution cite")
                source_cite = cite.get_text(strip=True) if cite else ""

                item = CandidateItem(
                    id=f"bing_{int(time.time()*1000)}_{idx}",
                    url=target_url,
                    title=title or target_url,
                    kind="web",
                    abstract=snippet,
                    text_status=TextStatus.SEARCH_SNIPPET,
                    subtitle_status=SubtitleStatus.NONE,
                    metadata={
                        "raw_href": raw_href,
                        "source_cite": source_cite,
                        "engine": "bing",
                        "market": market,
                        "page": page
                    },
                    search_origins=[
                        SearchOrigin(engine="bing", query=query, scope="global", raw_rank=idx + 1)
                    ],
                    source_urls=[target_url]
                )
                items.append(item)

            status = SearchStatus.COMPLETED if items else SearchStatus.PARTIAL
            return ChannelOutcome(
                channel="bing",
                status=status,
                query=query,
                count=len(items),
                message=f"Successfully retrieved {len(items)} results",
                latency_ms=latency,
                items=items[:limit]
            )
        except Exception as e:
            latency = (time.monotonic() - start_time) * 1000
            return ChannelOutcome(
                channel="bing",
                status=SearchStatus.FAILED,
                query=query,
                message=str(e),
                error_code="REQUEST_ERROR",
                latency_ms=latency
            )

    def get_suggestions(self, query: str, market: str = "zh-CN") -> List[str]:
        """Fetch search suggestions from Bing AS/Suggestions API."""
        url = f"https://www.bing.com/AS/Suggestions?{urllib.parse.urlencode({'qry': query, 'mkt': market, 'cvid': '1'})}"
        try:
            with self._client() as client:
                res = client.get(url)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                return [li.get_text(strip=True) for li in soup.select("li.sa_sg") if li.get_text(strip=True)]
        except Exception:
            pass
        return []
