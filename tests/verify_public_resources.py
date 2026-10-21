"""Fresh extension-free MCP checks; candidate discovery is not a download pass."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
FOLLOWUP = '--followup' in sys.argv
COPY = ROOT / ('data/verification/public-platform-followup-project' if FOLLOWUP else 'data/verification/public-platform-project')
REPORT = ROOT / ('evidence/public-platform-followup.json' if FOLLOWUP else 'evidence/public-platform-live.json')
CASES = [
    ('iqiyi-detail', 'https://www.iqiyi.com/v_11mr3ddg90s.html'),
    ('iqiyi-home', 'https://www.iqiyi.com/'),
    ('tencent-home', 'https://v.qq.com/'),
    ('bilibili-home', 'https://www.bilibili.com/'),
    ('douyin-home', 'https://www.douyin.com/jingxuan'),
    ('tiktok-explore', 'https://www.tiktok.com/explore'),
    ('youtube-detail', 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'),
    ('vimeo-home', 'https://vimeo.com/'),
    ('netflix-public-home', 'https://www.netflix.com/'),
    ('ithome', 'https://www.ithome.com/'),
    ('wikipedia', 'https://en.wikipedia.org/wiki/Solar_System'),
    ('mdn', 'https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/video'),
    ('arxiv', 'https://arxiv.org/abs/1706.03762'),
    ('hls', 'https://storage.googleapis.com/shaka-demo-assets/angel-one-hls/hls.m3u8'),
    ('dash', 'https://storage.googleapis.com/shaka-demo-assets/angel-one/dash.mpd'),
    ('bilibili-keyword', 'bili:海龟汤'),
]
if FOLLOWUP:
    CASES = [(label, value) for label, value in CASES if label in {'arxiv', 'hls', 'dash', 'douyin-home', 'tiktok-explore'}]


async def main():
    COPY.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'crawler_system', COPY / 'crawler_system', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['run_mcp.py', 'run_service.py']:
        shutil.copy2(ROOT / name, COPY / name)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('OPENCLI_')}
    env.update(PYTHONUTF8='1', CRAWLER_DATA_DIR=str(COPY / 'data'), CRAWLER_PYTHON=sys.executable)
    parameters = StdioServerParameters(command=sys.executable, args=[str(COPY / 'run_mcp.py')], cwd=str(COPY), env=env)
    report = {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'project_copy': str(COPY), 'cases': [], 'downloads': [],
              'scope': 'Public resource discovery. Homepage candidates and metadata do not prove original video downloads.'}
    selected = []

    def save():
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        report['tools'] = [tool.name for tool in (await session.list_tools()).tools]

        async def call(name, **arguments):
            result = await session.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(str(result.content)[:400])
            return result.structured_content or json.loads(next(block.text for block in result.content if hasattr(block, 'text')))

        async def finish(identity, limit=90):
            deadline = time.monotonic() + limit
            while time.monotonic() < deadline:
                job = await call('job_status', job_id=identity)
                if job['state'] in {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}:
                    return job
                await asyncio.sleep(.3)
            await call('cancel_job', job_id=identity)
            return await call('job_status', job_id=identity)

        for label, value in CASES:
            start = time.monotonic()
            row = {'case': label, 'input': value}
            try:
                job = await finish((await call('collect', inputs=[value], render='auto', strategy='focused', max_results=5))['id'])
                items = [await call('get_item', item_id=identity) for identity in job['item_ids']]
                row.update(job_id=job['id'], state=job['state'], errors=job.get('errors'), seconds=round(time.monotonic()-start, 3))
                row['items'] = [{'id': item['id'], 'title': item['title'], 'kind': item['kind'], 'entries': len(item.get('entries', [])),
                                 'assets': len(item.get('assets', [])), 'media': sum(a['kind'] in {'media', 'video', 'mp4', 'webm', 'audio', 'mp3'} for a in item.get('assets', [])),
                                 'body_chars': len(item.get('content', '')), 'subtitle_chars': len(item.get('subtitle_text', '')), 'extractor': item.get('extractor')} for item in items]
                assert all(not item.get('content') and not item.get('subtitle_text') for item in items)
                if label in {'hls', 'dash', 'arxiv'} and items:
                    kind = 'pdf' if label == 'arxiv' else 'media'
                    index = next((i for i, asset in enumerate(items[0]['assets']) if asset['kind'] == kind), None)
                    if index is not None:
                        selected.append((label, {'item_id': items[0]['id'], 'asset_index': index, 'max_mb': 30}))
                if label == 'iqiyi-detail' and items:
                    probe = await finish((await call('download_asset', item_id=items[0]['id'], asset_index=0, max_mb=8))['id'])
                    report['iqiyi_original'] = {'state': probe['state'], 'errors': probe.get('errors'), 'downloads': probe.get('downloads')}
            except Exception as error:
                row['failure'] = str(error)[:400]
            report['cases'].append(row)
            save()
            print(json.dumps(row, ensure_ascii=False), flush=True)
        if selected:
            batch = await call('download_batch', downloads=[value for label, value in selected])
            report['batch_queued'] = batch['queued']
            for (label, selection), entry in zip(selected, batch['jobs']):
                job = await finish(entry['id'], 180)
                row = {'case': label, 'job_id': job['id'], 'state': job['state'], 'errors': job.get('errors'), 'downloads': job.get('downloads')}
                for download in job.get('downloads') or []:
                    if label != 'arxiv':
                        probe = subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height:format=duration,size', '-of', 'json', download['path']], capture_output=True, text=True, timeout=20)
                        row['ffprobe_exit_code'] = probe.returncode
                        row['media'] = json.loads(probe.stdout) if probe.stdout else {'error': probe.stderr[:300]}
                    else:
                        with Path(download['path']).open('rb') as file:
                            row['pdf_signature_valid'] = file.read(5) == b'%PDF-'
                report['downloads'].append(row)
                save()
                print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
