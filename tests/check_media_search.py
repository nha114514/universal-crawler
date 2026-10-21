import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import runtime_env
from tests.verify_mcp import payload


async def main():
    params=StdioServerParameters(command=str(ROOT/'.venv/Scripts/python.exe'),args=['-m','crawler_system.mcp_server'],cwd=str(ROOT),env=runtime_env())
    evidence=[]
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer,read_timeout_seconds=180) as session:
            await session.initialize()
            cases=[('youtube','https://www.youtube.com/watch?v=aircAruvnKk','video'),('video_file','https://media.w3.org/2010/05/sintel/trailer.mp4','video'),('paper_search','attention transformer','paper'),('web_search','Python official documentation','web'),('auto_search','attention transformer','auto'),('video_search','neural networks explained','video'),('platform_bridge','bili: 机器学习','web')]
            pending=[]
            for name,value,mode in cases:
                job=payload(await session.call_tool('collect',{'inputs':[value],'mode':mode,'enrich':False,'max_results':3}))
                pending.append((name,job['id']))
            deadline=time.monotonic()+300
            while pending and time.monotonic()<deadline:
                for name,job_id in list(pending):
                    job=payload(await session.call_tool('job_status',{'job_id':job_id}))
                    if job['state'] in {'queued','running'}:
                        continue
                    row={'name':name,'job_id':job_id,'state':job['state'],'errors':job['errors'],'items':[]}
                    for item_id in job['item_ids']:
                        item=payload(await session.call_tool('get_item',{'item_id':item_id,'max_chars':3000}))
                        row['items'].append({key:item.get(key) for key in ['id','url','title','kind','extractor','text_status','subtitle_status','warnings']})
                        row['items'][-1]['subtitle_chars']=len(item.get('subtitle_text',''))
                    evidence.append(row);pending.remove((name,job_id));print(name,job['state'],len(job['item_ids']),job['errors'],flush=True)
                await asyncio.sleep(1)
    (ROOT/'evidence/media-and-search.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not pending, 'Jobs still pending at the verification deadline'
    assert all(row['items'] for row in evidence if row['name'] != 'platform_bridge'), evidence


if __name__=='__main__':
    asyncio.run(main())
