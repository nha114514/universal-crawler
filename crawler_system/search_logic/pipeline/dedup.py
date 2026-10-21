"""URL Canonicalization, Content Deduplication, and Origin Merging."""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Dict, List, Set
from ..models import CandidateItem, TextStatus, SubtitleStatus


TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "spm_id_from", "from_source", "from", "vd_source",
    "_hsenc", "_hsmi", "mc_cid", "mc_eid", "ref", "source", "fclid"
}


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL: lowercase scheme/host, strip tracking params, sort query keys."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Remove standard ports
        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path[:-1]

        # Filter tracking params
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = [
            (k, v) for k, v in query_pairs
            if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
        ]
        clean_pairs.sort(key=lambda x: x[0])
        clean_query = urllib.parse.urlencode(clean_pairs)

        return urllib.parse.urlunsplit((scheme, netloc, path, clean_query, ""))
    except Exception:
        return url.strip()


def content_digest(text: str) -> str:
    """Compute SHA-256 digest for body text longer than 100 characters."""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) >= 100:
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()
    return ""


def merge_and_deduplicate(channel_results: List[List[CandidateItem]], budget: int = 50) -> List[CandidateItem]:
    """Merge candidates across channels and variants using fair round-robin scheduling.

    Retains all discovery origins, merges metadata, and deduplicates by canonical URL.
    """
    merged_items: List[CandidateItem] = []
    seen_urls: Dict[str, CandidateItem] = {}
    seen_digests: Set[str] = set()

    # Fair round-robin across lists
    max_len = max((len(lst) for lst in channel_results), default=0)
    for i in range(max_len):
        for lst in channel_results:
            if i >= len(lst):
                continue
            item = lst[i]
            canon_url = canonicalize_url(item.url)

            if canon_url in seen_urls:
                # Merge into existing item: preserve origins and sources
                existing = seen_urls[canon_url]
                for origin in item.search_origins:
                    if not any(o.engine == origin.engine and o.query == origin.query for o in existing.search_origins):
                        existing.search_origins.append(origin)
                for src in item.source_urls:
                    if src not in existing.source_urls:
                        existing.source_urls.append(src)
                # If incoming item has better text/title, merge
                if len(item.abstract) > len(existing.abstract):
                    existing.abstract = item.abstract
                if len(item.title) > len(existing.title) and "No title" in existing.title:
                    existing.title = item.title
            else:
                # Check content digest if full content present
                digest = content_digest(item.content or item.abstract)
                if digest and digest in seen_digests:
                    continue
                if digest:
                    seen_digests.add(digest)

                item.url = canon_url
                seen_urls[canon_url] = item
                merged_items.append(item)

            if len(merged_items) >= budget * 2:
                break
        if len(merged_items) >= budget * 2:
            break

    return merged_items[:budget]
