from playwright.sync_api import sync_playwright

condition = """platform => {
    const root = document.querySelector('article, main') || document.body;
    if (!root) return 'no root';
    const copy = root.cloneNode(true);
    copy.querySelectorAll('script,style,nav,footer,header,aside,button,[hidden],[aria-hidden="true"]').forEach(e => e.remove());
    const text = copy.textContent.replace(/\\s+/g,' ').trim();
    const players = Array.from(document.querySelectorAll('video,audio'));
    const readyMedia = players.some(p => /^https?:/.test(p.currentSrc || p.src || p.querySelector('source')?.src || ''));
    return {
        hasRoot: Boolean(root),
        textLen: text.length,
        textSample: text.slice(0, 50),
        playersCount: players.length,
        readyMedia: readyMedia,
        conditionResult: Boolean(players.length ? readyMedia : (text.length >= 32 && text !== document.title && !/^(loading|正在加载|加载中)[. …]*$/i.test(text)))
    };
}"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto('https://www.toutiao.com/', wait_until='domcontentloaded')
    res = page.evaluate(condition, None)
    print('Condition debug on toutiao:', res)
    b.close()
