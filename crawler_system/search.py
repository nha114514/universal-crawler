"""Search routing using local protocol parsers and installed libraries."""
from .config import settings
from .errors import CrawlerError
from .platforms import parse_prefix
from .regions import check_scope, scoped_query


def search_web(query, limit=8, region='all', search_kind='web'):
    from .discovery import engine_search, verified_batch
    from .reconstruction import prioritize
    check_scope(query, region)
    parsed = parse_prefix(query)
    if parsed and region != 'all' and parsed[0]['region'] != region:
        raise CrawlerError('SOURCE_SCOPE_MISMATCH', 'Specified site does not match selected source scope', 'Change source scope or site filter')
    if parsed and parsed[0]['id'] in {'bilibili', 'youtube'}:
        if parsed[0]['id'] == 'bilibili':
            return {'items': engine_search('bilibili', parsed[1], limit), 'errors': []}
        from .video import search_video
        return search_video(parsed[1], limit)
    effective = parsed[1] + ' site:' + parsed[0]['domains'][0] if parsed else scoped_query(query, region, search_kind, settings().get('source_region_overrides', {}))
    errors = []
    for engine in settings().get('search_engines', ['google', 'bing', 'duckduckgo']):
        try:
            rows = engine_search(engine, effective, limit)
            rows = verified_batch(rows, {'query': effective, 'topic': parsed[1] if parsed else query})
            for row in rows:
                row.setdefault('metadata', {}).update(query=query, search_region=region, search_scope='public_index')
            if rows:
                return {'items': prioritize(rows), 'errors': errors}
        except CrawlerError as error:
            errors.append({'channel': engine, **error.as_dict()})
    return {'items': [], 'errors': errors}
