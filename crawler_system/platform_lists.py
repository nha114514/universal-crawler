"""Named public content cards actually returned to an anonymous site page."""
import re


def tencent_entries(payload):
    if payload.get('ret') != 0 or not isinstance(payload.get('data'), dict):
        return []
    sections = payload['data'].get('CardList')
    if not isinstance(sections, list):
        return []
    rows = {}
    for section in sections[:100]:
        if not isinstance(section, dict) or not isinstance(section.get('children_list'), dict):
            continue
        listing = section['children_list'].get('list')
        if not isinstance(listing, dict) or not isinstance(listing.get('cards'), list):
            continue
        # ad_list, cut_vid and window_vid describe ads/previews, not the original.
        for card in listing['cards'][:200]:
            if not isinstance(card, dict) or not isinstance(card.get('params'), dict):
                continue
            item = card['params']
            title = item.get('title') or item.get('title_pc')
            cid, vid = item.get('cid'), item.get('vid')
            if not isinstance(title, str) or not title.strip() or not isinstance(cid, str) or not re.fullmatch(r'[a-zA-Z0-9]{8,30}', cid):
                continue
            is_video = isinstance(vid, str) and bool(re.fullmatch(r'[a-zA-Z0-9]{8,30}', vid))
            target = 'https://v.qq.com/x/cover/' + cid + ('/' + vid if is_video else '') + '.html'
            rows[target] = {'url': target, 'title': title[:500], 'kind': 'video' if is_video else 'web', 'authors': [],
                            'source': 'tencent-public-page-response', 'content_type': 'video' if is_video else 'program'}
    return list(rows.values())[:200]
