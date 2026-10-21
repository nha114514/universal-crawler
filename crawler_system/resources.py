"""Find resource links; bodies, PDF text and captions are not extracted."""
import re
from urllib.parse import urlsplit

from .network import Network, validate_url
from .errors import CrawlerError
from .parsers import FILE_EXTENSIONS, file_extension, listing_platform, parse_html, video_content_link


def resource_only(item):
    item.update(content='', abstract='', subtitles=[], subtitle_text='', subtitle_status='unchecked', text_status='resources')
    item.setdefault('metadata', {})['resources_only'] = True
    if item.get('kind') in {'video', 'audio'} and item.get('url') and not any(a.get('kind') in {'media', 'video', 'audio', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg'} for a in item.get('assets', [])):
        try:
            validate_url(item['url'], resolve=False)
            item.setdefault('assets', []).insert(0, {'url': item['url'], 'source_url': item['url'], 'kind': 'media', 'name': (item.get('title') or 'Media') + ' (Parse on demand)'})
            item['metadata']['media_url_status'] = 'not_extracted'
        except CrawlerError:
            item.setdefault('warnings', []).append('Media entry invalid, download option not added')
    item['metadata'].pop('page_text_unverified', None)
    for data in item['metadata'].get('video_objects', []):
        data.pop('transcript', None)
        data.pop('description', None)
    return item


def collect_resources(value, render='auto', network=None):
    url = value.strip()
    if re.match(r'^(?:doi:\s*)?10\.\d{4,9}/', url, re.I):
        url = 'https://doi.org/' + re.sub(r'^doi:\s*', '', url, flags=re.I)
    validate_url(url)
    if render in {'auto', 'always'} and (urlsplit(url).hostname or '').removeprefix('www.') == 'netflix.com':
        render = 'session'
    network = network or Network()
    network.config['request_timeout'] = min(10, network.config['request_timeout'])
    network.config.update(browser_timeout_ms=15000, browser_retries=1)
    if render in {'auto', 'never'} and video_content_link(url, 'iqiyi') and re.match(r'^/[vw]_', urlsplit(url).path):
        from .iqiyi import public_metadata
        try:
            return {'items': [resource_only(public_metadata(url, network))], 'errors': [], 'needs_browser': False}
        except CrawlerError as error:
            if error.code in {'PRIVATE_URL', 'INVALID_URL', 'SIZE_LIMIT'} or render == 'never':
                raise
    extension = file_extension(url)
    if extension in FILE_EXTENSIONS:
        response = network.fetch(url, method='HEAD')
    elif render == 'session':
        from .browser_session import session_response
        response = session_response(url)
    else:
        try:
            response = network.fetch(url, render=render == 'always')
        except CrawlerError as error:
            if render == 'auto' and error.code in {'LOGIN_OR_BLOCKED', 'NETWORK_FAILURE', 'HTTP_ERROR'}:
                return {'items': [], 'errors': [], 'needs_browser': True, 'static_attempt_error': error.as_dict()}
            raise
    content_type = response.headers.get('content-type', '').lower()
    if 'html' in content_type or response.body.lstrip()[:200].lower().startswith((b'<html', b'<!doctype html', b'<title')):
        item = parse_html(response, resources_only=True)
        from .web import login_page, verification_page
        if login_page(item) or verification_page(item):
            code = 'WEB_LOGIN_REQUIRED' if login_page(item) else 'WEB_VERIFICATION_REQUIRED'
            return {'items': [], 'errors': [{'code': code, 'message': 'Collection returned a login or verification page; resources not yet obtained',
                    'action': 'Verify dedicated Edge and logged-in page share the same site and session; keep window open and retry',
                    'browser_session': response.render_diagnostics.get('browser_session')}], 'needs_browser': render == 'auto'}
        # Empty image URLs can resolve back to the player page; they are not files.
        item['assets'] = [a for a in item.get('assets', []) if a.get('kind') not in {'img', 'png', 'jpg'} or a['url'] != item['url']]
        if video_content_link(url, 'iqiyi') and item['metadata'].get('page_type') != 'video_detail':
            item['assets'] = []
        from .platforms import platform_for_url
        source = platform_for_url(response.url)
        if source and source['category'] == 'video' and video_content_link(response.url, source['id']) and not listing_platform(response.url) and source['id'] != 'iqiyi' and not any(a['kind'] in {'media', 'video', 'mp4', 'audio'} for a in item['assets']):
            item['assets'].insert(0, {'url': response.url, 'source_url': response.url, 'name': item['title'] + ' (Parse video on demand)', 'kind': 'media'})
            item['kind'] = 'video'
            item['metadata']['media_url_status'] = 'not_extracted'
        needs_browser = render == 'auto' and (bool(listing_platform(url)) or not item.get('entries') and (
            item['metadata']['html_quality']['has_app_root'] or
            item['metadata']['html_quality']['has_scripts'] and not any(a['kind'] in {'media', 'video', 'audio', 'mp4', 'pdf'} for a in item['assets'])))
        if item['metadata'].get('page_type') == 'video_detail':
            needs_browser = False
        if not needs_browser and not item.get('assets') and not item.get('entries'):
            item['assets'] = [{'url': response.url, 'name': (item['title'] or 'webpage') + '.html', 'kind': 'html'}]
        item['metadata'].update(response.render_diagnostics)
        if render == 'session':
            item['extractor'] = 'scrapling:edge-session'
    else:
        manifest = extension in {'m3u8', 'mpd'} or any(value in content_type for value in ('mpegurl', 'dash+xml'))
        kind = 'video' if manifest else 'pdf' if 'pdf' in content_type or extension == 'pdf' else 'video' if content_type.startswith('video/') else 'audio' if content_type.startswith('audio/') else 'file'
        item = resource_only({'kind': kind, 'url': response.url, 'title': urlsplit(response.url).path.rsplit('/', 1)[-1] or url,
                              'assets': [{'url': response.url, 'name': 'Streaming Manifest (Parse on demand)' if manifest else 'Original File', 'kind': 'media' if manifest else extension if extension in FILE_EXTENSIONS else kind}],
                              'links': [], 'warnings': [], 'extractor': 'resource-headers', 'http_status': response.status})
        if manifest:
            item['metadata']['media_url_status'] = 'manifest'
        needs_browser = False
    if render != 'auto' and needs_browser:
        needs_browser = False
    return {'items': [item] if item.get('assets') or item.get('entries') or not needs_browser else [], 'errors': [], 'needs_browser': needs_browser}
