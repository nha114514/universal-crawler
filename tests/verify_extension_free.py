"""Real MCP/browser/download checks from an isolated copy with only libraries."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / 'data/verification/extension-free-project'
SEARCH_ONLY = '--search-only' in sys.argv
EVIDENCE = ROOT / ('evidence/extension-free-search-live.json' if SEARCH_ONLY else 'evidence/extension-free-live.json')
CASES = [
    ('iqiyi-detail', 'https://www.iqiyi.com/v_11mr3ddg90s.html?qyTrace=W1WNz1xkuppLFVAi&posterid=1486369400168001080', 'auto'),
    ('bilibili-home', 'https://www.bilibili.com/', 'session'),
    ('douyin-home', 'https://www.douyin.com/', 'auto'),
    ('tiktok-explore', 'https://www.tiktok.com/explore', 'session'),
    ('keyword-search', 'lateral thinking puzzles', 'auto'),
]
SAMPLES = ['https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', 'https://media.w3.org/2010/05/sintel/trailer.mp4']


async def main():
    COPY.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'crawler_system', COPY / 'crawler_system', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['run_mcp.py', 'run_service.py']:
        shutil.copy2(ROOT / name, COPY / name)
    python = str(ROOT / '.venv/Scripts/python.exe')
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('OPENCLI_')}
    environment.update(PYTHONUTF8='1', CRAWLER_DATA_DIR=str(COPY / 'data'), CRAWLER_PYTHON=python)
    environment['PATH'] = os.pathsep.join(path for path in environment.get('PATH', '').split(os.pathsep) if not any(word in path.lower() for word in ['opencli', 'skills', 'personal-crawlers']))
    parameters = StdioServerParameters(command=python, args=[str(COPY / 'run_mcp.py')], cwd=str(COPY), env=environment)
    report = {'isolated_root': str(COPY), 'libraries_python': python, 'no_adjacent_search_project': not (COPY.parent / 'search-reconstruction').exists(), 'cases': []}

    def save():
        EVIDENCE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        report['tools'] = [tool.name for tool in (await session.list_tools()).tools]
        assert 'download_batch' in report['tools'] and 'open_login_browser' in report['tools']

        async def call(name, **arguments):
            result = await session.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(str(result.content)[:500])
            return result.structured_content or json.loads(next(block.text for block in result.content if hasattr(block, 'text')))

        async def finish(identity, seconds=65):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                job = await call('job_status', job_id=identity)
                if job['state'] in {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}:
                    return job
                await asyncio.sleep(.3)
            await call('cancel_job', job_id=identity)
            return await call('job_status', job_id=identity)

        status = await call('tool_status')
        assert 'legacy_config' not in status['settings'] and 'opencli_profile' not in status['settings']
        report['runtime'] = {'browser_configured': status['browser_configured'], 'platform_note': status['platform_note'], 'installed_libraries': status['tools']}
        movie = None
        for label, value, render in (CASES[-1:] if SEARCH_ONLY else CASES):
            started = time.monotonic()
            try:
                job = await finish((await call('collect', inputs=[value], render=render, mode='web' if label == 'keyword-search' else 'auto', strategy='focused', max_results=3))['id'])
                items = [await call('get_item', item_id=identity) for identity in job['item_ids']]
                row = {'case': label, 'job_id': job['id'], 'state': job['state'], 'seconds': round(time.monotonic()-started, 3), 'errors': job.get('errors'),
                       'items': [{'id': item['id'], 'title': item['title'], 'kind': item['kind'], 'entries': len(item.get('entries', [])), 'assets': len(item.get('assets', [])), 'body_chars': len(item.get('content', '')), 'subtitle_chars': len(item.get('subtitle_text', '')), 'extractor': item.get('extractor'), 'diagnostics': item.get('metadata', {}).get('browser_session') } for item in items]}
                if label == 'iqiyi-detail' and items:
                    movie = items[0]
            except Exception as error:
                row = {'case': label, 'failure': str(error)[:600]}
            report['cases'].append(row)
            save()
            print(json.dumps(row, ensure_ascii=False), flush=True)
        if SEARCH_ONLY:
            return
        samples = await finish((await call('collect', inputs=SAMPLES, render='never'))['id'])
        batch = await call('download_batch', downloads=[{'item_id': identity, 'asset_index': 0, 'max_mb': 8} for identity in samples['item_ids']])
        downloaded = await asyncio.gather(*(finish(job['id'], 120) for job in batch['jobs']))
        report['batch'] = {'queued': batch['queued'], 'jobs': downloaded}
        for job in downloaded:
            assert job['state'] == 'completed', job
            path = Path(job['downloads'][0]['path'])
            assert path.read_bytes()[4:8] == b'ftyp' and path.suffix == '.mp4'
        print('Actual MP4 batch completed', flush=True)
        save()
        if movie:
            probe = await call('download_asset', item_id=movie['id'], asset_index=0, max_mb=8)
            result = await finish(probe['id'], 65)
            report['iqiyi_original_probe'] = {'state': result['state'], 'errors': result.get('errors'), 'downloads': result.get('downloads')}
            save()
            print(json.dumps(report['iqiyi_original_probe'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
