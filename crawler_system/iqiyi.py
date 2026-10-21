"""iQIYI metadata and owned-session playback within the server's access limits."""
import json
import re
from urllib.parse import urlencode, urlsplit

from .errors import CrawlerError
from .network import validate_url


def _identity(url):
    validate_url(url, resolve=False)
    parsed = urlsplit(url)
    match = re.fullmatch(r'/[vw]_([0-9a-z]+)\.html', parsed.path, re.I)
    if (parsed.hostname or '').removeprefix('www.') != 'iqiyi.com' or not match or parsed.port not in {None, 80, 443}:
        raise CrawlerError('IQIYI_DETAIL_UNSUPPORTED', 'Page resource scanning required for this entry point')
    return int(match[1], 36) ^ 0x75706971676C


def public_metadata(url, network):
    identity = _identity(url)
    parsed = urlsplit(url)
    endpoint = 'https://mesh.if.iqiyi.com/player/pcw/video/playervideoinfo?' + urlencode({'id': identity, 'locale': 'zh_CN'})
    response = network.fetch(endpoint)
    try:
        payload = json.loads(response.body)
        data = payload.get('data') or {}
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, AttributeError):
        raise CrawlerError('IQIYI_DETAIL_UNAVAILABLE', 'Public video metadata API returned no valid data')
    returned = urlsplit(data.get('vu', ''))
    if payload.get('code') != 'A00000' or not isinstance(data.get('vn'), str) or not data['vn'].strip() or returned.path != parsed.path or (returned.hostname or '').removeprefix('www.') != 'iqiyi.com':
        raise CrawlerError('IQIYI_DETAIL_UNAVAILABLE', 'Public video metadata does not match input URL', 'Continue using browser inspection for this entry point')
    assets = [{'url': url, 'source_url': url, 'name': data['vn'] + ' (On-demand video parsing)', 'kind': 'media'}]
    if data.get('vpic'):
        try:
            validate_url(data['vpic'], resolve=False)
            assets.append({'url': data['vpic'], 'name': data['vn'] + ' · Cover', 'kind': 'img'})
        except CrawlerError:
            pass
    return {'kind': 'video', 'url': url, 'title': data['vn'], 'assets': assets, 'links': [], 'http_status': None,
            'extractor': 'iqiyi:public-metadata',
            'metadata': {'page_type': 'video_detail', 'tvid': identity, 'api_http_status': response.status,
                         'api_source': endpoint, 'media_url_status': 'not_extracted', 'vip_types': data.get('vipType', []),
                         'playback_authorization': 'unchecked'},
            'warnings': ['Video metadata and cover identified; full original video not yet retrieved. Membership tags do not imply verified playback permissions for the current session']}


def playback_info(url, network, *, use_session=False, max_bytes=None):
    identity = _identity(url)
    endpoint = 'https://mesh.if.iqiyi.com/player/lw/lwplay/accelerator.js?' + urlencode({
        'apiVer': 3, 'tvid': identity, 'format': 'json'})
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': url}
    if use_session:
        from .browser_session import iqiyi_playback_payload
        payload = iqiyi_playback_payload(url, network.config)
        headers['User-Agent'] = payload.get('_browser_user_agent') or headers['User-Agent']
    else:
        response = network.fetch(endpoint, headers=headers)
        try:
            payload = json.loads(response.body)
        except ValueError:
            payload = {}
    try:
        video = payload['videoInfo']
        # Read the ordinary JSON exposed by the shipped pcweb.wonder decoder.
        # Server rights and media encryption keys are never changed.
        playback = json.loads(''.join(chr(ord(c) ^ 90) for c in payload['ev']))
        data = playback['data']
        if playback['code'] != 'A00000' or any(str(value) != str(identity) for value in (
                payload['tvId'], video['tvId'], data['tvid'])):
            raise ValueError()
        title = video['title'].strip()
        streams = data['program']['video']
        if not title or not isinstance(streams, list):
            raise ValueError()
        streams = [row for row in streams if isinstance(row, dict)]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CrawlerError('IQIYI_PLAYBACK_UNAVAILABLE', 'The new API did not return valid playback metadata for this video; full original video not yet retrieved', 'Verify that the video plays normally on the platform and retry; membership tags do not prove current playback permissions')
    duration = max((row['duration'] for row in streams if isinstance(row.get('duration'), (int, float)) and 0 < row['duration'] < 604800), default=0)
    boss = data.get('boss_ts') if isinstance(data.get('boss_ts'), dict) else {}
    account = boss.get('data') if isinstance(boss.get('data'), dict) else {}
    access = str(account.get('prv'))
    if access == '1':
        seconds = boss.get('previewTime')
        multiplier = {'1': 60, '3': 1}.get(str(boss.get('previewType')))
        seconds = int(seconds * multiplier) if isinstance(seconds, (int, float)) and multiplier and 0 < seconds < 604800 else None
        period = f' {seconds}s' if seconds else ''
        total = f'; total duration {int(duration)}s' if duration else ''
        raise CrawlerError('VIDEO_PREVIEW_ONLY', f'"{title}" only authorized {period} preview{total} for this request; full original video was not downloaded', 'Open dedicated Edge in settings, log in with an account having access to this video, close the window and retry; previews and ads are not saved as full original videos')
    if access != '0':
        raise CrawlerError('VIDEO_ACCESS_UNVERIFIED', 'The new API did not confirm full playback permissions; full original video was not downloaded', 'Verify that the current session has full playback access on the platform and retry')
    protected = False
    oversized = []
    for row in sorted(streams, key=lambda entry: (bool(entry.get('_selected')),
                      entry.get('bid') if isinstance(entry.get('bid'), (int, float)) else -1), reverse=True):
        # The shipped player labels -1/1 as clear; ispre is not the account grant.
        if str(row.get('drmType')) in {'7', '8', '9'} or row.get('eak'):
            protected = True
            continue
        if str(row.get('drmType')) not in {'-1', '0', '1'}:
            continue
        for key in ('m3u8Url', 'mpdUrl', 'ml'):
            manifest = row.get(key)
            if isinstance(manifest, str) and manifest:
                validate_url(manifest)
                declared = row.get('vsize')
                if max_bytes is not None and isinstance(declared, (int, float)) and declared > max_bytes:
                    oversized.append(declared)
                    continue
                suffix = row.get('ms') or ''
                if not isinstance(suffix, str) or len(suffix) > 8192 or any(ord(c) < 32 for c in suffix):
                    continue
                return {'manifest_url': manifest, 'title': title, 'duration': duration,
                        'id': str(identity), 'webpage_url': url, 'http_headers': headers,
                        'extra_param_to_segment_url': suffix, 'bid': row.get('bid'), 'declared_bytes': row.get('vsize')}
    if oversized:
        raise CrawlerError('SIZE_LIMIT', f'Available full version is at least ~{min(oversized) / 1024 / 1024:.1f} MB, exceeding current limit', 'Increase the single file download limit in settings and retry')
    if protected:
        raise CrawlerError('VIDEO_DRM_PROTECTED', 'Current full authorized version is DRM-protected; no unencrypted version supported by this program was found', 'Use playback or offline caching provided by the platform')
    raise CrawlerError('IQIYI_FORMAT_UNSUPPORTED', 'New player recognized, but platform did not return a complete unencrypted media playlist supported by this program', 'Use platform playback or caching features; fragments, ads, or unknown encrypted formats are not saved as original videos')
