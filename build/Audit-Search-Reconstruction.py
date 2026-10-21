"""Read-only, offline audit probes for the independent search prototype."""
import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import re
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import MagicMock, patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prototype", default="D:/Dev/project/search-reconstruction")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.prototype).resolve()
    sys.path.insert(0, str(root))
    from src.adapters.bilibili import BilibiliAdapter
    from src.adapters.bing import decode_bing_redirect
    from src.indexing.local_index import LocalIndex
    from src.models import CandidateItem, TextStatus, SubtitleStatus
    from src.pipeline.dedup import canonicalize_url
    from src.pipeline.ranking import build_link_graph, compute_pagerank
    from src.pipeline.scheduler import CrawlScheduler
    from src.query.expansion import QueryExpander

    manifest = json.loads((root / "hash_manifest.json").read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            mismatches.append(name)
    output = {"checked_at": datetime.now(timezone.utc).isoformat(), "prototype": str(root), "manifest_files": len(manifest), "manifest_mismatches": mismatches}
    output["bing_decoder"] = decode_bing_redirect("https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLm9yZy9HdWlkZQ")
    source = "https://www.bilibili.com/video/BV12DHk69E3H/"
    output["bvid_url"] = {"input": source, "output": canonicalize_url(source), "case_preserved": "BV12DHk69E3H" in canonicalize_url(source)}

    adapter = BilibiliAdapter()
    response = MagicMock(status_code=200)
    response.json.return_value = {"code": -101, "message": "Not logged in"}
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response
    with patch.object(adapter, "_client", return_value=client), patch.object(adapter, "get_mixin_key", return_value="fixture"):
        status, _, _ = adapter.fetch_subtitles("BV12DHk69E3H", 1)
    output["login_subtitle_classification"] = {"api_code": -101, "result": status.value}

    expander = QueryExpander(use_live_suggestions=True)
    with patch.object(expander.google_adapter, "get_suggestions", return_value=["Google live fixture"]) as google, patch.object(expander.bing_adapter, "get_suggestions", return_value=["Bing live fixture"]) as bing:
        variants = expander.expand("海龟汤", max_variants=5)
        output["live_expansion_for_benchmark_query"] = {"variants": variants, "google_calls": google.call_count, "bing_calls": bing.call_count}
    output["dictionary_false_match"] = expander_without_network("dragon", QueryExpander)

    candidates = [CandidateItem(id=str(i), url=f"https://example.org/page{i}", title=f"Candidate {i}") for i in range(3)]
    graph = build_link_graph(candidates)
    output["empty_link_pagerank"] = {"edges": sum(len(v) for v in graph.values()), "scores": compute_pagerank(graph)}

    with tempfile.TemporaryDirectory() as directory:
        index = LocalIndex(str(Path(directory) / "quality.sqlite3"))
        url = "https://example.org/quality"
        original = "完整正文测试文本" * 50
        index.save_item(CandidateItem(id="original", url=url, title="Quality", content=original, text_status=TextStatus.FULL_TEXT))
        index.save_item(CandidateItem(id="refresh", url=url, title="Quality", content="搜索摘要", text_status=TextStatus.SEARCH_SNIPPET))
        saved = index.search("Quality")[0]
        output["nonempty_snippet_overwrite"] = {"original_chars": len(original), "saved_chars": len(saved["content"]), "saved_content": saved["content"], "saved_text_status": saved["text_status"]}

        index2 = LocalIndex(str(Path(directory) / "retained.sqlite3"))
        index2.save_item(CandidateItem(id="original", url=url, title="Retained", content=original, text_status=TextStatus.FULL_TEXT, subtitle_text="已有字幕", subtitle_status=SubtitleStatus.AVAILABLE))
        index2.save_item(CandidateItem(id="refresh", url=url, title="Retained", text_status=TextStatus.SEARCH_SNIPPET, subtitle_status=SubtitleStatus.NONE))
        retained = index2.search("Retained")[0]
        output["retained_payload_vs_stats"] = {"payload_text_status": retained["text_status"], "payload_subtitle_status": retained["subtitle_status"], "stats": index2.get_stats()}
        # The prototype commits SQLite context managers but never closes them.
        # Collect their cycles before removing the Windows-only test database.
        gc.collect()

    item = CandidateItem(id="cache", url="https://example.org/cached", title="Cached", content="Body", text_status=TextStatus.FULL_TEXT)
    scheduler = CrawlScheduler(domain_interval=0)
    with patch.object(scheduler.extractor, "process_item", return_value=item):
        first = scheduler._rate_limited_extract(item)[1]
        second = scheduler._rate_limited_extract(item)[1]
    next_scheduler = CrawlScheduler(domain_interval=0)
    with patch.object(next_scheduler.extractor, "process_item", return_value=item):
        fresh_instance = next_scheduler._rate_limited_extract(item)[1]
    output["cache_lifetime"] = {"first_hit": first, "same_instance_second_hit": second, "new_instance_hit": fresh_instance}

    benchmark = json.loads((root / "evidence/benchmark_results.json").read_text(encoding="utf-8"))
    rows = [topic["modes"]["bilibili"] for topic in benchmark["topics"]]
    output["benchmark"] = {"topics": len(rows), "modes": list(benchmark["topics"][0]["modes"]), "bilibili_candidates": sum(row["unique_candidates"] for row in rows), "none_subtitles": sum(row["none_subtitles"] for row in rows), "scheduled_extractions": sum(row["network_fetches"] for row in rows), "cache_hits_all_modes": sum(row["cache_hits"] for topic in benchmark["topics"] for row in topic["modes"].values()), "google_candidate_counts": [topic["modes"]["google"]["unique_candidates"] for topic in benchmark["topics"]]}
    import sqlite3
    database = root / "data/benchmark_library.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            output["benchmark"]["saved_candidate_rows"] = db.execute("SELECT count(*) FROM items").fetchone()[0]
    sample = json.loads((root / "evidence/bilibili/raw_search_response.json").read_text(encoding="utf-8"))
    groups = sample.get("data", {}).get("result", [])
    output["bilibili_raw_sample"] = {"code": sample.get("code"), "videos": sum(len(g.get("data", [])) for g in groups if isinstance(g, dict) and g.get("result_type") == "video")}
    output["retained_google_evidence"] = google_evidence(Path(__file__).resolve().parent.parent, root)
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


def expander_without_network(query, cls):
    return cls(use_live_suggestions=False).expand(query, max_variants=5)


def google_evidence(project, prototype):
    folder = project / "data/reverse/google-search"
    if not (folder / "session-evidence.json").is_file() or not (folder / "analysis-evidence.json").is_file():
        return {"available": False}
    capture = json.loads((folder / "session-evidence.json").read_text(encoding="utf-8-sig"))
    analysis = json.loads((folder / "analysis-evidence.json").read_text(encoding="utf-8-sig"))
    observed = capture["records"][0]
    static = next(r for r in analysis["records"] if r.get("operation") == "analyze_javascript_application")
    paths = []
    for step in observed["normalized_result"]["steps"]:
        value = step.get("after_url", {})
        url = value.get("url", "") if isinstance(value, dict) else value
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            parsed = urlsplit(url)
            paths.append(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
    return {"available": True, "external_capture_file": str(folder / "session-evidence.json"), "capture_evidence_id": observed["evidence_id"], "capture_completeness": observed["normalized_result"]["completeness"], "final_urls_without_query": paths,
            "actual_analysis_evidence_id": static["evidence_id"], "reported_analysis_evidence_id": next((value for value in re.findall(r'ev_[a-f0-9]{64}', (prototype / 'reports/google_reverse_report.md').read_text(encoding='utf-8')) if value == static['evidence_id']), None), "static_confidence": static["confidence"], "static_statistics": static["normalized_result"]["statistics"], "static_limitations": static["limitations"],
            "javascript_files": [{"path": str(p), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in folder.rglob("*.js")]}


if __name__ == "__main__":
    main()
