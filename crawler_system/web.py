import re
from urllib.parse import urlsplit, urlunsplit

from .errors import CrawlerError
from .network import Network
from .parsers import parse_html, parse_pdf, parse_feed, clean_text, listing_platform


def rendering_reason(response, item):
    if listing_platform(response.url):
        return "Dynamic platform homepage or content list"
    quality = item["metadata"]["html_quality"]
    from .platforms import platform_for_url
    source = platform_for_url(response.url)
    if source and source['id'] in {'x', 'instagram', 'facebook', 'reddit', 'threads', 'bluesky', 'pinterest', 'tumblr', 'xiaohongshu', 'weibo', 'zhihu', 'tieba'} and quality['has_scripts'] and not quality.get('has_article') and quality['prose_chars'] < 2000:
        return 'Social page requires checking dynamic body or data'
    if quality.get('media_pending'):
        return "Player URL not loaded yet, dynamic media check needed"
    if quality["app_shell"]:
        return "App page only retrieved navigation or placeholder shell"
    if len(clean_text(item["content"])) < 180 and (quality["has_scripts"] or len(item["content"].strip()) < 30):
        return "Static content insufficient, async loading check needed"
    return ""


def incomplete(item, code, message, action):
    item["retrieval_error"] = {"code": code, "message": message, "action": action}
    item["text_status"] = "partial" if item.get("content") else "metadata_only"
    item["warnings"].append(message + "; " + action)
    return item


def verification_page(item):
    title = ((item.get('title') or '') + ' ' + item.get('metadata', {}).get('document_title', '')).casefold()
    content = (item.get('content') or '').casefold()
    title_gate = re.search(r'checking your browser|正在检查(?:您的)?浏览器|verify (?:you are|that you)|just a moment|attention required|安全验证|security check|prove your humanity|sina visitor system|访问验证|访问异常|安全检查', title)
    body_gate = re.search(r"checking your browser (?:before|to)|正在检查浏览器以便让您访问|verify you are human|prove your humanity|正在验证您是否|enable javascript and cookies to continue|(?:you have|you.ve) been blocked by network security", content)
    titles = [item.get('title') or '', item.get('metadata', {}).get('document_title') or '']
    denial_title = any(re.fullmatch(r'(?:403\s+forbidden|access denied)(?:\s*[-|·]\s*(?:nginx|cloudflare))?', value.strip(), re.I) for value in titles)
    denial_title = denial_title and not (item.get('metadata', {}).get('html_quality', {}).get('has_article') and len(content) >= 180)
    denial_body = (not item.get('metadata', {}).get('html_quality', {}).get('has_article')
                   and re.fullmatch(r'(?:403\s+forbidden|access denied)(?:\s+(?:nginx|cloudflare))?', clean_text(content), re.I))
    return bool(title_gate or body_gate and len(content) < 2000 or denial_title or denial_body)


def verification_result(item):
    item['content'] = ''
    item.update(kind='web', assets=[], entries=[])
    item['metadata']['verification_page'] = True
    return incomplete(item, 'WEB_VERIFICATION_REQUIRED', 'Page returned browser/CAPTCHA verification; body text not yet retrieved', 'Check access and verification requirements on the site, or use other public sources')


def login_page(item):
    title = ((item.get('title') or '') + ' ' + item.get('metadata', {}).get('document_title', '')).casefold().strip()
    quality = item.get('metadata', {}).get('html_quality', {})
    short = len(clean_text(item.get('content', ''))) < 2000
    title_gate = re.search(r'^(?:log[ -]?in|sign[ -]?in)(?:\s+to|\s*[-|·]|$)|[-|•·]\s*(?:log in|sign in)(?:\s|$)|^登录(?:\s|[-|·]|$)', title)
    path_gate = re.search(r'/(?:login|signin|sign-in|passport)(?:/|$)', urlsplit(item.get('url', '')).path)
    prompt = re.search(r'please (?:log in|sign in) to continue|登录后(?:查看|继续)|请先登录', item.get('content', ''), re.I)
    signup_wall = re.search(r'注册就不会再错过.+的帖子|sign up to see (?:photos|videos|posts)|log in to (?:see|view) (?:more|this|posts)', item.get('content', ''), re.I)
    return bool(short and (title_gate and (quality.get('login_form') or not quality.get('has_article'))
                           or path_gate and quality.get('login_form') or prompt and quality.get('login_form') or signup_wall))


def login_result(item):
    item['content'] = ''
    item.update(kind='web', assets=[], entries=[])
    item['metadata']['login_page'] = True
    return incomplete(item, 'WEB_LOGIN_REQUIRED', 'Page returned login interface only; body text not yet retrieved', 'Log in on the corresponding website before crawling accessible content, or use a public source')


