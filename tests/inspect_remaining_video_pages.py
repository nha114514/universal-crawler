"""Inspect anonymous public card URLs/state names before declaring adapters blocked."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup
from crawler_system.network import Network
from crawler_system.errors import CrawlerError

if __name__ == '__main__':
    rows = []
    for name, url in [('tencent-video','https://v.qq.com/'), ('iqiyi','https://www.iqiyi.com/'), ('mgtv','https://www.mgtv.com/')]:
        row = {'source': name, 'url': url}
        network = Network()
        network.config = {**network.config, 'request_timeout': 8}
        try:
            response = network.fetch(url)
            soup = BeautifulSoup(response.body, 'html.parser')
            directory = ROOT / 'data/reverse' / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'public-page.html').write_bytes(response.body)
            links = []
            for tag in soup.select('a[href]'):
                target = urljoin(response.url, tag['href'])
                if re.search(r'/x/cover/|/v_|/a_|/b/', target):
                    links.append({'url': target, 'title': tag.get('title'), 'text': tag.get_text(' ',strip=True)[:150],
                                  'alt': (tag.find('img') or {}).get('alt')})
            row.update(http_status=response.status, bytes=len(response.body), content_links=links[:20],
                       state_variables=list(dict.fromkeys(re.findall(r'window\.(\w+)\s*=', response.body.decode('utf-8',errors='replace')))),
                       json_scripts=[{'id': tag.get('id'), 'type': tag.get('type'), 'bytes': len(tag.get_text())} for tag in soup.select('script') if tag.get('type') == 'application/json'],
                       player_scripts=[urljoin(response.url, tag['src']) for tag in soup.select('script[src]')][:8])
        except CrawlerError as error:
            row['error'] = error.as_dict()
        rows.append(row)
    (ROOT / 'evidence/universal-remaining-video-inspection.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(rows, ensure_ascii=False, indent=2))
