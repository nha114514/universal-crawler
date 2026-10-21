"""Verify common www aliases for public entry hosts whose bare connection timed out."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crawler_system.network import Network
from crawler_system.errors import CrawlerError


def check(row):
    host = urlsplit(row['url']).hostname
    target = 'https://www.' + host + '/'
    network = Network()
    network.config = {**network.config, 'request_timeout': 8}
    network.max_bytes = 4 * 1024 * 1024
    result = {'platform': row['platform'], 'host': host, 'alias': 'www.' + host, 'url': target}
    try:
        response = network.fetch(target, allow_errors=True)
        result.update(final_url=response.url, http_status=response.status, bytes=len(response.body))
    except CrawlerError as error:
        result['error'] = error.as_dict()
    return result


if __name__ == '__main__':
    baseline = json.loads((ROOT / 'evidence/universal-platform-static-probe.json').read_text(encoding='utf-8'))
    candidates = [row for row in baseline['results'] if (row.get('error') or {}).get('code') == 'NETWORK_FAILURE'
                  and not urlsplit(row['url']).hostname.startswith('www.')]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(check, row) for row in candidates]):
            results.append(future.result())
            (ROOT / 'evidence/universal-public-aliases.json').write_text(json.dumps({'captured_at': datetime.now(timezone.utc).isoformat(), 'total': len(candidates), 'checked': len(results), 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
            row = results[-1]
            print(row['platform'], row.get('http_status') or row['error']['code'], flush=True)
