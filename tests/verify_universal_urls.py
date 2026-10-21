"""Real application workers against representative public URLs; no originals downloaded."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler_system.errors import CrawlerError
from crawler_system.jobs import run_worker

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'evidence/universal-url-workers.json'
CASES = [
    ('runoob', 'https://www.runoob.com/python/python-tutorial.html', 'auto'),
    ('python-docs', 'https://docs.python.org/3/tutorial/index.html', 'auto'),
    ('mozilla', 'https://developer.mozilla.org/en-US/docs/Web/JavaScript', 'auto'),
    ('wikipedia', 'https://en.wikipedia.org/wiki/Lateral_thinking_puzzle', 'auto'),
    ('zh-wikipedia', 'https://zh.wikipedia.org/wiki/海龜湯', 'auto'),
    ('github', 'https://github.com/python/cpython', 'web'),
    ('stackoverflow', 'https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do-in-python', 'web'),
    ('reddit', 'https://www.reddit.com/r/Python/', 'web'),
    ('hackernews', 'https://news.ycombinator.com/', 'web'),
    ('v2ex', 'https://www.v2ex.com/', 'web'),
    ('xiaohongshu', 'https://www.xiaohongshu.com/explore', 'web'),
    ('zhihu', 'https://www.zhihu.com/', 'web'),
    ('weibo', 'https://weibo.com/', 'web'),
    ('douban', 'https://www.douban.com/', 'web'),
    ('thepaper', 'https://www.thepaper.cn/', 'web'),
    ('bbc', 'https://www.bbc.com/news', 'web'),
    ('cnn', 'https://edition.cnn.com/', 'web'),
    ('pubmed', 'https://pubmed.ncbi.nlm.nih.gov/31452104/', 'auto'),
    ('arxiv', 'https://arxiv.org/abs/1706.03762', 'auto'),
    ('bilibili-home', 'https://www.bilibili.com/', 'auto'),
    ('douyin-home', 'https://www.douyin.com/', 'auto'),
    ('youtube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw', 'auto'),
    ('vimeo', 'https://vimeo.com/76979871', 'auto'),
    ('tiktok', 'https://www.tiktok.com/explore', 'web'),
    ('instagram', 'https://www.instagram.com/nasa/', 'web'),
    ('facebook', 'https://www.facebook.com/NASA/', 'web'),
    ('x', 'https://x.com/NASA', 'web'),
    ('acfun', 'https://www.acfun.cn/', 'web'),
    ('youku', 'https://www.youku.com/', 'web'),
    ('tencent-video', 'https://v.qq.com/', 'web'),
]


def check(case):
    name, url, mode = case
    started = time.monotonic()
    row = {'source': name, 'requested_url': url, 'mode': mode}
    try:
        result = run_worker({'operation': 'collect', 'input': url, 'mode': mode, 'render': 'auto'}, threading.Event())
        row['errors'] = result.get('errors', [])
        row['items'] = [{'url': item['url'], 'title': item.get('title'), 'kind': item.get('kind'),
                         'text_status': item.get('text_status'), 'body_chars': len(item.get('content', '')),
                         'abstract_chars': len(item.get('abstract', '')), 'subtitle_status': item.get('subtitle_status'),
                         'subtitle_chars': len(item.get('subtitle_text', '')), 'entries': len(item.get('entries', [])),
                         'assets': len(item.get('assets', [])), 'extractor': item.get('extractor'),
                         'retrieval_error': item.get('retrieval_error'),
                         'render_reason': item.get('metadata', {}).get('render_reason'),
                         'media_assets': sum(a.get('kind') in {'media', 'video', 'audio', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg'} for a in item.get('assets', []))}
                        for item in result.get('items', [])]
    except Exception as error:
        row['errors'] = [error.as_dict() if isinstance(error, CrawlerError) else {'code': type(error).__name__, 'message': str(error)[:350]}]
    row['elapsed_s'] = round(time.monotonic() - started, 2)
    return row


if __name__ == '__main__':
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(check, case) for case in CASES]):
            row = future.result()
            rows.append(row)
            OUTPUT.write_text(json.dumps({'captured_at': datetime.now(timezone.utc).isoformat(),
                                         'note': 'Real worker collection of each listed URL. No original downloads or personal login cookies. Counts do not establish all pages on a platform.',
                                         'total': len(CASES), 'checked': len(rows), 'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(row['source'], [(i['kind'], i['text_status'], i['body_chars'], i['entries']) for i in row.get('items', [])], [e.get('code') for e in row['errors']], flush=True)
