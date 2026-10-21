"""Real MCP failure reporting and cancellation against the shared API."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import python_command, runtime_env
from tests.verify_mcp import payload


async def main():
    params = StdioServerParameters(command=python_command(), args=[str(ROOT / 'run_mcp.py')], env=runtime_env())
    checks = []
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            async def call(name, args):
                return payload(await session.call_tool(name, args))
            job = await call('collect', {'inputs': ['http://127.0.0.1:12345/private', 'https://www.python.org/doc/'], 'mode': 'web', 'enrich': False, 'render': 'never'})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                finished = await call('job_status', {'job_id': job['id']})
                if finished['state'] not in {'queued', 'running'}:
                    break
                await asyncio.sleep(.5)
            assert finished['state'] == 'partial' and len(finished['item_ids']) == 1, finished
            assert any(row['code'] == 'PRIVATE_URL' for row in finished['errors']), finished
            checks.append({'name': 'real_partial_failure_and_private_url_rejection', 'passed': True, 'job_id': job['id'], 'state': finished['state'], 'error_codes': [row['code'] for row in finished['errors']]})
            job = await call('crawl_site', {'url': 'https://quotes.toscrape.com/', 'max_pages': 50, 'depth': 2, 'render': 'never'})
            for _ in range(30):
                running = await call('job_status', {'job_id': job['id']})
                if running['state'] == 'running':
                    break
                await asyncio.sleep(.1)
            assert running['state'] == 'running', running
            await asyncio.sleep(.5)
            cancelled = await call('cancel_job', {'job_id': job['id']})
            await asyncio.sleep(.8)
            final = await call('job_status', {'job_id': job['id']})
            assert cancelled['state'] == final['state'] == 'cancelled'
            assert (await call('tool_status', {}))['application'] == 'universal-crawler-v1'
            checks.append({'name': 'real_running_crawl_cancel_and_service_survival', 'passed': True, 'job_id': job['id'], 'state': final['state']})
    (ROOT / 'evidence/error-verification.json').write_text(json.dumps({'verified_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Real error/cancellation checks PASS', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
