import json
from pathlib import Path
import time
import sys

ROOT = Path('.')
sys.path.insert(0, str(ROOT))
from crawler_system.jobs import run_worker
import threading

targets_cat3 = [
    ('iqiyi', 'https://www.iqiyi.com/'),
    ('mgtv', 'https://www.mgtv.com/'),
    ('tieba', 'https://tieba.baidu.com/'),
    ('baike', 'https://baike.baidu.com/'),
    ('niconico', 'https://www.nicovideo.jp/'),
    ('nytimes', 'https://www.nytimes.com/'),
    ('acfun', 'https://www.acfun.cn/'),
    ('ximalaya', 'https://www.ximalaya.com/'),
    ('wenku', 'https://wenku.baidu.com/'),
    ('rumble', 'https://rumble.com/'),
]

report = []
for name, url in targets_cat3:
    print(f"\nTesting Category 3: {name} ({url})...", flush=True)
    started = time.monotonic()
    row = {'platform': name, 'url': url}
    try:
        # Use mode='web', render='auto'
        res = run_worker({'operation': 'collect', 'input': url, 'mode': 'web', 'render': 'auto'}, threading.Event())
        elapsed = round(time.monotonic() - started, 2)
        row['elapsed_s'] = elapsed
        row['errors'] = res.get('errors', [])
        items = res.get('items', [])
        row['item_count'] = len(items)
        if items:
            it = items[0]
            row['item'] = {
                'title': it.get('title'), 'kind': it.get('kind'),
                'text_status': it.get('text_status'),
                'body_chars': len(it.get('content', '')),
                'entries': len(it.get('entries', [])),
                'extractor': it.get('extractor'),
                'warnings': it.get('warnings', [])
            }
            print(f"  Result: {it.get('text_status')} | body={len(it.get('content',''))} | entries={len(it.get('entries',[]))} | kind={it.get('kind')} | errs={[e.get('code') for e in row['errors']]} ({elapsed}s)", flush=True)
        else:
            print(f"  No items: errs={[e.get('code') for e in row['errors']]} ({elapsed}s)", flush=True)
    except Exception as e:
        elapsed = round(time.monotonic() - started, 2)
        row['elapsed_s'] = elapsed
        row['exception'] = str(e)[:200]
        print(f"  Exception: {row['exception']} ({elapsed}s)", flush=True)
    report.append(row)

(ROOT / 'tests/diagnose_cat3_results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print("\nSaved to tests/diagnose_cat3_results.json", flush=True)
