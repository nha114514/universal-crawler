"""Evidence-backed Link Graph, PageRank, and Crawl Scheduling Prioritization.

Sorting is used strictly to determine retrieval and crawl order,
NEVER to discard or filter out broad discovery candidates.
"""
from __future__ import annotations

import urllib.parse
from typing import Dict, List, Set
from ..models import CandidateItem
from .dedup import canonicalize_url


def build_link_graph(items: List[CandidateItem]) -> Dict[str, Set[str]]:
    """Construct directed graph from candidates and their discovered links.

    Note: At initial discovery stage before web page content extraction, candidates
    have links=[] by default. In this zero-edge state, build_link_graph produces
    isolated nodes whose computed PageRank is uniformly 1/N. Non-trivial link
    topology applies during multi-hop crawling once outgoing links are extracted.
    """
    graph: Dict[str, Set[str]] = {}
    known_urls = {canonicalize_url(it.url) for it in items}

    for it in items:
        source = canonicalize_url(it.url)
        if source not in graph:
            graph[source] = set()
        for link in it.links:
            target = canonicalize_url(link)
            if target and target != source:
                graph[source].add(target)
                if target not in graph:
                    graph[target] = set()

    return graph


def compute_pagerank(graph: Dict[str, Set[str]], damping: float = 0.85,
                     max_iter: int = 50, tol: float = 1e-6) -> Dict[str, float]:
    """Calculate PageRank with damping factor and uniform dangling node mass redistribution."""
    nodes = list(graph.keys())
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: 1.0}

    # Initialize uniform probability
    scores = {node: 1.0 / n for node in nodes}
    in_links: Dict[str, List[str]] = {node: [] for node in nodes}
    out_degrees: Dict[str, int] = {node: len(graph[node]) for node in nodes}

    for src, targets in graph.items():
        for tgt in targets:
            if tgt in in_links:
                in_links[tgt].append(src)

    base = (1.0 - damping) / n

    for _ in range(max_iter):
        # Accumulate mass from dangling nodes (out_degree == 0)
        dangling_sum = sum(scores[node] for node in nodes if out_degrees[node] == 0)
        dangling_contrib = damping * (dangling_sum / n)

        new_scores: Dict[str, float] = {}
        for node in nodes:
            inbound_sum = sum(scores[src] / out_degrees[src] for src in in_links[node] if out_degrees[src] > 0)
            new_scores[node] = base + dangling_contrib + damping * inbound_sum

        # Check convergence
        diff = sum(abs(new_scores[node] - scores[node]) for node in nodes)
        scores = new_scores
        if diff < tol:
            break

    # Normalize sum to 1.0
    total = sum(scores.values()) or 1.0
    return {k: v / total for k, v in scores.items()}


def prioritize_candidates(items: List[CandidateItem],
                          graph: Optional[Dict[str, Set[str]]] = None) -> List[CandidateItem]:
    """Schedule crawl ordering based on discovered PageRank and domain diversity.

    Ranking is used ONLY for queue prioritization. Broad discovery candidates
    are NEVER deleted based on low score thresholds.
    """
    if not items:
        return []

    if graph is None:
        graph = build_link_graph(items)

    pr_scores = compute_pagerank(graph)

    # Calculate domain frequencies to promote host diversity
    host_counts: Dict[str, int] = {}
    for item in items:
        host = urllib.parse.urlsplit(item.url).hostname or "unknown"
        host_counts[host] = host_counts.get(host, 0) + 1

    # Assign priority score:
    # base PR score + rank origin bonus + domain diversity penalty factor
    for idx, item in enumerate(items):
        canon = canonicalize_url(item.url)
        pr = pr_scores.get(canon, 1.0 / (len(items) or 1))
        host = urllib.parse.urlsplit(item.url).hostname or "unknown"

        # Host penalty to prevent consecutive monopoly from single domain
        host_penalty = 1.0 / (host_counts.get(host, 1) ** 0.5)

        # Origin bonus: items found by multiple engines or top rank get boost
        origin_boost = 1.0 + 0.1 * len(item.search_origins)

        # Video/Platform special consideration: ensure tutorials/subtitled videos are interleaved
        kind_boost = 1.2 if item.kind == "video" else 1.0

        priority = pr * host_penalty * origin_boost * kind_boost
        item.crawl_priority = priority
        item.metadata["local_pagerank"] = pr
        item.metadata["rank_scope"] = "discovered_subgraph"

    # Sort descending by priority for crawl schedule
    prioritized = sorted(items, key=lambda x: x.crawl_priority, reverse=True)
    return prioritized