def collect_html(response, network, render, reason="", static_error=None):
    item = parse_html(response)
    if login_page(item):
        if response.rendered or render == 'never' or static_error:
            return login_result(item)
        reason = 'Static page returned login interface only'
    elif verification_page(item):
        if response.rendered or render == 'never' or static_error:
            return verification_result(item)
        reason = 'Static page requires browser verification'
    if not response.rendered and render == "auto" and static_error is None:
        reason = reason or rendering_reason(response, item)
        if reason:
            try:
                rendered = parse_html(network.fetch(response.url, render=True))
                if login_page(rendered):
                    return login_result(rendered)
                if verification_page(rendered):
                    return verification_result(rendered)
                ready = bool(rendered.get("entries")) if listing_platform(response.url) else bool(rendered["content"] or any(a.get('kind') in {'video', 'audio', 'media', 'mp4', 'webm', 'mp3', 'm4a'} for a in rendered.get('assets', [])))
                if ready:
                    item = rendered
                else:
                    return incomplete(item, "DYNAMIC_CONTENT_INCOMPLETE", "Browser did not retrieve valid body or content list; keeping available static content", "Retry later, or verify on the platform whether login/verification is required")
            except CrawlerError as error:
                if error.code in {"PRIVATE_URL", "INVALID_URL", "SIZE_LIMIT"}:
                    raise
                if verification_page(item):
                    return verification_result(item)
                if login_page(item):
                    return login_result(item)
                return incomplete(item, "DYNAMIC_RENDER_FAILED", f"Dynamic crawl incomplete, static content retained: {error}", error.action)
    item["metadata"]["render_reason"] = reason or ("Browser specified by user" if render == "always" else "Static page content available")
    item["retrieval_error"] = None
    item['metadata']['verification_page'] = False
    item['metadata']['login_page'] = False
    if static_error:
        return incomplete(item, "DYNAMIC_RENDER_FAILED", f"Dynamic crawl incomplete, static content retained: {static_error}", static_error.action)
    if item["metadata"]["page_type"] == "listing" and not item.get("entries"):
        item["content"] = ""
        return incomplete(item, "DYNAMIC_CONTENT_INCOMPLETE" if render != "never" else "STATIC_CONTENT_INCOMPLETE",
                          "Homepage video or content list not yet retrieved; images, titles, and layout shells do not count as a complete crawl", "Retry later, or verify on the platform whether login/verification is required")
    if response.rendered and not item["content"] and item.get('kind') not in {'video', 'audio'}:
        return incomplete(item, "DYNAMIC_CONTENT_INCOMPLETE", "Browser has not retrieved valid body text, only obtained layout shell or loading indicator", "Retry later, or check site login/verification requirements")
    if item.get('kind') == 'web' and not item.get('entries') and len(clean_text(item.get('content', ''))) < 180 and not item['metadata']['html_quality'].get('has_article'):
        return incomplete(item, 'WEB_CONTENT_INCOMPLETE', 'Only a small amount of page text was retrieved; body text completeness unverified', 'Retain these clues; you may specify a login session or retry later')
    return item


def collect_url(url, mode="auto", network=None, render="auto"):
    if render == 'session':
        from .browser_session import collect_session
        return collect_session(url)
    allow_session = network is None or isinstance(network, Network)
    try:
        item = _collect_url(url, mode, network, render)
    except CrawlerError as error:
        if not allow_session or render != 'auto' or error.code not in {'NETWORK_FAILURE', 'LOGIN_OR_BLOCKED'}:
            raise
        from .browser_session import collect_session
        try:
            item = collect_session(url)
        except CrawlerError as bridge_error:
            if bridge_error.code in {'PRIVATE_URL', 'INVALID_URL'}:
                raise
            raise error
        item['metadata']['previous_attempt'] = error.as_dict()
        return item
    error = item.get('retrieval_error')
    if allow_session and render == 'auto' and error and error.get('code') in {'WEB_LOGIN_REQUIRED', 'WEB_VERIFICATION_REQUIRED', 'DYNAMIC_RENDER_FAILED', 'DYNAMIC_CONTENT_INCOMPLETE', 'WEB_CONTENT_INCOMPLETE'}:
        from .browser_session import collect_session
        try:
            browser_item = collect_session(url)
            if not browser_item.get('retrieval_error'):
                browser_item['metadata']['previous_attempt'] = error
                return browser_item
            item['metadata']['browser_session_error'] = browser_item['retrieval_error']
        except CrawlerError as bridge_error:
            if bridge_error.code in {'PRIVATE_URL', 'INVALID_URL'}:
                raise
            item['metadata']['browser_session_error'] = bridge_error.as_dict()
    return item


