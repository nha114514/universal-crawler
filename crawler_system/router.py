from collections import deque
from functools import lru_cache
import re
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser

from .errors import CrawlerError
from .search import search_web
from .network import Network, validate_url
from .papers import collect_paper, search_papers
from .parsers import clean_text, video_content_link
from .store import canonical_url
from .web import collect_url
from .regions import classify_source, check_scope
from .config import settings


def classify(value):
    value = value.strip()
    if re.match(r"^(?:doi:\s*)?10\.\d{4,9}/\S+$", value, re.I) or re.match(r"^https?://(?:dx\.)?doi\.org/", value, re.I):
        return "doi"
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"}:
        if host in {"arxiv.org", "www.arxiv.org"} and parsed.path.startswith(("/abs/", "/pdf/")):
            return "arxiv"
        return "url"
    if "://" in value or parsed.scheme in {"file", "data", "javascript", "mailto"}:
        return "url"  # The public-URL guard rejects unsupported schemes before retrieval.
    return "query"


@lru_cache(maxsize=1)
def video_extractors():
    from yt_dlp.extractor import gen_extractor_classes
    return [extractor for extractor in gen_extractor_classes() if extractor.ie_key() != "Generic"]


def known_video(url):
    if urlsplit(url).path.lower().endswith((".mp4", ".webm", ".mov", ".m4a", ".mp3", ".wav", ".ogg", ".m3u8")):
        return True
    for extractor in video_extractors():
        try:
            if extractor.suitable(url):
                return True
        except Exception:
            continue
    return False


def adapter_fallback(value, mode, network, render, error):
    if error.code in {'PRIVATE_URL', 'INVALID_URL', 'SIZE_LIMIT'}:
        raise error
    try:
        item = collect_url(value, mode, network, render)
    except CrawlerError as page_error:
        # Keep the provider's useful login/bridge diagnosis when both routes fail.
        raise CrawlerError(error.code, f'{error}; public webpage collection also incomplete: {page_error}', error.action) from page_error
    item.setdefault('metadata', {}).setdefault('adapter_errors', []).append(error.as_dict())
    item.setdefault('warnings', []).append(f'Dedicated adapter incomplete, continued with public webpage: {error}')
    if not item.get('retrieval_error'):
        item['retrieval_error'] = error.as_dict()
    return item


def collect(value, mode="auto", render="auto", network=None):
    network = network or Network()
    value = value.strip()
    route = classify(value)
    if route == "query":
        return search(value, mode, 8, network)
    if route in {"doi", "arxiv"}:
        if route == "arxiv" or value.startswith(("http://", "https://")):
            validate_url(value)
        return collect_paper(value, network)
    validate_url(value)
    if render == 'session':
        return collect_url(value, mode, network, render)
    if mode in {'auto', 'video'} and video_content_link(value, 'iqiyi') and not urlsplit(value).path.startswith('/a_'):
        # This player extractor timed out before public details could be read.
        # Metadata collection and on-request media download are separate paths.
        return collect_url(value, mode, network, render)
    host = (urlsplit(value).hostname or "").lower().removeprefix("www.")
    if mode == "video" or (mode == "auto" and known_video(value)):
        from .video import collect_video
        try:
            return collect_video(value, network)
        except CrawlerError as error:
            if mode == "video":
                raise
            return adapter_fallback(value, mode, network, render, error)
    item = collect_url(value, mode, network, render)
    if mode == "auto" and not item.get('content') and item.get("kind") not in {"playlist", "video", "audio"} and any(asset["kind"] in {"mp4", "webm", "mp3", "m4a", "video", "audio", "source"} for asset in item.get("assets", [])):
        from .video import collect_video
        try:
            return collect_video(value, network)
        except CrawlerError as error:
            item.setdefault("warnings", []).append(f"Page contains media, video extraction currently incomplete: {error}")
    return item


def filter_source_scope(result, region, limit, overrides):
    items, excluded, unknown = [], 0, 0
    for item in result.get('items', []):
        actual = classify_source(item['url'], overrides)
        if actual == region:
            items.append(item)
        elif actual == 'unknown':
            unknown += 1
        else:
            excluded += 1
    errors = list(result.get('errors', []))
    if not items and not errors:
        errors.append({'code': 'SOURCE_SCOPE_EMPTY', 'message': 'No results confirmed to belong to the selected scope were found',
                       'action': 'Try all sources, or set classifications for uncategorized sites first'})
    return {**result, 'items': items[:limit], 'errors': errors,
            'scope': {'region': region, 'matched': len(items), 'excluded': excluded, 'unknown': unknown}}


