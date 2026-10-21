"""Fresh stdio MCP, shared-library and bounded real-platform probes."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
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
    if result.is_error:
        raise RuntimeError(str(result.content)[:400])
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(block.text for block in result.content if block.type == 'text'))


async def wait(session, job_id, timeout=420):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = payload(await session.call_tool('job_status', {'job_id': job_id}))
        if job['state'] not in {'queued', 'running'}:
            return job
        await asyncio.sleep(1)
    await session.call_tool('cancel_job', {'job_id': job_id})
    return payload(await session.call_tool('job_status', {'job_id': job_id}))


async def main():
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': [], 'search_jobs': []}
    api_url, secret = ensure_service()
    params = StdioServerParameters(command=str(ROOT / '.venv/Scripts/python.exe'), args=['-m', 'crawler_system.mcp_server'], cwd=str(ROOT), env=runtime_env())
    def save():
        (ROOT / 'evidence/platform-expansion-live.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        (DATA / 'platform-checks.json').write_text(json.dumps({'checks': evidence['checks'], 'checked_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tool_list = await session.list_tools()
            tools = {tool.name: tool for tool in tool_list.tools}
            assert 'platform_catalog' in tools
            assert {'strategy', 'platforms', 'english_query', 'enrich_limit', 'request_budget'} <= set(tools['collect'].input_schema['properties'])
            directory = payload(await session.call_tool('platform_catalog', {}))
            evidence['protocol'] = {'tools': len(tools), 'catalog': len(directory['platforms']), 'new_collect_parameters': True}
            save()
            scenarios = [
                ('domestic_search', {'inputs': ['Python 教程'], 'mode': 'web', 'region': 'domestic', 'platforms': ['csdn','cnblogs','runoob','juejin'], 'english_query': 'Python tutorial', 'max_results': 20, 'enrich_limit': 1, 'request_budget': 6}),
                ('foreign_search', {'inputs': ['Python tutorial'], 'mode': 'web', 'region': 'foreign', 'platforms': ['github','stackoverflow','reddit','wikipedia','medium','readthedocs'], 'max_results': 20, 'enrich_limit': 1, 'request_budget': 6}),
                ('bilingual_topic', {'inputs': ['海龟汤'], 'mode': 'web', 'region': 'all', 'english_query': 'lateral thinking puzzles', 'max_results': 40, 'enrich_limit': 2, 'request_budget': 8}),
            ]
            for name, args in scenarios:
                started = time.monotonic()
                job = payload(await session.call_tool('collect', {**args, 'bilingual': False}))
                result = await wait(session, job['id'])
                sources = []
                for item_id in result['item_ids']:
                    item = payload(await session.call_tool('get_item', {'item_id': item_id, 'max_chars': 200}))
                    sources.append({key: item.get(key) for key in ('id','url','title','kind','source_region','text_status','material_path')})
                    assert item.get('material_path') and Path(item['material_path']).is_file()
                assert not result['downloads']
                evidence['search_jobs'].append({'name': name, 'job_id': job['id'], 'state': result['state'], 'sources': sources,
                                               'errors': result['errors'], 'events': result['events'], 'readable': result['readable_items'],
                                               'elapsed_seconds': round(time.monotonic()-started,2), 'default_downloads': 0})
                print(name, result['state'], 'sources=',len(sources),'readable=',result['readable_items'],flush=True)
                save()
            probes = [
                ('runoob', 'web', 'https://www.runoob.com/python3/python3-tutorial.html'),
                ('python-docs', 'web', 'https://docs.python.org/3/tutorial/index.html'),
                ('pubmed', 'paper_page', 'https://pubmed.ncbi.nlm.nih.gov/31452104/'),
                ('vimeo', 'video', 'https://vimeo.com/76979871'),
                ('bilibili', 'video', 'https://www.bilibili.com/video/BV12DHk69E3H'),
            ]
            for platform, stage, target in probes:
                job = payload(await session.call_tool('collect', {'inputs':[target], 'mode':'video' if stage=='video' else 'auto'}))
                result = await wait(session, job['id'], 180)
                check = {'platform':platform,'stage':stage,'url':target,'job_id':job['id'],'state':result['state'],
                         'errors':result['errors'],'checked_at':datetime.now(timezone.utc).isoformat()}
                if result['errors']:
                    check['code'] = result['errors'][0].get('code')
                if result['item_ids']:
                    item = payload(await session.call_tool('get_item', {'item_id':result['item_ids'][0],'max_chars':200}))
                    check.update({key:item.get(key) for key in ('id','title','kind','text_status','extractor','subtitle_status')})
                    check['body_chars'] = len(item.get('content') or '')
                    check['assets'] = [{'kind':asset['kind']} for asset in item.get('assets',[])]
                    assert Path(item['material_path']).is_file()
                    async with httpx.AsyncClient(trust_env=False) as client:
                        shared=(await client.get(api_url+'/api/items/'+item['id'], headers={'Authorization':'Bearer '+secret})).json()
                    assert shared['id']==item['id'] and shared['url']==item['url']
                    check['shared_api_verified']=True
                    check['full_body_chars']=len(shared.get('content') or '')
                    if stage=='video' and platform=='vimeo' and result['state']=='completed' and item['kind']=='video':
                        download=payload(await session.call_tool('download_asset', {'item_id':item['id'],'max_mb':30}))
                        finished=await wait(session, download['id'], 300)
                        check['download']={'job_id':download['id'],'state':finished['state'],'downloads':finished['downloads'],'errors':finished['errors']}
                evidence['checks'].append(check)
                print(platform, stage, result['state'], check.get('code',''),flush=True)
                save()
    evidence['finished_at']=datetime.now(timezone.utc).isoformat()
    save()


if __name__ == '__main__':
    if '--followup' not in sys.argv:
        asyncio.run(main())
    else:
        async def followup():
            path=ROOT/'evidence/platform-expansion-live.json'
            report=json.loads(path.read_text(encoding='utf-8'))
            report['superseded_checks']=report.get('superseded_checks',[])+[check for check in report['checks'] if check['platform'] in {'pubmed','bilibili'}]
            report['checks']=[check for check in report['checks'] if check['platform'] not in {'pubmed','bilibili'}]
            params=StdioServerParameters(command=str(ROOT/'.venv/Scripts/python.exe'),args=['-m','crawler_system.mcp_server'],cwd=str(ROOT),env=runtime_env())
            async with stdio_client(params) as (reader,writer):
                async with ClientSession(reader,writer) as session:
                    await session.initialize()
                    for platform,target in [('pubmed','https://pubmed.ncbi.nlm.nih.gov/31452104/'),('bilibili','https://www.bilibili.com/video/BV12DHk69E3H')]:
                        job=payload(await session.call_tool('collect',{'inputs':[target],'mode':'video' if platform=='bilibili' else 'auto'}))
                        result=await wait(session,job['id'],180)
                        check={'platform':platform,'url':target,'stage':'video' if platform=='bilibili' else 'paper_page',
                               'checked_at':datetime.now(timezone.utc).isoformat(),'job_id':job['id'],'state':result['state'],'errors':result['errors']}
                        if result['errors']: check['code']=result['errors'][0]['code']
                        if result['item_ids']:
                            item=payload(await session.call_tool('get_item',{'item_id':result['item_ids'][0],'max_chars':500}))
                            check.update({key:item.get(key) for key in ('id','title','extractor','text_status','subtitle_status','kind')})
                            check['body_chars']=len(item.get('content') or '')
                            check['verification_page']=bool(item.get('metadata',{}).get('verification_page'))
                            if check['verification_page']: assert not item['content']
                            if item['extractor']=='video-page-metadata': assert not item['content']
                        report['checks'].append(check)
                        print(platform,result['state'],check.get('code',''),check.get('body_chars'),flush=True)
            report['quality_followup_at']=datetime.now(timezone.utc).isoformat()
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            (DATA/'platform-checks.json').write_text(json.dumps({'checks':report['checks']},ensure_ascii=False,indent=2),encoding='utf-8')
        asyncio.run(followup())
