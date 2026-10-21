"""Data models and status definitions for search reconstruction."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SearchStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    VERIFICATION_REQUIRED = "verification_required"
    FAILED = "failed"


class TextStatus(str, Enum):
    FULL_TEXT = "full_text"
    SEARCH_SNIPPET = "search_snippet"
    ABSTRACT = "abstract"
    METADATA_ONLY = "metadata_only"
    FAILED = "failed"


class SubtitleStatus(str, Enum):
    AVAILABLE = "available"
    NONE = "none"
    LOGIN_REQUIRED = "login_required"
    UNCHECKED = "unchecked"
    FAILED = "failed"


@dataclass
class SearchOrigin:
    engine: str
    query: str
    scope: str = "global"
    discovered_at: str = field(default_factory=utc_now)
    raw_rank: int = 0


@dataclass
class SubtitleItem:
    language: str
    language_doc: str
    format: str = "json"
    url: str = ""
    lines: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class CandidateItem:
    id: str
    url: str
    title: str
    kind: str = "web"  # web, video, paper, article
    abstract: str = ""
    content: str = ""
    text_status: TextStatus = TextStatus.SEARCH_SNIPPET
    subtitle_status: SubtitleStatus = SubtitleStatus.UNCHECKED
    subtitle_text: str = ""
    subtitles: List[SubtitleItem] = field(default_factory=list)
    authors: List[str] = field(default_factory=list)
    pubdate: str = ""
    duration: str = ""
    links: List[str] = field(default_factory=list)
    assets: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    search_origins: List[SearchOrigin] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    crawl_priority: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "kind": self.kind,
            "abstract": self.abstract,
            "content": self.content,
            "text_status": self.text_status.value if isinstance(self.text_status, TextStatus) else self.text_status,
            "subtitle_status": self.subtitle_status.value if isinstance(self.subtitle_status, SubtitleStatus) else self.subtitle_status,
            "subtitle_text": self.subtitle_text,
            "subtitles": [
                {
                    "language": s.language,
                    "language_doc": s.language_doc,
                    "format": s.format,
                    "url": s.url,
                    "line_count": len(s.lines),
                    "raw_text": s.raw_text[:500] if s.raw_text else ""
                } for s in self.subtitles
            ],
            "authors": self.authors,
            "pubdate": self.pubdate,
            "duration": self.duration,
            "links": self.links,
            "assets": self.assets,
            "metadata": self.metadata,
            "search_origins": [
                {
                    "engine": o.engine,
                    "query": o.query,
                    "scope": o.scope,
                    "discovered_at": o.discovered_at,
                    "raw_rank": o.raw_rank
                } for o in self.search_origins
            ],
            "source_urls": self.source_urls,
            "warnings": self.warnings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "crawl_priority": self.crawl_priority
        }


@dataclass
class ChannelOutcome:
    channel: str
    status: SearchStatus
    query: str
    count: int = 0
    message: str = ""
    error_code: str = ""
    latency_ms: float = 0.0
    items: List[CandidateItem] = field(default_factory=list)


@dataclass
class SearchPlanResult:
    original_query: str
    query_variants: List[Dict[str, str]]
    outcomes: List[ChannelOutcome]
    total_candidates: int
    unique_candidates: int
    items: List[CandidateItem]
    duration_ms: float
