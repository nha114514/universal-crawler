import json
from pathlib import Path
import time
import sys

ROOT = Path('.')
sys.path.insert(0, str(ROOT))
from crawler_system.network import Network
from crawler_system.browser_bridge import collect_session, bridge_ready
from crawler_system.errors import CrawlerError

targets = [
    ('toutiao', 'https://www.toutiao.com/'),
    ('tiktok', 'https://www.tiktok.com/explore'),
    ('twitch', 'https://www.twitch.tv/'),
]

report = {}

for name, url in targets:
    print(f"\n=================== Testing {name}: {url} ===================", flush=True)
    report[name] = {}

    # 1. Static fetch
    print("  [1/3] Static fetch...", flush=True)
    n = Network()
    n.config = {**n.config, 'request_timeout': 8}
    start = time.monotonic()
    try:
        resp = n.fetch(url, allow_errors=True)
        report[name]['static'] = {
            'status': resp.status, 'body_bytes': len(resp.body),
            'elapsed_s': round(time.monotonic() - start, 2)
        }
        print(f"    Static OK: status={resp.status}, bytes={len(resp.body)}, time={report[name]['static']['elapsed_s']}s", flush=True)
    except Exception as e:
        report[name]['static'] = {'error': type(e).__name__ + ': ' + str(e)[:200], 'elapsed_s': round(time.monotonic() - start, 2)}
        print(f"    Static ERR: {report[name]['static']['error']}", flush=True)

    # 2. Independent anonymous browser (Scrapling / DynamicFetcher)
    print("  [2/3] Independent anonymous browser (render=True)...", flush=True)
    start = time.monotonic()
    try:
        resp = n.fetch(url, render=True)
        report[name]['anonymous_browser'] = {
            'status': resp.status, 'body_bytes': len(resp.body),
            'diagnostics': resp.render_diagnostics,
            'elapsed_s': round(time.monotonic() - start, 2)
        }
        print(f"    Browser OK: status={resp.status}, bytes={len(resp.body)}, diag={resp.render_diagnostics}, time={report[name]['anonymous_browser']['elapsed_s']}s", flush=True)
    except Exception as e:
        report[name]['anonymous_browser'] = {'error': type(e).__name__ + ': ' + str(e)[:200], 'elapsed_s': round(time.monotonic() - start, 2)}
        print(f"    Browser ERR: {report[name]['anonymous_browser']['error']}", flush=True)

    # 3. Existing Chrome session (OpenCLI)
    print(f"  [3/3] Existing Chrome session (OpenCLI bridge_ready={bridge_ready()})...", flush=True)
    if bridge_ready():
        start = time.monotonic()
        try:
            item = collect_session(url)
            report[name]['existing_session'] = {
                'title': item.get('title'), 'body_chars': len(item.get('content', '')),
                'entries': len(item.get('entries', [])),
                'elapsed_s': round(time.monotonic() - start, 2)
            }
            print(f"    Session OK: title={item.get('title')}, body={len(item.get('content',''))}, entries={len(item.get('entries',[]))}, time={report[name]['existing_session']['elapsed_s']}s", flush=True)
        except Exception as e:
            report[name]['existing_session'] = {'error': type(e).__name__ + ': ' + str(e)[:200], 'elapsed_s': round(time.monotonic() - start, 2)}
            print(f"    Session ERR: {report[name]['existing_session']['error']}", flush=True)
    else:
        report[name]['existing_session'] = {'error': 'OpenCLI bridge not ready'}
        print("    Session SKIP: bridge not ready", flush=True)

(ROOT / 'tests/diagnose_timeout_results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print("\nTimeout diagnostics finished. Saved to tests/diagnose_timeout_results.json", flush=True)
