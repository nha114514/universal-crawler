"""Merge real observations without counting empty shells as full platform support."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    auto = json.loads((ROOT/'evidence/universal-platform-auto-probe.json').read_text(encoding='utf-8'))
    mcp = json.loads((ROOT/'evidence/universal-url-mcp.json').read_text(encoding='utf-8'))
    observations = {row['source']: row for row in auto['results']}
    names = {'bilibili_home':'bilibili', 'douyin_home':'douyin', 'youku_home':'youku',
             'tencent_home':'tencent-video', 'reddit_gate':'reddit', 'x_profile':'x'}
    for row in mcp['checks']:
        platform = names.get(row['name'])
        if platform:
            observations[platform] = {'source':platform,'requested_url':row['url'],'errors':row['errors'],'items':row['items'],
                                      'job_id':row['job_id'],'state':row['state'],'origin':'final-mcp'}
    usable, limited, clues = [], [], []
    checks = []
    for key,row in observations.items():
        substantial = any(item.get('entries') or item.get('subtitle_chars') or item.get('body_chars',0)>=180 for item in row.get('items',[]))
        if row.get('errors'):
            limited.append(key)
            state,code = 'partial' if row.get('items') else 'failed', row['errors'][0]['code']
        elif substantial:
            usable.append(key)
            state,code = 'completed',None
        else:
            clues.append(key)
            state,code = 'metadata_only','SPARSE_PAGE_CLUES'
        observed_at = row.get('observed_at') if 'observed_at' in row else auto.get('captured_at')
        checks.append({'platform':key,'stage':'公开入口读取','state':state,'code':code,
                       'checked_at':observed_at,'url':row['requested_url'],
                       'scope':'仅此公开入口；不保证整站、受限正文或原文件下载', 'job_id':row.get('job_id')})
    summary = {'captured_at':datetime.now(timezone.utc).isoformat(),'checked':len(observations),
               'usable_public_entries':len(usable),'limited_public_entries':len(limited),'sparse_or_metadata_entries':len(clues),
               'usable_platforms':usable,'limited_platforms':limited,'sparse_platforms':clues,
               'definition':'Usable = actual entries/subtitles or at least 180 body characters without reported error. These are entry observations, not full-platform guarantees.',
               'mcp_protocol':mcp['protocol'],'final_mcp_checks':mcp['checks'], 'explicit_download':mcp.get('optional_download'),
               'production_code_sha256':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT/'crawler_system').glob('*.py'))},
               'sources':['universal-platform-static-probe.json','universal-platform-auto-probe.json','universal-public-aliases.json','universal-url-mcp.json'],
               'handoff_reconciliation':auto.get('handoff_reconciliation'),
               'session_checks_scope':'Explicit Chrome-session checks are recorded in handoff-review.json and do not replace automatic-entry observations.'}
    (ROOT/'evidence/universal-url-coverage-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'data/platform-checks.json').write_text(json.dumps({'checks':checks,'checked_at':summary['captured_at']},ensure_ascii=False,indent=2),encoding='utf-8')
    print({key:summary[key] for key in ('checked','usable_public_entries','limited_public_entries','sparse_or_metadata_entries')})
    print('Limited:', ', '.join(limited))
    print('Sparse:', ', '.join(clues))
