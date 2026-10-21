"""Read the public preload referenced by the anonymous landing page."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup
from crawler_system.network import Network

if __name__ == '__main__':
    for name in ['tencent-video', 'iqiyi', 'mgtv']:
        directory = ROOT / 'data/reverse' / name
        soup = BeautifulSoup((directory / 'public-page.html').read_bytes(), 'html.parser')
        print(name)
        for tag in soup.select('script'):
            text = tag.string or tag.get_text()
            if re.search(r'__channel_prefetch__|__NUXT__|qy_page_config_', text):
                print('state:', text[:900])
        if name == 'iqiyi':
            preload = next(tag['src'] for tag in soup.select('script[src]') if '/prelw/portal/' in tag['src'])
            response = Network().fetch(urljoin('https://www.iqiyi.com/', preload))
            (directory / 'public-preload.js').write_bytes(response.body)
            text = response.body.decode('utf-8')
            print('preload:', text[:600])
            print('keys:', list(dict.fromkeys(re.findall(r'"(\w+)"\s*:', text)))[:70])
