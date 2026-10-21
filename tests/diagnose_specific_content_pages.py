import json
from pathlib import Path
import time
import sys

ROOT = Path('.')
sys.path.insert(0, str(ROOT))
from crawler_system.jobs import run_worker
import threading

targets_content = [
    ('reuters', 'https://www.reuters.com/', 'https://www.reuters.com/world/'),
    ('thepaper', 'https://www.thepaper.cn/', 'https://www.thepaper.cn/newsDetail_forward_28800000'),
    ('xiaohongshu', 'https://www.xiaohongshu.com/explore', 'https://www.xiaohongshu.com/explore'),
    ('xiaoyuzhou', 'https://www.xiaoyuzhoufm.com/', 'https://www.xiaoyuzhoufm.com/podcast/5e280fae418a84a0461fc18c'),
    ('instagram', 'https://www.instagram.com/nasa/', 'https://www.instagram.com/p/DBEbM1RujzG/'),
    ('biorxiv', 'https://www.biorxiv.org/', 'https://www.biorxiv.org/content/10.1101/2023.01.01.522434v1'),
    ('springer', 'https://link.springer.com/', 'https://link.springer.com/article/10.1007/s11263-023-01878-4'),
    ('archive', 'https://archive.org/', 'https://archive.org/details/nasa'),
    ('ebay', 'https://www.ebay.com/', 'https://www.ebay.com/b/Cell-Phones-Smartphones/9355/bn_320094'),
]

report = []
for name, home_url, content_url in targets_content:
    print(f"\nTesting {name}...", flush=True)
    for kind, u in [('home', home_url), ('content_page', content_url)]:
        started = time.monotonic()
        row = {'platform': name, 'type': kind, 'url': u}
        try:
            res = run_worker({'operation': 'collect', 'input': u, 'mode': 'auto', 'render': 'auto'}, threading.Event())
            elapsed = round(time.monotonic() - started, 2)
            row['elapsed_s'] = elapsed
            row['errors'] = res.get('errors', [])
            items = res.get('items', [])
            row['item_count'] = len(items)
            if items:
                it = items[0]
                row['first_item'] = {
                    'title': it.get('title'), 'kind': it.get('kind'),
                    'text_status': it.get('text_status'),
                    'body_chars': len(it.get('content', '')),
                    'entries': len(it.get('entries', [])),
                    'extractor': it.get('extractor'),
                    'warnings': it.get('warnings', [])
                }
            print(f"  [{kind}] {u[:45]} -> {row.get('first_item', {}).get('text_status')} body={row.get('first_item', {}).get('body_chars')} entries={row.get('first_item', {}).get('entries')} errs={[e.get('code') for e in row['errors']]} ({elapsed}s)", flush=True)
        except Exception as e:
            elapsed = round(time.monotonic() - started, 2)
            row['elapsed_s'] = elapsed
            row['exception'] = str(e)[:200]
            print(f"  [{kind}] {u[:45]} -> EXCEPTION {row['exception']} ({elapsed}s)", flush=True)
        report.append(row)

(ROOT / 'tests/diagnose_content_results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print("\nSaved to tests/diagnose_content_results.json", flush=True)