def search(query, mode="auto", limit=8, network=None, region='all', strategy='focused', platforms=None,
           english_query='', bilingual=True, request_budget=24):
    check_scope(query, region)
    from .platforms import parse_prefix
    platform = parse_prefix(query)
    if strategy == 'broad' or platforms or (platform and (not platform[0]['native_prefix'] or platform[0]['id'] == 'bilibili')):
        from .discovery import search_broad
        return search_broad(query, limit, mode, network, region, platforms, english_query, bilingual, request_budget)
    network = network or Network()
    overrides = settings().get('source_region_overrides', {})
    if region == 'domestic':
        # Existing scholarly APIs and ytsearch discover overseas sources. For a
        # domestic scope use the same selected web/platform backend with site hints.
        result = search_web(query, min(20, max(limit * 2, 8)), region=region, search_kind=mode)
        return filter_source_scope(result, region, limit, overrides)
    if mode == "paper":
        result = search_papers(query, limit, network)
        return filter_source_scope(result, region, limit, overrides) if region != 'all' else result
    if mode == "video":
        from .video import search_video
        result = search_video(query, limit)
        return filter_source_scope(result, region, limit, overrides) if region != 'all' else result
    if mode == "web" or re.match(r"^(?:xhs|小红书|bili|bilibili|B站|dy|抖音|ks|快手|wb|微博|tieba|贴吧|zhihu|知乎|youtube|yt|twitter|x)[:：]", query):
        result = search_web(query, limit) if region == 'all' else search_web(query, min(20, max(limit * 2, 8)), region=region)
        return filter_source_scope(result, region, limit, overrides) if region != 'all' else result
    from .video import search_video
    per_channel = max(1, limit // 4)
    groups, errors = {}, []
    scope = {'region': region, 'matched': 0, 'excluded': 0, 'unknown': 0}
    web_search = lambda: search_web(query, min(20, limit * 2)) if region == 'all' else search_web(query, min(20, limit * 2), region=region)
    for name, operation in [("web", web_search), ("papers", lambda: search_papers(query, per_channel, network)), ("video", lambda: search_video(query, per_channel))]:
        try:
            result = operation()
            if region != 'all':
                result = filter_source_scope(result, region, 20, overrides)
                for key in ('matched', 'excluded', 'unknown'):
                    scope[key] += result['scope'][key]
            groups[name] = result.get("items", [])
            errors.extend(error for error in result.get('errors', []) if error.get('code') != 'SOURCE_SCOPE_EMPTY')
        except Exception as error:
            errors.append({"channel": name, **(error.as_dict() if isinstance(error, CrawlerError) else {"code": "SEARCH_FAILED", "message": str(error)[:500], "action": "Check corresponding channel and try again"})})
    # Scholarly APIs may match just part of a general topic. Do not let these
    # unrelated suggestions consume the public webpage result budget.
    terms = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query.casefold())
    papers = [item for item in groups.get("papers", []) if terms and all(
        term in (item.get("title", "") + " " + item.get("abstract", "")).casefold() for term in terms)]
    supplements = papers + groups.get("video", [])
    websites, repeated, hosts = [], [], set()
    for item in groups.get("web", []):
        host = (urlsplit(item["url"]).hostname or "").removeprefix("www.")
        (repeated if host in hosts else websites).append(item)
        hosts.add(host)
    web = websites + repeated
    primary_count = max(1, limit - len(supplements))
    items, seen = [], set()
    for item in [*web[:primary_count], *supplements, *web[primary_count:]]:
        key = canonical_url(item["url"])
        if key not in seen:
            items.append(item)
            seen.add(key)
    result = {"items": items[:limit], "errors": errors}
    if region != 'all':
        result['scope'] = scope
        if not items and not errors:
            result['errors'].append({'code': 'SOURCE_SCOPE_EMPTY', 'message': 'No results confirmed to belong to the selected scope were found', 'action': 'Try all sources, or set classifications for uncategorized sites'})
    return result


def crawl_site(url, depth=1, max_pages=20, render="auto", resources_only=False):
    validate_url(url)
    network = Network()
    host = urlsplit(url).hostname
    robots = RobotFileParser()
    errors = []
    try:
        response = network.fetch(urljoin(url, "/robots.txt"))
        robots.parse(response.body.decode("utf-8", errors="replace").splitlines())
    except CrawlerError as error:
        if "HTTP 404" not in str(error):
            errors.append({"channel": "robots", **error.as_dict()})
        robots.parse([])
    queue, visited, items = deque([(url, 0)]), set(), []
    while queue and len(visited) < max_pages:
        target, level = queue.popleft()
        key = canonical_url(target)
        if key in visited or urlsplit(target).hostname != host:
            continue
        visited.add(key)
        if not robots.can_fetch("*", target):
            errors.append({"input": target, "code": "ROBOTS_DISALLOWED", "message": "Site robots.txt disallows this page", "action": "Select a page permitted by the site"})
            continue
        try:
            if resources_only:
                from .resources import collect_resources
                result = collect_resources(target, render, network)
                if result.get('needs_browser'):
                    result = collect_resources(target, 'always', network)
                errors.extend(result.get('errors', []))
                if not result.get('items'):
                    continue
                item = result['items'][0]
            else:
                item = collect(target, "web", render, network)
            items.append(item)
            if item.get("retrieval_error"):
                errors.append({"input": target, **item["retrieval_error"]})
            if level < depth:
                for link in item.get("links", [])[:200]:
                    if urlsplit(link["url"]).hostname == host:
                        queue.append((link["url"], level + 1))
        except CrawlerError as error:
            errors.append({"input": target, **error.as_dict()})
    return {"items": items, "errors": errors, "visited_pages": len(visited)}

