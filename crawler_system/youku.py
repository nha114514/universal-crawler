"""Public homepage state and navigation observed in Youku's shipped client.

Only data literals are decoded. Scripts, preview clips and access checks are not
executed or reconstructed. Page.chunk.js 4.1.871 supplies video?vid= / video?s=.
"""
import json
import re
from urllib.parse import urlencode


def initial_state(script):
    if len(script) > 2 * 1024 * 1024:
        return None
    match = re.search(r'window\.__INITIAL_DATA__\s*=\s*', script)
    if not match:
        return None
    raw = script[match.end():].lstrip()
    if not raw.startswith('{'):
        return None
    # JSON-shaped server state also contains JavaScript undefined values. Replace
    # that token outside strings only; never eval the surrounding script.
    parts, index, quoted, escaped = [], 0, False, False
    while index < len(raw):
        char = raw[index]
        if quoted:
            parts.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
        elif char == '"':
            quoted = True
            parts.append(char)
            index += 1
        elif raw.startswith('undefined', index) and (index == 0 or not (raw[index-1].isalnum() or raw[index-1] in '_$')) and (index+9 == len(raw) or not (raw[index+9].isalnum() or raw[index+9] in '_$')):
            parts.append('null')
            index += 9
        else:
            parts.append(char)
            index += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(''.join(parts))
        return value if isinstance(value, dict) else None
    except (ValueError, RecursionError):
        return None


def public_entries(soup):
    state = next((value for script in soup.select('script')[:50]
                  for value in [initial_state(script.string or script.get_text())] if value), None)
    if not state:
        return []
    rows = {}
    modules = state.get('moduleList')
    if not isinstance(modules, list):
        return []
    for module in modules[:100]:
        if not isinstance(module, dict):
            continue
        components = module.get('components')
        if not isinstance(components, list):
            continue
        for component in components[:100]:
            if not isinstance(component, dict):
                continue
            items = component.get('itemList')
            if not isinstance(items, list):
                continue
            for item in items[:200]:
                if not isinstance(item, dict):
                    continue
                title = item.get('title') or item.get('name')
                action = item.get('action') or {}
                if not isinstance(action, dict):
                    action = {}
                action_type = item.get('action_type') or action.get('type', '')
                if not isinstance(title, str) or not title.strip() or action_type not in {'JUMP_TO_SHOW', 'JUMP_TO_VIDEO'}:
                    continue
                video = item.get('videoIdStr')
                value = item.get('action_value') or action.get('value')
                identifier = video or value
                if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_=-]{4,160}', identifier):
                    continue
                is_video = bool(video or identifier.startswith('X'))
                url = 'https://v.youku.com/video?' + urlencode({'vid' if is_video else 's': identifier})
                rows[url] = {'url': url, 'title': title[:500], 'kind': 'video' if is_video else 'web', 'authors': [],
                             'source': 'youku-public-initial-state', 'content_type': 'video' if is_video else 'program'}
    return list(rows.values())[:200]
