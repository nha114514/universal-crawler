import json
from pathlib import Path

ROOT = Path('.')
auto = json.loads((ROOT / 'evidence/universal-platform-auto-probe.json').read_text(encoding='utf-8'))
results = {r['source']: r for r in auto['results']}
targets_17 = [
    'kuaishou', 'douban', '36kr', 'huxiu', 'qingting', 'wanfang', 'cqvip',
    'sciengine', 'aminer', 'chinaxiv', 'sciencepaper', 'csdn', 'cnblogs',
    'segmentfault', 'gitee', 'sspai', 'smzdm'
]
print("=== 17 Network Failure Targets in auto-probe ===")
for t in targets_17:
    r = results.get(t, {})
    items = r.get('items', [])
    errs = r.get('errors', [])
    first_item = items[0] if items else {}
    print(f"{t:15} | url: {r.get('requested_url')} | items: {len(items)} | kind: {first_item.get('kind')} | text_status: {first_item.get('text_status')} | body: {first_item.get('body_chars')} | entries: {first_item.get('entries')} | errs: {[e.get('code') for e in errs]}")
