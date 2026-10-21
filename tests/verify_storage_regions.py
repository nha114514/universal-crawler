"""Real MCP/API/file verification without printing authentication or browser data."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import DATA, runtime_env
from crawler_system.service import ensure_service


def payload(result):
    if getattr(result, 'structured_content', None) is not None:
        return result.structured_content
    for block in result.content:
        if block.type == 'text':
            return json.loads(block.text)
    raise AssertionError('Missing MCP JSON result')


async def wait(session, job_id):
    deadline = time.monotonic() + 180
    while True:
        result = payload(await session.call_tool('job_status', {'job_id': job_id}))
        if result['state'] not in {'queued', 'running'}:
            return result
        if time.monotonic() > deadline:
            raise AssertionError('Job did not finish within the verification bound: ' + job_id)
        await asyncio.sleep(1)


async def main():
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    url, secret = ensure_service()
    headers = {'Authorization': 'Bearer ' + secret}
    before_file = DATA / 'storage-regions-before.json'
    if before_file.exists():
        before = json.loads(before_file.read_text(encoding='utf-8'))
        with closing(sqlite3.connect(DATA / 'library.sqlite3')) as db:
            after = {row[0]: hashlib.sha256(json.dumps({key: json.loads(row[1]).get(key) for key in ('url', 'title', 'content', 'subtitle_text', 'abstract', 'assets')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest() for row in db.execute('SELECT id,payload FROM items')}
        assert all(after.get(key) == digest for key, digest in before.items())
        evidence['checks'].append({'name': 'old_items_unchanged', 'passed': True, 'items': len(before)})
    params = StdioServerParameters(command=str(ROOT / '.venv/Scripts/python.exe'), args=['-m', 'crawler_system.mcp_server'], cwd=str(ROOT), env=runtime_env())
    original_root = None
    try:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=180) as session:
                initialized = await session.initialize()
                catalog = await session.list_tools()
                schemas = {tool.name: tool.input_schema for tool in catalog.tools}
                for name in ('collect', 'library_search', 'web_search', 'export_library'):
                    assert 'region' in schemas[name]['properties']
                assert {'set_storage_location', 'set_source_region'} <= schemas.keys()
                evidence['checks'].append({'name': 'fresh_mcp_schemas', 'passed': True, 'protocol': initialized.protocol_version, 'tools': list(schemas)})
                status = payload(await session.call_tool('tool_status'))
                original_root = status['storage']['root']
                chosen = DATA / 'storage-region-validation'
                saved = payload(await session.call_tool('set_storage_location', {'path': str(chosen)}))
                assert Path(saved['storage_root']) == chosen
                status = payload(await session.call_tool('tool_status'))
                assert Path(status['storage']['root']) == chosen
                evidence['checks'].append({'name': 'mcp_storage_write_read', 'passed': True, 'root': str(chosen)})
                items = {}
                for name, address, region in [
                    ('domestic_article', 'https://www.runoob.com/python3/python3-tutorial.html', 'domestic'),
                    ('overseas_chinese_article', 'https://docs.python.org/zh-cn/3/tutorial/', 'foreign'),
                    ('foreign_pdf', 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf', 'foreign'),
                ]:
                    job = payload(await session.call_tool('collect', {'inputs': [address], 'mode': 'web' if name != 'foreign_pdf' else 'auto', 'enrich': False, 'render': 'never'}))
                    result = await wait(session, job['id'])
                    check = {'name': name, 'job_id': job['id'], 'state': result['state'], 'errors': result['errors'], 'passed': bool(result['item_ids'])}
                    if result['item_ids']:
                        item = payload(await session.call_tool('get_item', {'item_id': result['item_ids'][0]}))
                        path = Path(item['material_path'])
                        assert path.is_file() and path.is_relative_to(chosen)
                        actual = json.loads(path.read_text(encoding='utf-8'))
                        assert actual['source_region'] == region == item['source_region']
                        assert actual['id'] == item['id']
                        check.update(item_id=item['id'], region=region, material_path=str(path), content_chars=len(actual.get('content', '')), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                        items[name] = item
                    evidence['checks'].append(check)
                    print(name, result['state'], flush=True)
                for region in ('domestic', 'foreign'):
                    rows = payload(await session.call_tool('library_search', {'region': region, 'limit': 100}))
                    assert all(row['source_region'] == region for row in rows['items'])
                    assert rows['total'] >= len(rows['items'])
                    exported = payload(await session.call_tool('export_library', {'format': 'json', 'region': region}))
                    path = Path(exported['path'])
                    actual = json.loads(path.read_text(encoding='utf-8'))
                    assert path.is_relative_to(chosen) and actual
                    assert all(row['source_region'] == region for row in actual)
                    evidence['checks'].append({'name': region + '_library_and_export', 'passed': True, 'items': rows['total'], 'export': exported})
                if 'foreign_pdf' in items:
                    item = items['foreign_pdf']
                    index = next((index for index, asset in enumerate(item['assets']) if asset.get('kind') == 'pdf'), None)
                    if index is not None:
                        job = payload(await session.call_tool('download_asset', {'item_id': item['id'], 'asset_index': index, 'max_mb': 2}))
                        result = await wait(session, job['id'])
                        check = {'name': 'download_to_selected_root', 'job_id': job['id'], 'state': result['state'], 'errors': result['errors'], 'passed': bool(result['downloads'])}
                        if result['downloads']:
                            record = result['downloads'][0]
                            path = Path(record['path'])
                            assert path.is_relative_to(chosen / '国外' / '下载')
                            assert path.read_bytes().startswith(b'%PDF-')
                            assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
                            with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
                                response = client.get('/api/downloads/' + record['id'] + '/file')
                                assert response.status_code == 200 and response.content == path.read_bytes()
                            check.update(path=str(path), bytes=record['bytes'], sha256=record['sha256'])
                        evidence['checks'].append(check)
                for region in ('domestic', 'foreign'):
                    job = payload(await session.call_tool('collect', {'inputs': ['Python 教程'], 'mode': 'web', 'region': region, 'max_results': 3, 'enrich': False}))
                    result = await wait(session, job['id'])
                    rows = [payload(await session.call_tool('get_item', {'item_id': item_id, 'max_chars': 500})) for item_id in result['item_ids']]
                    assert all(row['source_region'] == region for row in rows)
                    check = {'name': region + '_live_keyword_scope', 'job_id': job['id'], 'state': result['state'], 'errors': result['errors'],
                             'events': result['events'], 'sources': [row['url'] for row in rows], 'passed': bool(rows)}
                    evidence['checks'].append(check)
                    print(region, 'keyword', result['state'], 'sources', len(rows), flush=True)
    finally:
        if original_root:
            with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
                response = client.put('/api/settings', json={'storage_root': original_root})
                response.raise_for_status()
            evidence['restored_storage_root'] = original_root
        evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
        (ROOT / 'evidence/storage-regions-live.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    failed = [check['name'] for check in evidence['checks'] if not check['passed']]
    print(json.dumps({'checks': len(evidence['checks']), 'failed': failed, 'restored_root': original_root}, ensure_ascii=False))
    if failed:
        raise AssertionError('Live verification incomplete: ' + ', '.join(failed))


if __name__ == '__main__':
    asyncio.run(main())
