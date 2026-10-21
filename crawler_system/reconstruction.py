"""Reuse the delivered search project with this application's selected transport."""
from functools import lru_cache
import importlib
import re
from urllib.parse import urlencode, urljoin, urlsplit
import warnings

from bs4 import BeautifulSoup
import httpx

from .errors import CrawlerError
from .network import Network, validate_url


@lru_cache(maxsize=None)
def source(component):
    return importlib.import_module('.search_logic.' + component, package='crawler_system')


class SelectedClient:
    def __init__(self, network, engine, headers):
        self.network, self.engine = network, engine
        self.headers = {k: v for k, v in headers.items() if k.lower() != 'cookie'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, params=None):
        if params:
            url += ('&' if '?' in url else '?') + urlencode(params)
        response = self.network.fetch(url, headers=self.headers, allow_errors=True)
        if self.engine == 'google' and urlsplit(url).path == '/search':
            text = response.body.decode('utf-8', errors='replace')
            if 'enablejs' in text or '/sorry/' in response.url:
                response = self.network.fetch(url, render=True, allow_errors=True)
            if '/sorry/' in response.url or 'unusual traffic' in response.body.decode('utf-8', errors='replace').lower():
                from .browser_session import session_response
                response = session_response(url)
            soup = BeautifulSoup(response.body, 'html.parser')
            for tag in soup.select('script,style'):
                tag.decompose()
            for anchor in soup.select('a[href]'):
                anchor['href'] = urljoin(response.url, anchor['href'])
            response.body = str(soup).encode()
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}}
        return httpx.Response(response.status, content=response.body, headers=headers,
                              request=httpx.Request('GET', response.url))


def adapter(engine, network=None):
    network = network or Network()
    module = source('adapters.' + engine)
    cls = {'google': 'GoogleAdapter', 'bing': 'BingAdapter', 'bilibili': 'BilibiliAdapter'}[engine]
    instance = getattr(module, cls)()
    instance._client = lambda: SelectedClient(network, engine, instance.headers)
    if engine == 'bilibili':
        original_key = instance.get_mixin_key
        def live_key():
            # A failed live key must not silently become the prototype's fixed key.
            with warnings.catch_warnings():
                warnings.simplefilter('error', RuntimeWarning)
                return original_key()
        instance.get_mixin_key = live_key
    return instance


def search(engine, query, limit, network=None):
    instance = adapter(engine, network)
    market = {'market': 'en', 'country': 'US'} if engine == 'bing' and not re.search(r'[\u3400-\u9fff]', query) else {}
    outcome = instance.search(query, limit=min(40, limit * 2), **market)
    if outcome.status.value in {'failed', 'verification_required'}:
        raise CrawlerError(outcome.error_code or 'SEARCH_FAILED', outcome.message, 'Complete site verification or check this search channel, then retry')
    rows, seen = [], set()
    for item in outcome.items:
        try:
            validate_url(item.url, resolve=False)
        except CrawlerError:
            continue
        if item.url in seen:
            continue
        seen.add(item.url)
        row = item.to_dict()
        for key in ('id', 'created_at', 'updated_at'):
            row.pop(key, None)
        row['subtitle_status'] = 'unchecked'
        row['extractor'] = 'search-logic:' + engine
        row['metadata'].update(reconstruction_source=source('adapters.' + engine).__file__,
                               search_origins=row.pop('search_origins'))
        rows.append(row)
    return rows[:limit]


def expand(query, network=None):
    expander = source('query.expansion').QueryExpander(use_live_suggestions=network is not None)
    if network is not None:
        expander.google_adapter = adapter('google', network)
        expander.bing_adapter = adapter('bing', network)
    return expander.expand(query, max_variants=5)


def prioritize(rows):
    if not rows:
        return rows
    models = source('models')
    candidates = []
    for index, row in enumerate(rows):
        origins = row.get('metadata', {}).get('search_origins', [])
        links = [link.get('url', '') if isinstance(link, dict) else link for link in row.get('links', [])]
        distinct_origins = {o.get('engine', '').removesuffix('-wbi'): o for o in origins}
        candidates.append(models.CandidateItem(id=str(index), url=row['url'], title=row.get('title', ''), kind=row.get('kind', 'web'),
                           links=[link for link in links if isinstance(link, str) and link.startswith(('http://', 'https://'))],
                           search_origins=[models.SearchOrigin(engine=o.get('engine', ''), query=o.get('query', ''), raw_rank=o.get('raw_rank', 0)) for o in distinct_origins.values()]))
    ranked = source('pipeline.ranking').prioritize_candidates(candidates)
    result = []
    for candidate in ranked:
        row = rows[int(candidate.id)]
        row.setdefault('metadata', {}).update(candidate.metadata, crawl_priority=candidate.crawl_priority)
        result.append(row)
    return result
