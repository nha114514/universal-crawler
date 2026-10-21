"""Pipeline modules: dedup, ranking, and scheduling."""
from .dedup import canonicalize_url, content_digest, merge_and_deduplicate
from .ranking import build_link_graph, compute_pagerank, prioritize_candidates

__all__ = [
    "canonicalize_url",
    "content_digest",
    "merge_and_deduplicate",
    "build_link_graph",
    "compute_pagerank",
    "prioritize_candidates"
]
