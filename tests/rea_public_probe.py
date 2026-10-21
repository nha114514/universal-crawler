"""Own a disposable, anonymous browser for a bounded REA public-page inspection."""
import json
from pathlib import Path
import socket
import sys
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data/reverse/universal-public-check'

if __name__ == '__main__':
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    stop = DIRECTORY / 'stop'
    stop.unlink(missing_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True,
                                              args=[f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1'])
        try:
            page = browser.new_page()
            url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.reddit.com/r/Python/'
            result = {'cdp_endpoint': f'http://127.0.0.1:{port}', 'requested_url': url,
                      'context': 'Disposable anonymous browser; personal profiles and cookies were not read.'}
            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
                result.update(url=page.url, http_status=response.status if response else None, title=page.title())
            except Exception as error:
                result['navigation_error'] = str(error)[:500]
            (DIRECTORY / 'endpoint.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + 180
            while not stop.exists() and time.monotonic() < deadline:
                page.wait_for_timeout(500)
        finally:
            browser.close()
