"""Anonymous public Youku client artifacts for focused REA inspection."""
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
    directory = ROOT / 'data/reverse/youku-client'
    directory.mkdir(parents=True, exist_ok=True)
    network = Network()
    response = network.fetch('https://www.youku.com/ku/webhome')
    soup = BeautifulSoup(response.body, 'html.parser')
    script = next(tag.get_text() for tag in soup.select('script') if 'window.__INITIAL_DATA__' in tag.get_text())
    (directory / 'initial-state.js').write_text(script, encoding='utf-8')
    match = re.search(r'window\.__INITIAL_DATA__\s*=\s*', script)
    raw = script[match.end():]
    try:
        json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError as error:
        print('JSON boundary:', raw[max(0,error.pos-80):error.pos+120])
    print({'undefined': raw.count('undefined'), 'functions': len(re.findall(r'function\s*\(', raw))})
    chunks = [urljoin(response.url, tag['src']) for tag in soup.select('script[src]') if 'Page.chunk.js' in tag['src']]
    for index, url in enumerate(chunks[:1]):
        page = network.fetch(url)
        (directory / 'Page.chunk.js').write_bytes(page.body)
        print({'script': url, 'bytes': len(page.body)})
    print('Artifacts:', str(directory))