def _collect_url(url, mode="auto", network=None, render="auto"):
    network = network or Network()
    requested_url = url
    from .platforms import preferred_public_url
    url = preferred_public_url(url)
    parsed = urlsplit(url)
    public_douyin = False
    if render != "never" and listing_platform(url) == "douyin" and not parsed.path.strip("/"):
        # This public entry was observed in a real anonymous page and feed.
        url = urlunsplit((parsed.scheme, parsed.netloc, "/jingxuan", parsed.query, parsed.fragment))
        public_douyin = True
    initial_path = urlsplit(url).path.lower()
    binary_extensions = (".zip", ".7z", ".rar", ".exe", ".epub", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mp3", ".wav", ".m4a")
    if initial_path.endswith(binary_extensions):
        response = network.fetch(url, method="HEAD")
        return {"kind": "file", "url": response.url, "title": initial_path.rsplit("/", 1)[-1], "content": "",
                "metadata": {"content_type": response.headers.get("content-type", ""), "bytes": int(response.headers.get("content-length", "0")) or None},
                "assets": [{"url": response.url, "name": "Original File", "kind": initial_path.rsplit(".", 1)[-1]}],
                "extractor": "file-metadata", "text_status": "metadata_only", "http_status": response.status,
                "warnings": ["File metadata saved, original file can be downloaded on demand"]}
    reason = "Dynamic platform homepage or content list" if render == "auto" and listing_platform(url) else ""
    static_error = None
    try:
        response = network.fetch(url, render=render == "always" or bool(reason))
    except CrawlerError as error:
        if not reason and render == 'auto' and error.code in {'LOGIN_OR_BLOCKED', 'NETWORK_FAILURE'}:
            response = network.fetch(url, render=True)
            reason = 'Static request restricted; attempted browser retrieval of public page'
        elif not reason or error.code in {"PRIVATE_URL", "INVALID_URL", "SIZE_LIMIT"}:
            raise
        else:
            static_error = error
            response = network.fetch(url)
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith(('video/', 'audio/')):
        kind = 'audio' if content_type.startswith('audio/') else 'video'
        length = response.headers.get('content-length', '')
        return {'kind': kind, 'url': response.url, 'title': urlsplit(response.url).path.rsplit('/', 1)[-1] or response.url,
                'content': '', 'metadata': {'content_type': content_type, 'bytes': int(length) if length.isdecimal() else None},
                'assets': [{'url': response.url, 'name': 'Original Video/Audio', 'kind': kind}], 'extractor': 'media-metadata',
                'text_status': 'metadata_only', 'subtitle_status': 'unchecked', 'http_status': response.status,
                'warnings': ['Original media URL identified; original file can be downloaded on demand']}
    prefix = response.body[:200].lstrip()
    path = urlsplit(response.url).path.lower()
    if "application/pdf" in content_type or prefix.startswith(b"%PDF-"):
        return parse_pdf(response.body, response.url)
    if any(x in content_type for x in ("application/rss", "application/atom")) or prefix.startswith((b"<rss", b"<feed")) or (prefix.startswith(b"<?xml") and (b"<feed" in response.body[:1000] or b"<rss" in response.body[:1000])):
        return parse_feed(response.body, response.url)
    if "html" in content_type or b"<html" in prefix.lower() or b"<!doctype html" in prefix.lower():
        item = collect_html(response, network, render, reason, static_error)
        if requested_url != url:
            item["source_urls"] = [requested_url, item["url"]]
            item["metadata"]["requested_url"] = requested_url
            item["metadata"]["public_entry"] = url
            item["warnings"].append("Homepage content retrieved via public featured entry" if public_douyin else "Crawled via verified public www entry of same site, preserving original URL association")
        return item
    is_text = content_type.startswith("text/") or any(x in content_type for x in ("json", "xml", "csv")) or path.endswith((".txt", ".csv", ".json", ".md", ".xml"))
    if is_text:
        maximum = network.config["max_text_chars"]
        text = response.body.decode("utf-8-sig", errors="replace")
        return {"kind": "text", "url": response.url, "title": path.rsplit("/", 1)[-1] or response.url, "content": text[:maximum],
                "metadata": {"content_type": content_type, "bytes": len(response.body)}, "assets": [{"url": response.url, "name": "Original File", "kind": "text"}],
                "extractor": "text", "http_status": response.status, "text_status": "full_text", "truncated": len(text) > maximum}
    return {"kind": "file", "url": response.url, "title": path.rsplit("/", 1)[-1] or response.url, "content": "",
            "metadata": {"content_type": content_type, "bytes": len(response.body)}, "assets": [{"url": response.url, "name": "Original File", "kind": "file"}],
            "extractor": "file-metadata", "http_status": response.status, "text_status": "metadata_only", "warnings": ["Metadata saved for this format; original file can be downloaded on demand"]}
