"""Bounded, bilingual public discovery through the selected crawler providers."""
import base64
from collections import deque
import copy
import json
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .config import settings
from .errors import CrawlerError
from .network import Network, validate_url
from .platforms import catalog, parse_prefix, platform_for_url, validate_platforms
from .regions import classify_source, check_scope
from .store import canonical_url

ALIASES = {'海龟汤': ['lateral thinking puzzles', 'situation puzzles'],
           '横向思维': ['lateral thinking'], '人工智能': ['artificial intelligence'],
           '机器学习': ['machine learning'], '深度学习': ['deep learning']}


def translate_query(query, target, network=None):
    network = network or Network()
    response = network.fetch('https://translate.googleapis.com/translate_a/single?' + urlencode(
        {'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'q': query}))
    try:
        text = ''.join(part[0] or '' for part in json.loads(response.body)[0]).strip()
    except (ValueError, TypeError, IndexError):
        raise CrawlerError('TRANSLATION_UNAVAILABLE', 'Translation did not return available text', 'You can manually provide English related keywords')
    if not text:
        raise CrawlerError('TRANSLATION_UNAVAILABLE', 'Translation result is empty', 'You can manually provide English related keywords')
    return text


def expand_queries(query, english_query='', translator=None, network=None):
    parsed = parse_prefix(query)
    text = parsed[1] if parsed else query.strip()
    if not text:
        raise CrawlerError('EMPTY_PLATFORM_QUERY', 'Platform keyword cannot be empty', 'Provide topic after platform prefix')
    sites = re.findall(r'(?<!\S)(-?site:\S+)', text)
    topic = re.sub(r'(?<!\S)-?site:\S+', '', text).strip() or text
    suffix = ' site:' + parsed[0]['domains'][0] if parsed else (' ' + ' '.join(sites) if sites else '')
    variants, errors = [], []
    def add(value, origin):
        value = value.strip()
        if not value:
            return
        scoped = value + suffix
        if not any(entry['query'] == scoped for entry in variants):
            variants.append({'query': scoped, 'language': 'zh' if re.search(r'[\u3400-\u9fff]', value) else 'en', 'origin': origin})
    add(topic, 'original')
    add(english_query, 'user_related')
    from .reconstruction import expand
    for variant in expand(topic, network):
        add(variant['query'], variant['source'])
    if translator:
        try:
            add(translator(topic, 'en' if re.search(r'[\u3400-\u9fff]', topic) else 'zh-CN'), 'translation')
        except Exception as error:
            errors.append({'channel': 'translation', **error_dict(error)})
    return {'variants': variants[:5], 'errors': errors}


def query_plan(variants, platforms=None, region='all', mode='auto'):
    selected = validate_platforms(platforms or [])
    rows = [row for row in catalog(region, mode) if not selected or row['id'] in selected]
    # Interleave domestic/foreign batches. Every directory entry is in the plan;
    # an execution budget can leave later entries explicitly not_run.
    batches = []
    for offset in range(0, max(sum(row['region'] == scope for row in rows) for scope in ('domestic', 'foreign')) or 1, 8):
        for scope in ('domestic', 'foreign'):
            batch = [row for row in rows if row['region'] == scope][offset:offset + 8]
            if batch:
                batches.append(batch)
    plan, seen, variant_plans = [], set(), []
    for variant in variants:
        variant_plan = []
        query = variant['query']
        restricted = bool(re.search(r'(?<!\S)site:', query))
        hint = ' (paper OR 论文 OR PDF)' if mode == 'paper' else ' (video OR 视频 OR podcast)' if mode == 'video' else ''
        entries = [(query + hint, selected, 'platform' if selected or restricted else 'global')] if not selected or restricted else []
        if not restricted:
            for batch in batches:
                domains = [row['domains'][0] for row in batch]
                entries.append((query + hint + ' (' + ' OR '.join('site:' + domain for domain in domains) + ')', [row['id'] for row in batch], 'platform'))
        for effective, keys, scope in entries:
            if effective not in seen:
                variant_plan.append({'query': effective, 'topic': query, 'language': variant.get('language', ''), 'platforms': keys, 'scope': scope})
                seen.add(effective)
        variant_plans.append(deque(variant_plan))
    while any(variant_plans):
        for queue in variant_plans:
            if queue:
                plan.append(queue.popleft())
    return plan


def decode_search_url(url):
    try:
        parsed = urlsplit(url)
        values = parse_qs(parsed.query)
        if (parsed.hostname or '').endswith('bing.com') and parsed.path.startswith('/ck/'):
            value = values.get('u', [''])[0]
            if value.startswith('a1'):
                value = value[2:]
                url = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)).decode('utf-8')
        elif (parsed.hostname or '') in {'duckduckgo.com', 'lite.duckduckgo.com'} and values.get('uddg'):
            url = values['uddg'][0]
        if urlsplit(url).username or urlsplit(url).password:
            return None
        validate_url(url, resolve=False)
        return url
    except (ValueError, UnicodeError, CrawlerError):
        return None


def kind_for_url(url):
    parsed = urlsplit(url)
    host, path = (parsed.hostname or '').lower(), parsed.path
    def on(domain):
        return host == domain or host.endswith('.' + domain)
    if path.lower().endswith('.pdf'):
        return 'pdf'
    if (on('bilibili.com') and '/video/' in path or on('douyin.com') and '/video/' in path
        or host in {'youtu.be', 'www.youtube.com', 'youtube.com'} and (host == 'youtu.be' or path.startswith(('/watch', '/shorts/')))
        or on('tiktok.com') and '/video/' in path or on('dailymotion.com') and '/video/' in path
        or on('vimeo.com') and re.match(r'^/\d+(?:$|/)', path) or on('twitch.tv') and '/videos/' in path):
        return 'video'
    return 'web'


def engine_search(engine, query, limit, network=None):
    network = network or Network()
    if engine in {'google', 'bing', 'bilibili'}:
        from .reconstruction import search
        rows = search(engine, query, limit, network)
        for row in rows:
            row['kind'] = kind_for_url(row['url'])
        return rows
    if engine != 'duckduckgo':
        raise CrawlerError('SEARCH_ENGINE_UNSUPPORTED', 'Search entry point not configured')
    from bs4 import BeautifulSoup
    response = network.fetch('https://html.duckduckgo.com/html/?' + urlencode({'q': query}), allow_errors=True)
    soup = BeautifulSoup(response.body, 'html.parser')
    if response.status >= 400 or soup.select_one('form#challenge-form, .anomaly-modal'):
        raise CrawlerError('WEB_SEARCH_BLOCKED', 'DuckDuckGo requires verification or denied access', 'Try another search entry point')
    rows = []
    for entry in soup.select('.result'):
        anchor = entry.select_one('a.result__a[href]')
        if not anchor:
            continue
        raw = anchor['href']
        url = decode_search_url('https:' + raw if raw.startswith('//') else raw)
        if url:
            snippet = entry.select_one('.result__snippet')
            rows.append({'url': url, 'title': anchor.get_text(' ', strip=True) or url, 'kind': kind_for_url(url),
                         'abstract': snippet.get_text(' ', strip=True) if snippet else '', 'text_status': 'search_snippet',
                         'extractor': 'scrapling:duckduckgo'})
    return rows[:limit]


def verified_batch(rows, entry):
    """Reject ignored operators, engine ads and wholly unrelated results."""
    sites = re.findall(r'(?<!-)site:([a-z0-9.-]+)', entry['query'], re.I)
    def on_site(url):
        host = (urlsplit(url).hostname or '').lower()
        return not sites or any(host == site.lower() or host.endswith('.' + site.lower()) for site in sites)
    public = [row for row in rows if (urlsplit(row.get('url','')).hostname or '').lower() not in
              {'bing.com','www.bing.com','duckduckgo.com','lite.duckduckgo.com'}]
    scoped = [row for row in public if on_site(row['url'])]
    if public and sites and not scoped:
        raise CrawlerError('SEARCH_SCOPE_IGNORED', 'Search results did not comply with site restriction; rejected these candidates', 'Try another search entry point or input public URL directly')
    topic = re.sub(r'(?<!\S)-?site:\S+', '', entry.get('topic', entry['query']))
    terms = [term for term in re.findall(r'[a-z0-9]+|[\u3400-\u9fff]+', topic.casefold())
             if term not in {'or','and','not','the','a','an','of','to','in','for','with','site','pdf','paper','video'}]
    # One observed term is sufficient; this is not the former all-terms exact
    # title filter. Expand variants to discover related concepts/languages.
    related = [row for row in scoped if not terms or any(term in (' '.join(str(row.get(field) or '') for field in ('title','abstract','url'))).casefold() for term in terms)]
    if rows and not related:
        raise CrawlerError('SEARCH_QUERY_UNRELIABLE', 'Search only returned ads, redirects, or wholly unrelated content', 'Use another entry point or add related keywords')
    return related


def merge_results(groups, query, limit):
    queues = [deque(group) for group in groups]
    merged, order = {}, []
    while any(queues):
        for queue in queues:
            if not queue:
                continue
            item = copy.deepcopy(queue.popleft())
            url = decode_search_url(item.get('url', ''))
            if not url:
                continue
            item['url'] = url
            key = canonical_url(url)
            metadata = item.setdefault('metadata', {})
            metadata['collection_queries'] = list(dict.fromkeys([*metadata.get('collection_queries', []), query]))
            platform = platform_for_url(url)
            if platform:
                metadata['source_platform'] = platform['id']
            if key not in merged:
                merged[key] = item
                order.append(key)
            else:
                previous = merged[key].setdefault('metadata', {})
                previous['search_origins'] = [*previous.get('search_origins', []), *[origin for origin in metadata.get('search_origins', []) if origin not in previous.get('search_origins', [])]]
                previous['collection_queries'] = list(dict.fromkeys([*previous.get('collection_queries', []), *metadata['collection_queries']]))
    primary, repeats, hosts = [], [], set()
    for key in order:
        host = (urlsplit(merged[key]['url']).hostname or '').removeprefix('www.')
        (repeats if host in hosts else primary).append(merged[key])
        hosts.add(host)
    from .reconstruction import prioritize
    return prioritize([*primary, *repeats][:limit])


def error_dict(error):
    return error.as_dict() if isinstance(error, CrawlerError) else {'code': 'SEARCH_FAILED', 'message': str(error)[:300], 'action': 'Check channel status and retry'}


def search_broad(query, limit=60, mode='auto', network=None, region='all', platforms=None,
                 english_query='', bilingual=True, request_budget=24):
    check_scope(query, region)
    selected = validate_platforms(platforms or [])
    parsed = parse_prefix(query)
    if parsed:
        selected = [parsed[0]['id']]
    selected_rows = [row for row in catalog() if row['id'] in selected]
    if region != 'all' and any(row['region'] != region for row in selected_rows):
        raise CrawlerError('SOURCE_SCOPE_MISMATCH', 'Specified platform does not match source scope', 'Change source scope or platform')
    network = network or Network()
    translator = (lambda text, target: translate_query(text, target, network)) if bilingual and not english_query and re.search(r'[\u3400-\u9fff]', query) else None
    expanded = expand_queries(query, english_query, translator, network)
    plan = query_plan(expanded['variants'], selected, region, mode)
    groups, errors, channels, disabled = [], expanded['errors'], [], set()
    bad_scopes, bad_languages = set(), set()
    native = [row for row in selected_rows if row['id'] in {'bilibili', 'youtube'}]
    if not selected and mode in {'auto', 'video'}:
        native = [row for row in catalog(region) if row['id'] in {'bilibili', 'youtube'}]
    web_budget = max(0, request_budget - len(native))
    attempts = 0
    for index, entry in enumerate(plan):
        entry['state'] = 'not_run'
        for engine in settings().get('search_engines', ['google', 'bing', 'duckduckgo']):
            if (engine in disabled or engine in bad_scopes and entry['scope'] == 'platform'
                    or (engine,entry['language']) in bad_languages or attempts >= web_budget):
                continue
            attempts += 1
            try:
                rows = engine_search(engine, entry['query'], min(20, max(8, limit)), network)
                rows = verified_batch(rows, entry)
                for row in rows:
                    row.setdefault('metadata', {}).setdefault('search_origins', []).append({'engine': engine, **{k: v for k, v in entry.items() if k != 'state'}})
                groups.append(rows)
                entry['state'] = 'completed' if rows else 'empty'
                channels.append({'channel': engine, 'query': entry['query'], 'platforms': entry['platforms'], 'state': entry['state'], 'found': len(rows)})
                if rows and entry['scope'] != 'global':
                    break
            except Exception as error:
                details = {'channel': engine, 'query': entry['query'], **error_dict(error)}
                errors.append(details)
                channels.append({**details, 'platforms': entry['platforms'], 'state': 'failed', 'found': 0})
                entry['state'] = 'failed'
                if details['code'] == 'SEARCH_SCOPE_IGNORED':
                    bad_scopes.add(engine)
                elif details['code'] == 'SEARCH_QUERY_UNRELIABLE':
                    bad_languages.add((engine,entry['language']))
                else:
                    disabled.add(engine)
    # Native protocols use in-process libraries; other platforms use the public index plan above.
    for row in native:
        if attempts >= request_budget:
            channels.append({'channel': row['id'], 'state': 'not_run', 'found': 0})
            continue
        attempts += 1
        topic = parsed[1] if parsed else (english_query or query) if row['region'] == 'foreign' else query
        try:
            if row['id'] == 'bilibili':
                result = {'items': engine_search('bilibili', topic, min(20, max(4, limit // 4)), network), 'errors': []}
            else:
                from .video import search_video
                result = search_video(topic, min(20, max(4, limit // 4)))
            candidates = result['items']
            for item in candidates:
                item['kind'] = kind_for_url(item['url'])
                item.setdefault('metadata', {}).setdefault('search_origins', []).append({'engine': row['id'], 'query': topic, 'platforms': [row['id']], 'scope': 'native'})
            groups.append(candidates)
            channels.append({'channel': row['id'], 'state': 'completed' if candidates else 'empty', 'found': len(candidates)})
            errors.extend(result.get('errors', []))
        except Exception as error:
            details = {'channel': row['id'], **error_dict(error)}
            errors.append(details)
            channels.append({**details, 'state': 'failed', 'found': 0})
    if not selected and mode in {'auto', 'paper'} and region != 'domestic':
        from .papers import search_papers
        try:
            result = search_papers(english_query or query, min(20, max(3, limit // 6)), network)
            groups.append(result['items'])
            errors.extend(result.get('errors', []))
            channels.append({'channel': 'scholarly-apis', 'state': 'partial' if result.get('errors') else 'completed', 'found': len(result['items'])})
        except Exception as error:
            errors.append({'channel': 'scholarly-apis', **error_dict(error)})
    overrides = settings().get('source_region_overrides', {})
    excluded, unknown = 0, 0
    filtered = []
    for group in groups:
        accepted = []
        for item in group:
            source = classify_source(item['url'], overrides)
            if selected and not any(any((urlsplit(item['url']).hostname or '') == domain or (urlsplit(item['url']).hostname or '').endswith('.' + domain) for domain in row['domains']) for row in selected_rows):
                excluded += 1
            elif region != 'all' and source != region:
                unknown += source == 'unknown'
                excluded += source != 'unknown'
            else:
                accepted.append(item)
        filtered.append(accepted)
    items = merge_results(filtered, query, min(200, limit))
    if not items and not errors:
        errors.append({'code': 'SEARCH_EMPTY', 'message': 'Public search yielded no records matching source scope',
                       'action': 'Try related keywords, all sources, or input public URLs directly; unclassified sites can be assigned source attribution in settings'})
    return {'items': items, 'errors': errors,
            'scope': {'region': region, 'matched': len(items), 'excluded': excluded, 'unknown': unknown},
            'discovery': {'variants': expanded['variants'], 'plan': plan, 'channels': channels,
                          'requests': attempts, 'request_budget': request_budget,
                          'not_run': sum(entry['state'] == 'not_run' for entry in plan),
                          'note': 'Platform directory is used for public index discovery; actual results and limits are based on channel records for this run'}}
