import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx

ROOT = Path('.')

targets = {
    'kuaishou': ['https://kuaishou.com/', 'https://www.kuaishou.com/', 'https://live.kuaishou.com/'],
    'douban': ['https://douban.com/', 'https://www.douban.com/', 'https://movie.douban.com/', 'https://www.douban.com/feed/review/movie'],
    '36kr': ['https://36kr.com/', 'https://www.36kr.com/', 'https://36kr.com/feed', 'https://www.36kr.com/newsflashes'],
    'huxiu': ['https://huxiu.com/', 'https://www.huxiu.com/', 'https://www.huxiu.com/rss/0.xml'],
    'qingting': ['https://qingting.fm/', 'https://www.qingting.fm/', 'https://www.qingting.fm/channels/'],
    'wanfang': ['https://wanfangdata.com.cn/', 'https://www.wanfangdata.com.cn/', 'https://c.wanfangdata.com.cn/periodical'],
    'cqvip': ['https://cqvip.com/', 'https://www.cqvip.com/'],
    'sciengine': ['https://sciengine.com/', 'https://www.sciengine.com/'],
    'aminer': ['https://aminer.cn/', 'https://www.aminer.cn/'],
    'chinaxiv': ['https://chinaxiv.org/', 'https://www.chinaxiv.org/'],
    'sciencepaper': ['https://paper.edu.cn/', 'http://paper.edu.cn/', 'http://www.paper.edu.cn/'],
    'csdn': ['https://csdn.net/', 'https://www.csdn.net/', 'https://blog.csdn.net/'],
    'cnblogs': ['https://cnblogs.com/', 'https://www.cnblogs.com/', 'https://feed.cnblogs.com/blog/sitehome/rss'],
    'segmentfault': ['https://segmentfault.com/', 'https://www.segmentfault.com/', 'https://segmentfault.com/feeds/blogs'],
    'gitee': ['https://gitee.com/', 'https://www.gitee.com/', 'https://gitee.com/explore'],
    'sspai': ['https://sspai.com/', 'https://www.sspai.com/', 'https://sspai.com/feed'],
    'smzdm': ['https://smzdm.com/', 'https://www.smzdm.com/', 'https://feed.smzdm.com/'],
}

results = {}
for name, urls in targets.items():
    results[name] = []
    print(f"\n--- Checking {name} ---", flush=True)
    for u in urls:
        started = time.monotonic()
        try:
            with httpx.Client(timeout=4.0, follow_redirects=True, verify=False) as client:
                resp = client.get(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
                elapsed = round(time.monotonic() - started, 2)
                res = {'url': u, 'final_url': str(resp.url), 'status': resp.status_code, 'len': len(resp.content), 'elapsed': elapsed}
                results[name].append(res)
                print(f"  {u:45} -> {resp.status_code} ({len(resp.content)} bytes, {elapsed}s)", flush=True)
        except Exception as e:
            elapsed = round(time.monotonic() - started, 2)
            err = type(e).__name__ + ': ' + str(e)[:100]
            res = {'url': u, 'status': None, 'error': err, 'elapsed': elapsed}
            results[name].append(res)
            print(f"  {u:45} -> ERROR: {err} ({elapsed}s)", flush=True)

(ROOT / 'tests/diagnose_17_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
