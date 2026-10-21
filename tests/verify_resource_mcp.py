"""Fresh MCP connection and actual small-file batch download verification."""
import asyncio
import json
import os
from pathlib import Path
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
IQIYI = 'https://www.iqiyi.com/v_11mr3ddg90s.html?qyTrace=W1WNz1xkuppLFVAi&posterid=1486369400168001080'
SAMPLES = ['https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', 'https://media.w3.org/2010/05/sintel/trailer.mp4']


async def main():
    parameters = StdioServerParameters(command=str(ROOT / '.venv/Scripts/python.exe'), args=[str(ROOT / 'run_mcp.py')], cwd=str(ROOT), env={**os.environ, 'PYTHONUTF8': '1'})
    report = {}
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        names = [tool.name for tool in (await session.list_tools()).tools]
        assert 'download_batch' in names
        report['tools'] = names

        async def call(name, **arguments):
            result = await session.call_tool(name, arguments)
            assert not result.is_error, str(result.content)[:300]
            return result.structured_content or json.loads(next(block.text for block in result.content if hasattr(block, 'text')))

        async def finish(job_id, timeout=60):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                job = await call('job_status', job_id=job_id)
                if job['state'] in {'completed', 'partial', 'failed', 'cancelled'}:
                    return job
                await asyncio.sleep(.25)
            raise TimeoutError(job_id)

        started = time.monotonic()
        first = await call('collect', inputs=[IQIYI], max_results=1)
        job = await finish(first['id'])
        elapsed = time.monotonic() - started
        assert job['state'] == 'completed', job
        item = await call('get_item', item_id=job['item_ids'][0])
        assert item['title'] == '逆行战场' and not item.get('content') and not item.get('subtitle_text')
        report['iqiyi'] = {'job_id': job['id'], 'state': job['state'], 'seconds_to_resources': round(elapsed, 3), 'item_id': item['id'], 'title': item['title'], 'assets': len(item['assets']), 'body_chars': len(item.get('content', '')), 'subtitle_chars': len(item.get('subtitle_text', '')), 'media_url_status': item['metadata']['media_url_status'], 'vip_types': item['metadata'].get('vip_types', [])}
        print(json.dumps(report['iqiyi'], ensure_ascii=False), flush=True)

        samples = await finish((await call('collect', inputs=SAMPLES, render='never'))['id'])
        assert samples['state'] == 'completed', samples
        assert len(samples['item_ids']) == 2
        selected = [{'item_id': identity, 'asset_index': 0, 'max_mb': 8} for identity in samples['item_ids']]
        batch = await call('download_batch', downloads=selected)
        assert batch['queued'] == 2
        jobs = await asyncio.gather(*(finish(job['id'], 120) for job in batch['jobs']))
        saved = []
        for result in jobs:
            assert result['state'] == 'completed', result
            record = result['downloads'][0]
            path = Path(record['path'])
            signature = path.read_bytes()[:32]
            assert signature[4:8] == b'ftyp' and path.suffix == '.mp4'
            saved.append({key: record[key] for key in ['path', 'bytes', 'sha256', 'content_type']})
        report['batch'] = {'queued': batch['queued'], 'jobs': [job['id'] for job in jobs], 'actual_files': saved, 'signature_verified': 'ISO BMFF ftyp; no PNG files'}
        print(json.dumps(report['batch'], ensure_ascii=False), flush=True)
    (ROOT / 'evidence/resource-workflow-mcp-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
