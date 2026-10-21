"""Fresh MCP read/action/read, selected browser session and real optional MP4 download."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crawler_system.config import runtime_env
from tests.verify_platform_expansion import payload, wait


async def main():
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    output = ROOT / 'evidence/universal-url-mcp.json'
    def save():
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    params = StdioServerParameters(command=str(ROOT / '.venv/Scripts/python.exe'), args=['-m', 'crawler_system.mcp_server'], cwd=str(ROOT), env=runtime_env())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            status = payload(await session.call_tool('tool_status', {}))
            evidence['protocol'] = {'tools': len(tools.tools), 'browser_session_connected': status.get('browser_session_connected'), 'storage_root': status['storage']['root']}
            save()
            cases = [
                ('article_with_video', 'https://www.w3schools.com/html/html5_video.asp', 'auto'),
                ('chrome_session', 'https://docs.python.org/3/tutorial/index.html', 'session'),
                ('bilibili_home', 'https://www.bilibili.com/', 'auto'),
                ('douyin_home', 'https://www.douyin.com/', 'auto'),
                ('youku_home', 'https://www.youku.com/ku/webhome', 'auto'),
                ('tencent_home', 'https://v.qq.com/', 'auto'),
                ('paper', 'https://arxiv.org/abs/1706.03762', 'auto'),
                ('youtube_subtitles', 'https://www.youtube.com/watch?v=jNQXAC9IVRw', 'auto'),
                ('reddit_gate', 'https://www.reddit.com/r/Python/', 'auto'),
                ('x_profile', 'https://x.com/NASA', 'auto'),
                ('optional_mp4', 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', 'auto'),
            ]
            for name, url, render in cases:
                job = payload(await session.call_tool('collect', {'inputs': [url], 'render': render}))
                final = await wait(session, job['id'], timeout=360)
                row = {'name': name, 'url': url, 'job_id': job['id'], 'state': final['state'], 'errors': final['errors'], 'download_count': len(final['downloads']), 'items': []}
                assert not final['downloads'], 'Collection must not download originals'
                for item_id in final['item_ids']:
                    item = payload(await session.call_tool('get_item', {'item_id': item_id, 'max_chars': 300000}))
                    assert Path(item['material_path']).is_file()
                    row['items'].append({**{key: item.get(key) for key in ('id', 'url', 'title', 'kind', 'text_status', 'extractor', 'source_region', 'subtitle_status', 'material_path', 'retrieval_error')},
                                         'body_chars': len(item.get('content', '')), 'subtitle_chars': len(item.get('subtitle_text', '')), 'entries': len(item.get('entries', [])),
                                         'assets': len(item.get('assets', [])), 'media_assets': sum(asset.get('kind') in {'media', 'video', 'audio', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg'} for asset in item.get('assets', []))})
                    if name == 'optional_mp4' and not final['errors']:
                        download_job = payload(await session.call_tool('download_asset', {'item_id': item_id, 'asset_index': 0, 'max_mb': 10}))
                        downloaded = await wait(session, download_job['id'], timeout=360)
                        evidence['optional_download'] = {'job_id': download_job['id'], 'state': downloaded['state'], 'errors': downloaded['errors'], 'downloads': downloaded['downloads']}
                        if downloaded['downloads']:
                            file = Path(downloaded['downloads'][0]['path'])
                            assert file.is_file()
                            evidence['optional_download']['actual_bytes'] = file.stat().st_size
                            evidence['optional_download']['actual_sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
                            assert evidence['optional_download']['actual_sha256'] == downloaded['downloads'][0]['sha256']
                evidence['checks'].append(row)
                save()
                print(name, final['state'], [(item['kind'], item['body_chars'], item['entries'], item['media_assets']) for item in row['items']], [error['code'] for error in final['errors']], flush=True)
    save()


if __name__ == '__main__':
    asyncio.run(main())
