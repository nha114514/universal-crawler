"""Bounded real public-page probes; directory coverage is not body/video coverage."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler_system.errors import CrawlerError
from crawler_system.network import Network
from crawler_system.platforms import catalog
from crawler_system.web import collect_url

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'evidence/universal-platform-static-probe.json'


def probe(row):
    started = time.monotonic()
    url = 'https://' + row['domains'][0] + '/'
    network = Network()
    network.config = {**network.config, 'request_timeout': 10, 'domain_interval': 1}
    network.max_bytes = 4 * 1024 * 1024
    try:
        item = collect_url(url, network=network, render='never')
        result = {'final_url': item['url'], 'title': item['title'], 'kind': item['kind'],
                  'text_status': item['text_status'], 'body_chars': len(item.get('content', '')),
                  'entries': len(item.get('entries', [])), 'assets': len(item.get('assets', [])),
                  'media_assets': sum(a.get('kind') in {'media', 'video', 'audio', 'mp4', 'webm', 'mp3', 'm4a'} for a in item.get('assets', [])),
                  'error': item.get('retrieval_error'), 'http_status': item.get('http_status')}
    except Exception as error:
        result = {'error': error.as_dict() if isinstance(error, CrawlerError) else {'code': type(error).__name__, 'message': str(error)[:250]}}
    return {'platform': row['id'], 'region': row['region'], 'category': row['category'], 'url': url,
            'elapsed_s': round(time.monotonic() - started, 2), **result}


if __name__ == '__main__':
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(probe, row) for row in catalog()]
        for future in as_completed(futures):
            rows.append(future.result())
            payload = {'captured_at': datetime.now(timezone.utc).isoformat(),
                       'scope': 'Public homepage static probes only; not article, login or media-download verification.',
                       'total': len(catalog()), 'checked': len(rows), 'results': rows}
            OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            row = rows[-1]
            print(row['platform'], row.get('text_status') or row['error']['code'], row.get('body_chars', 0), flush=True)
