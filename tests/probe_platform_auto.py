"""Exercise all directory public entry URLs through actual bounded app workers."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crawler_system.platforms import catalog, preferred_public_url
from tests.verify_universal_urls import check


if __name__ == '__main__':
    baseline = json.loads((ROOT / 'evidence/universal-platform-static-probe.json').read_text(encoding='utf-8'))
    observed = {row['platform']: row.get('final_url') for row in baseline['results']}
    fingerprints = {name: hashlib.sha256((ROOT / 'crawler_system' / name).read_bytes()).hexdigest()
                    for name in ['network.py', 'parsers.py', 'router.py', 'web.py', 'browser_bridge.py', 'jobs.py']}
    rows = []
    output = ROOT / 'evidence/universal-platform-auto-probe.json'
    started = datetime.now(timezone.utc).isoformat()
    cases = [(row['id'], preferred_public_url(observed.get(row['id']) or 'https://' + row['domains'][0] + '/'), 'web') for row in catalog()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(check, case) for case in cases]):
            row = future.result()
            rows.append(row)
            output.write_text(json.dumps({'started_at': started, 'updated_at': datetime.now(timezone.utc).isoformat(),
                                          'scope': 'Actual public homepage/entry collection workers. No original downloads. These observations do not guarantee every article, login, subtitle or media download.',
                                          'code_sha256': fingerprints, 'total': len(cases), 'checked': len(rows), 'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(row['source'], [(item['kind'], item['text_status'], item['body_chars'], item['entries']) for item in row.get('items', [])], [e.get('code') for e in row.get('errors', [])], flush=True)
