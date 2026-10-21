import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import tomllib

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import DATA,runtime_env
from tests.verify_mcp import payload


async def main():
    config=tomllib.loads((Path.home()/'.codex/config.toml').read_text(encoding='utf-8'))['mcp_servers']['crawler-system']
    params=StdioServerParameters(command=config['command'],args=config['args'],env={**runtime_env(),**config.get('env',{})})
    evidence={'started_at':datetime.now(timezone.utc).isoformat(),'registered_command_verified':True,'checks':[]}
    original_files_before = {path.name for path in (DATA/'downloads').glob('*') if path.is_file()}
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer,read_timeout_seconds=180) as session:
            await session.initialize()
            async def call(name,args):
                return payload(await session.call_tool(name,args))
            async def wait(job):
                deadline=time.monotonic()+300
                while time.monotonic()<deadline:
                    value=await call('job_status',{'job_id':job['id']})
                    if value['state'] not in {'queued','running'}:
                        return value
                    await asyncio.sleep(.5)
                return value
            cases=[('ted_fallback','https://www.ted.com/talks/hans_rosling_the_best_stats_you_ve_ever_seen','video'),('rss','https://www.nasa.gov/feed/','auto'),('large_file_metadata','https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4.zip','auto'),('direct_media_metadata','https://media.w3.org/2010/05/sintel/trailer.mp4','video')]
            for name,value,mode in cases:
                result=await wait(await call('collect',{'inputs':[value],'mode':mode,'enrich':False}))
                row={'name':name,'state':result['state'],'errors':result['errors'],'items':[],'job_id':result['id']}
                for item_id in result['item_ids']:
                    item=await call('get_item',{'item_id':item_id,'max_chars':2000})
                    row['items'].append({key:item.get(key) for key in ['id','title','kind','extractor','text_status','subtitle_status','warnings']})
                    if name=='direct_media_metadata':
                        media_id=item_id
                evidence['checks'].append(row);print(name,row['state'],row['items'],row['errors'],flush=True)
            crawl=await wait(await call('crawl_site',{'url':'https://quotes.toscrape.com/','max_pages':3,'depth':1,'render':'never'}))
            evidence['checks'].append({'name':'bounded_crawl','job_id':crawl['id'],'state':crawl['state'],'items':len(crawl['item_ids']),'errors':crawl['errors']})
            print('bounded_crawl',crawl['state'],len(crawl['item_ids']),flush=True)
            original_files_after = {path.name for path in (DATA/'downloads').glob('*') if path.is_file()}
            evidence['checks'].append({'name':'no_default_original_downloads','passed':original_files_before == original_files_after, 'existing_files':len(original_files_before),'new_original_files':len(original_files_after-original_files_before)})
            assert original_files_before == original_files_after
            pdf=await call('library_search',{'kind':'pdf','limit':5})
            target=next(item for item in pdf['items'] if item['title']=='dummy.pdf')
            downloaded=await wait(await call('download_asset',{'item_id':target['id'],'max_mb':1}))
            record=downloaded['downloads'][0] if downloaded['downloads'] else None
            assert record and Path(record['path']).exists(),downloaded
            assert hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()==record['sha256']
            evidence['checks'].append({'name':'explicit_pdf_download','passed':True,'job_id':downloaded['id'],'bytes':record['bytes'],'sha256':record['sha256']})
            print('explicit_pdf_download PASS',record['bytes'],flush=True)
            if 'media_id' in locals():
                video=await wait(await call('download_asset',{'item_id':media_id,'max_mb':15}))
                evidence['checks'].append({'name':'explicit_media_download','state':video['state'],'errors':video['errors'],'downloads':[{key:record[key] for key in ['filename','bytes','sha256']} for record in video['downloads']]})
                print('explicit_media_download',video['state'],flush=True)
    evidence['finished_at']=datetime.now(timezone.utc).isoformat()
    (ROOT/'evidence/workflows.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    asyncio.run(main())
