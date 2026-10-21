"""Capture anonymous public list responses actually requested by the page."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scrapling.fetchers import DynamicFetcher
from crawler_system.network import Network

if __name__ == '__main__':
    real_fetch = DynamicFetcher.fetch
    rows = []
    def capture(url, **options):
        options['capture_xhr'] = r'/trpc\.vector_layout\.page_view\.PageService/getPage'
        page = real_fetch(url, **options)
        for response in page.captured_xhr:
            if response.status == 200 and len(response.body) < 4 * 1024 * 1024:
                try:
                    rows.append(json.loads(response.body))
                except ValueError:
                    pass
        return page
    with patch.object(DynamicFetcher, 'fetch', side_effect=capture):
        Network().fetch('https://v.qq.com/', render=True)
    directory = ROOT / 'data/reverse/tencent-video'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'public-lists.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    print({'responses': len(rows), 'top_keys': [list(row) for row in rows]})
    hits = []
    def walk(value, path=()):
        if isinstance(value, dict):
            if any(key in value for key in ('play_link','url','play_url','title','cid','vid','item_id','content_id')) and len(hits) < 15:
                hits.append({'path':path, 'keys':list(value), 'data':{key:value[key] for key in ('play_link','url','play_url','title','cid','vid','item_id','content_id') if key in value and isinstance(value[key],(str,int))}})
            for key, child in value.items():
                walk(child, path+(key,))
        elif isinstance(value,list):
            for index,child in enumerate(value):
                walk(child,path+(index,))
    walk(rows)
    print(json.dumps(hits, ensure_ascii=False)[:6000])
