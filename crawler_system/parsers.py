from io import BytesIO
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from markdownify import markdownify

from .errors import CrawlerError
from .config import settings

FILE_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt", "json", "zip", "7z", "epub", "jpg", "jpeg", "png", "gif", "webp", "mp4", "mp3", "wav", "webm", "m4a", "mov", "ogg", "m3u8", "mpd"}


def file_extension(url):
    parsed = urlsplit(url)
    if (parsed.hostname or '').removeprefix('www.') == 'arxiv.org' and re.fullmatch(r'/pdf/(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?(?:\.pdf)?', parsed.path):
        return 'pdf'
    return parsed.path.rsplit('.', 1)[-1].lower()


def clean_text(value):
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def listing_platform(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if (host == "bilibili.com" and path in {"", "/index.html"}) or host in {"search.bilibili.com", "space.bilibili.com"}:
        return "bilibili"
    if host == "douyin.com" and path in {"", "/jingxuan", "/discover", "/recommend", "/search"}:
        return "douyin"
    if host == 'youku.com' and path == '/ku/webhome':
        return 'youku'
    if host == 'iqiyi.com' and path in {'', '/index.html'}:
        return 'iqiyi'
    if host == 'nicovideo.jp' and path in {'', '/index.html', '/video_top'}:
        return 'niconico'
    if host == 'pornhub.com' and (path in {'/video', '/video/search'} or re.fullmatch(r'/(?:categories|channels|model|pornstar)/[^/]+(?:/videos)?', path)):
        return 'pornhub'
    if path in {'', '/index.html', '/explore', '/feed', '/search', '/videos', '/discover'}:
        from .platforms import platform_for_url
        source = platform_for_url(url)
        if source and source['category'] == 'video':
            return source['id']
    return ""


def video_content_link(url, platform):
    from .platforms import platform_for_url
    source = platform_for_url(url)
    if not source or source['id'] != platform:
        return False
    parsed = urlsplit(url)
    patterns = {
        'bilibili': r'^/(?:video/|bangumi/play/)', 'douyin': r'^/video/\d+',
        'kuaishou': r'^/(?:short-video|video)/', 'youku': r'^/v_show/id_',
        'iqiyi': r'^/(?:v_|w_|a_)[^/]+\.html$', 'tencent-video': r'^/x/(?:cover|page)/',
        'mgtv': r'^/(?:b|h)/[^/]+/[^/]+\.html$', 'xigua': r'^/\d{8,}',
        'acfun': r'^/v/ac\d+', 'tiktok': r'^/@[^/]+/video/\d+',
        'youtube': r'^/(?:shorts|live)/', 'vimeo': r'^/\d+(?:/|$)',
        'dailymotion': r'^/video/', 'twitch': r'^/videos/\d+',
        'niconico': r'^/watch/(?:sm|so)?\d+',
    }
    if platform == 'youtube' and parsed.path == '/watch':
        return bool(parse_qs(parsed.query).get('v'))
    if platform == 'iqiyi' and parsed.path == '/tvg/to_page_url':
        qs = parse_qs(parsed.query)
        return bool(qs.get('album_id') or qs.get('tv_id'))
    if platform == 'haokan':
        return parsed.path == '/v' and bool(parse_qs(parsed.query).get('vid'))
    if platform == 'pornhub':
        return parsed.path == '/view_video.php' and bool(parse_qs(parsed.query).get('viewkey'))
    return bool(re.search(patterns.get(platform, r'(?!)'), parsed.path))


def douyin_feed_entries(payload):
    if payload.get("status_code") != 0:
        return []
    rows = payload.get("aweme_list") or ([payload["aweme_detail"]] if isinstance(payload.get("aweme_detail"), dict) else [])
    entries = []
    for row in rows[:200]:
        identifier = str(row.get("aweme_id", ""))
        video = row.get("video") or {}
        urls = video.get("download_addr", {}).get("url_list", []) or video.get("play_addr", {}).get("url_list", [])
        urls = [url for url in urls if isinstance(url, str) and url.startswith(("https://", "http://"))]
        if not re.fullmatch(r"\d{6,22}", identifier) or not urls:
            continue
        # The supplied public player URL refreshes expiring CDN redirects.
        media = next((url for url in urls if (urlsplit(url).hostname or "").removeprefix("www.") == "douyin.com" and urlsplit(url).path == "/aweme/v1/play/"), urls[0])
        entries.append({"url": "https://www.douyin.com/video/" + identifier, "kind": "video",
                        "title": str(row.get("desc") or identifier)[:500], "authors": [row.get("author", {}).get("nickname", "")],
                        "media_url": media, "duration": (video.get("duration") or 0) / 1000})
    return entries


def content_entries(soup, url, platform):
    entries, scores = {}, {}
    for anchor in soup.select("a[href]"):
        target = urljoin(url, anchor["href"])
        parsed = urlsplit(target)
        host = (parsed.hostname or "").removeprefix("www.")
        is_video = video_content_link(target, platform)
        if parsed.scheme not in {"http", "https"} or not is_video:
            continue
        if platform == 'pornhub':
            # Playlist links add pkey= to the same video; keep one entry per viewkey.
            target = f"{parsed.scheme}://{parsed.netloc}/view_video.php?viewkey={parse_qs(parsed.query)['viewkey'][0]}"
        heading = anchor.find(["h2", "h3"]) or anchor.find_parent(["h2", "h3"])
        picture = anchor.find('img')
        name = (heading.get_text(" ", strip=True) if heading else anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" ", strip=True) or (picture.get('alt') if picture else ''))
        if not name or re.fullmatch(r"[\d\s:万亿kKmMbB.+]+", name):
            if picture and picture.get('alt') and not re.fullmatch(r"[\d\s:万亿kKmMbB.+]+", picture.get('alt', '')):
                name = picture['alt']
            else:
                continue
        score = len(name) + (1000 if heading or anchor.get("title") else 0)
        key = target
        if platform == 'iqiyi' and parsed.path == '/tvg/to_page_url':
            qs = parse_qs(parsed.query)
            a_id = qs.get('album_id', [''])[0]
            t_id = qs.get('tv_id', [''])[0]
            if a_id or t_id:
                key = f"iqiyi:{a_id}:{t_id}"
        if score <= scores.get(key, -1):
            continue
        card = anchor.find_parent(class_=lambda value: value in {"bili-video-card", "video-card", "feed-card"})
        author = card.select_one('a[href*="space.bilibili.com"], [class*="author"], [class*="owner"]') if card else None
        is_programme = platform == 'iqiyi' and parsed.path.startswith('/a_')
        entries[key] = {"url": target, "title": name[:300], "kind": "web" if is_programme else "video", "authors": [author.get_text(" ", strip=True)] if author else []}
        if is_programme:
            entries[key]['content_type'] = 'program'
        scores[key] = score
    if platform == "douyin":
        # The live public feed uses div cards, not ordinary video anchors.
        # Only retain metadata actually present in the rendered card.
        for card in soup.select("[data-aweme-id]"):
            identifier = card.get("data-aweme-id", "")
            if not re.fullmatch(r"\d{6,22}", identifier):
                continue
            spans = card.find_all(["span", "p", "h2", "h3"])
            author_tag = next((tag for tag in spans if tag.get_text(" ", strip=True).startswith("@")), None)
            author = author_tag.get_text(" ", strip=True).lstrip("@ ").split("·", 1)[0].strip() if author_tag else ""
            titles = []
            for tag in spans:
                text = tag.get_text(" ", strip=True)
                in_author = any(parent.name == "span" and parent.get_text(" ", strip=True).startswith("@") for parent in tag.parents if parent is not card)
                if tag.find(["span", "p"]) or in_author or not text or text.startswith(("@", "·")) or re.fullmatch(r"[\d\s:.,万亿kKmMbB]+", text):
                    continue
                titles.append(text)
            if not titles:
                continue
            target = "https://www.douyin.com/video/" + identifier
            if target not in entries:
                entries[target] = {"url": target, "title": max(titles, key=len)[:500], "kind": "video", "authors": [author] if author else []}
    return list(entries.values())[:200]


def collection_entries(soup, url, source):
    patterns = {
        'reddit': r'^/r/[^/]+/comments/', 'x': r'^/[^/]+/status/\d+',
        'instagram': r'^/(?:p|reel)/', 'facebook': r'/posts/|/videos/|^/watch',
        'xiaohongshu': r'^/(?:explore|discovery/item)/[^/]+', 'weibo': r'^/(?:detail/\d+|\d+/[A-Za-z0-9]+)$',
        'zhihu': r'^/(?:question/\d+|p/\d+)', 'tieba': r'^/p/\d+',
        'douban': r'^/(?:note|subject|group/topic)/\d+', 'v2ex': r'^/t/\d+',
        'threads': r'^/@[^/]+/post/', 'bluesky': r'^/profile/[^/]+/post/',
        'pinterest': r'^/pin/\d+',
        'baike': r'^/item/', 'nytimes': r'/\d{4}/\d{2}/\d{2}/',
        '36kr': r'^/(?:p|newsflashes)/\d+', 'huxiu': r'^/article/\d+',
        'cnblogs': r'^/[^/]+/p/\d+', 'segmentfault': r'^/(?:a|blogs)/',
        'sspai': r'^/post/\d+', 'smzdm': r'^/(?:p|article)/\d+',
        'gitee': r'^/[^/]+/[^/]+$',
    }
    rows = {}
    for anchor in soup.select('a[href]'):
        if anchor.find_parent(['nav', 'header', 'footer', 'aside']) or anchor.find_parent(attrs={'role': 'dialog'}):
            continue
        target = urljoin(url, anchor['href'])
        parsed = urlsplit(target)
        if parsed.scheme not in {'http', 'https'} or target.split('#', 1)[0] == url.split('#', 1)[0]:
            continue
        key = source['id'] if source else ''
        if key in patterns and not re.search(patterns[key], parsed.path):
            continue
        heading = anchor.find(['h1', 'h2', 'h3', 'h4']) or anchor.find_parent(['h1', 'h2', 'h3', 'h4'])
        if not heading and key in patterns:
            card = anchor.find_parent('article')
            heading = card.find(['h1', 'h2', 'h3', 'h4']) if card else None
        name = heading.get_text(' ', strip=True) if heading else anchor.get('aria-label') or anchor.get('title') or ''
        if len(name.strip()) < 4 or not heading and key not in patterns:
            continue
        rows[target] = {'url': target, 'title': name[:500], 'kind': 'video' if video_content_link(target, key) else 'web', 'authors': []}
    return list(rows.values())[:200]


def parse_html(response, resources_only=False):
    soup = BeautifulSoup(response.body, "html.parser")
    from .platforms import platform_for_url
    source = platform_for_url(response.url)
    path = urlsplit(response.url).path.rstrip('/')
    platform = listing_platform(response.url)
    social_collection = bool(source and source['category'] == 'social' and not re.search(r'/(?:status|comments|p|reel|posts|post|videos|watch|question|answer|note|topic|subject|detail|item|explore)/', path + '/'))
    generic_collection = not platform and (social_collection or source and source['category'] in {'news', 'tech', 'reference', 'commerce'} and path in {'', '/news', '/world', '/technology', '/explore', '/blogs', '/post', '/pick', '/newsflashes', '/channels'}
                                           or path in {'', '/index.html'} and len(soup.find_all('article')) > 1)
    entries = content_entries(soup, response.url, platform) if platform else []
    if platform == 'youku':
        from .youku import public_entries
        by_url = {entry['url']: entry for entry in entries}
        for entry in public_entries(soup):
            by_url.setdefault(entry['url'], entry)
        entries = list(by_url.values())[:200]
    if generic_collection:
        entries = collection_entries(soup, response.url, source)
    media_entries = getattr(response, "media_entries", [])
    if platform:
        by_url = {entry["url"]: entry for entry in entries}
        for entry in media_entries:
            if entry['url'] == response.url or not entry.get('title'):
                continue
            by_url.setdefault(entry["url"], {}).update(entry)
        entries = list(by_url.values())[:200]
    has_app_root = soup.find(id=re.compile(r"^(root|app|__next|__nuxt)$", re.I)) is not None
    has_scripts = soup.find("script", attrs={"type": lambda value: value != "application/ld+json"}) is not None
    has_media = soup.find(['video', 'audio']) is not None
    login_form = soup.select_one('input[type="password"]') is not None
    has_article = soup.find('article') is not None
    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
            if tag and tag.get("content"):
                return tag["content"]
        return ""
    title = meta("citation_title", "og:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    h1 = soup.find("h1")
    title = title or (h1.get_text(" ", strip=True) if h1 else response.url)
    author = meta("citation_author", "author", "article:author")
    doi = meta("citation_doi", "dc.identifier")
    video_objects = []
    iqiyi_detail = None
    if video_content_link(response.url, 'iqiyi') and not path.startswith('/a_'):
        label = soup.select_one('[class*="meta_titleTextBlock__"]')
        match = re.match(r'(.+?)-(?:电影|电视剧|综艺|动漫|纪录片|儿童|片花|音乐|娱乐|体育|资讯)(?:-|$)', title)
        name = label.get_text(' ', strip=True) if label else match[1] if match else ''
        description = meta('description', 'og:description')
        if name and description and not description.startswith('爱奇艺-在线视频网站'):
            facts = soup.select_one('[class*="meta_tagList__"]')
            iqiyi_detail = {'@type': 'VideoObject', 'name': name, 'description': description}
            video_objects.append(iqiyi_detail)
            iqiyi_detail['detail_facts'] = facts.get_text(' ', strip=True) if facts else ''
    def visit(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, dict):
            kinds = value.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if "VideoObject" in kinds or "AudioObject" in kinds:
                video_objects.append({k: value.get(k) for k in ("@type", "name", "description", "duration", "uploadDate", "contentUrl", "embedUrl", "transcript", "thumbnailUrl", "author")})
            for key in ("@graph", "video", "audio", "mainEntity", "itemListElement", "item"):
                if key in value:
                    visit(value[key], depth + 1)
        elif isinstance(value, list):
            for child in value[:100]:
                visit(child, depth + 1)
    for script in soup.select('script[type="application/ld+json"]')[:20]:
        try:
            raw = script.string or script.get_text()
            if len(raw) <= 1000000:
                visit(json.loads(raw))
        except (ValueError, TypeError):
            pass
    assets, links, seen = [], [], set()
    for tag in soup.select("a[href], img[src], video[src], audio[src], source[src]"):
        target = urljoin(response.url, tag.get("href") or tag.get("src"))
        if not target.startswith(("https://", "http://")) or target in seen:
            continue
        seen.add(target)
        extension = 'pdf' if tag.get('type', '').lower() == 'application/pdf' else file_extension(target)
        name = tag.get_text(" ", strip=True) or tag.get("alt") or urlsplit(target).path.rsplit("/", 1)[-1]
        if extension in FILE_EXTENSIONS or tag.name in {"img", "video", "audio", "source"} or tag.has_attr("download"):
            parent_media = tag.find_parent(['video', 'audio']) if tag.name == 'source' else None
            media_kind = parent_media.name if parent_media else tag.name
            if tag.name == 'source' and not parent_media:
                media_kind = 'audio' if tag.get('type', '').startswith('audio/') else 'video'
            assets.append({"url": target, "name": name[:180], "kind": extension if extension in FILE_EXTENSIONS else media_kind})
        elif tag.name == "a":
            links.append({"url": target, "title": name[:180]})
    for data in video_objects:
        for key, kind in [('contentUrl', 'audio' if data.get('@type') == 'AudioObject' else 'video'), ('embedUrl', 'media')]:
            source = data.get(key)
            if isinstance(source, str):
                target = urljoin(response.url, source)
                if target.startswith(('http://', 'https://')) and target not in seen:
                    assets.append({'url': target, 'name': str(data.get('name') or 'Original Video/Audio')[:180], 'kind': kind, 'source_url': response.url})
                    seen.add(target)
    for tag in soup.select('iframe[src]')[:50]:
        target = urljoin(response.url, tag['src'])
        parsed = urlsplit(target)
        host = (parsed.hostname or '').lower()
        media_frame = any(host == domain or host.endswith('.' + domain) for domain in ('youtube.com', 'youtube-nocookie.com', 'vimeo.com', 'dailymotion.com', 'bilibili.com', 'tiktok.com')) and (re.search(r'/embed/|/player', parsed.path) or host.startswith('player.'))
        if parsed.scheme in {'http', 'https'} and media_frame and target not in seen:
            assets.append({'url': target, 'name': tag.get('title') or 'Embedded Video', 'kind': 'media', 'source_url': response.url})
            seen.add(target)
    pdf = meta("citation_pdf_url")
    if pdf and pdf not in seen:
        assets.insert(0, {"url": urljoin(response.url, pdf), "name": "Paper PDF", "kind": "pdf"})
    if platform:
        # Homepage players include adverts and previews. Only content-card or
        # matched public-feed media may supply an original-video download button.
        assets = [asset for asset in assets if asset['kind'] not in {'video', 'audio', 'source', 'media', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg'}]
    selected_media = next((entry for entry in media_entries if entry["url"] == response.url), None) if not platform else None
    video_sources = [entry for entry in entries if entry.get('kind') in {'video', 'audio'}] if platform else [entry for entry in media_entries if entry['url'] == response.url]
    for entry in reversed(video_sources):
        assets.insert(0, {"url": entry.get("media_url") or entry["url"], "source_url": entry["url"],
                          "name": (entry.get("title") or title) + (' · Page Media (Unconfirmed)' if entry.get('source') == 'observed-player-resource' else ''), "kind": entry.get('kind', 'video') if entry.get("media_url") else "media"})
    for asset in assets:
        if urlsplit(asset['url']).path.lower().endswith(('.m3u8', '.mpd')) and asset['kind'] in {'video', 'audio', 'source', 'm3u8', 'mpd'}:
            asset['kind'] = 'media'
    media_kinds = {'media', 'video', 'audio', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg'}
    assets.sort(key=lambda asset: 0 if asset['kind'] in media_kinds else 1)
    media_pending = has_media and not any(asset['kind'] in media_kinds for asset in assets)
    for tag in soup.select("script, style, noscript, nav, footer, header, aside, [hidden], [aria-hidden=true]"):
        tag.decompose()
    main = (soup.find('main') or soup.body or soup) if generic_collection else soup.find("article") or soup.find("main") or soup.body or soup
    content = '' if resources_only else markdownify(str(main), heading_style="ATX", strip=["img"]).strip()
    plain = main.get_text(" ", strip=True)
    without_links = BeautifulSoup(str(main), "html.parser")
    for tag in without_links.select("a, button, input, select"):
        tag.decompose()
    prose_chars = len(without_links.get_text(" ", strip=True))
    title_only = bool(plain) and re.sub(r"\W+", "", plain) == re.sub(r"\W+", "", title)
    loading_only = plain.casefold().strip(" .…!！") in {"loading", "loading...", "正在加载", "加载中", "请稍候"}
    app_shell = has_app_root and prose_chars < 30 and not main.find(["article", "p"])
    if entries and not resources_only:
        content = "\n\n".join(f"## {entry['title']}\n" + (", ".join(entry["authors"]) + "\n" if entry["authors"] else "") + entry["url"] for entry in entries)
    elif title_only or loading_only or app_shell:
        content = ""
    maximum = settings()["max_text_chars"]
    item = {"kind": "playlist" if entries and platform else "paper" if doi or meta("citation_title") else "web", "url": response.url,
            "title": title, "authors": [author] if author else [], "published": meta("citation_publication_date", "article:published_time", "date"),
            "doi": doi if re.search(r"10\.\d{4,9}/", doi) else "", "content": content[:maximum],
            "abstract": meta("citation_abstract", "description", "og:description") if doi else "",
            "assets": assets[:200], "links": links[:1000], "metadata": {"content_type": response.headers.get("content-type", ""), "document_title": soup.title.get_text(' ', strip=True) if soup.title else title, "video_objects": video_objects[:10], "open_graph_type": meta("og:type"),
                "page_type": "listing" if platform else "collection" if generic_collection else "page", "listing_platform": platform,
                "html_quality": {"has_scripts": has_scripts, "has_app_root": has_app_root, "prose_chars": prose_chars, "title_only": title_only, "app_shell": app_shell, "media_pending": media_pending, "login_form": login_form, "has_article": has_article},
                "render_diagnostics": getattr(response, "render_diagnostics", {})},
            "extractor": "scrapling-browser" if response.rendered else "scrapling-static", "http_status": response.status,
            "text_status": "listing" if entries else "full_text" if content else "empty", "truncated": len(content) > maximum, "warnings": []}
    if platform or generic_collection:
        item["entries"] = entries
    if selected_media and not generic_collection and not entries and len(plain) < 180:
        item.update(kind=selected_media.get('kind', 'video'), title=selected_media.get("title") or title, authors=selected_media.get("authors", []) or item['authors'], content="",
                    text_status="metadata_only", subtitle_status="unchecked", subtitle_text="")
        item["metadata"].update(duration=selected_media.get("duration"), public_video_response=True)
    elif not platform and not generic_collection and any(asset['kind'] in media_kinds for asset in assets) and len(plain) < 180:
        item.update(kind='audio' if assets[0]['kind'] in {'audio', 'mp3', 'wav', 'm4a', 'ogg'} else 'video',
                    content='', text_status='metadata_only', subtitle_status='unchecked', subtitle_text='',
                    abstract=meta('description', 'og:description'))
        transcript = next((data['transcript'] for data in video_objects if isinstance(data.get('transcript'), str) and data['transcript'].strip()), '')
        if transcript:
            item.update(subtitle_text=transcript[:maximum], subtitle_status='available', text_status='subtitles', subtitle_source='page')
    if iqiyi_detail:
        item.update(kind='video', title=iqiyi_detail['name'], content='', abstract=iqiyi_detail['description'],
                    text_status='metadata_only', subtitle_status='unchecked', subtitle_text='',
                    assets=[{'url': response.url, 'source_url': response.url, 'name': 'Original Video (On-demand parsing)', 'kind': 'media'}])
        item['metadata'].update(page_type='video_detail', video_details_source='iqiyi-public-dom',
                                detail_facts=iqiyi_detail['detail_facts'], media_url_status='not_extracted')
        item['warnings'].append('Public video details retrieved; subtitles and original video URL not yet retrieved, original file requires separate resolution/download')
    if resources_only:
        item.update(content='', abstract='', subtitles=[], subtitle_text='', subtitle_status='unchecked', text_status='resources')
        item['metadata']['resources_only'] = True
        for data in item['metadata']['video_objects']:
            data.pop('transcript', None)
            data.pop('description', None)
    return item


def parse_pdf(data, url):
    from pypdf import PdfReader
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise CrawlerError("PDF_ENCRYPTED", "PDF requires password", "Download original file and open with password locally")
        maximum = settings()["max_text_chars"]
        texts, size = [], 0
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            texts.append(f"\n\n--- Page {index + 1} ---\n\n{text}")
            size += len(texts[-1])
            if size >= maximum:
                break
        raw_text = "\n".join(texts)
        extracted = any((page.extract_text() or "").strip() for page in reader.pages[:1]) or bool(re.sub(r"--- (?:第 \d+ 页|Page \d+) ---|\s", "", raw_text))
        metadata = reader.metadata or {}
        return {"kind": "pdf", "url": url, "title": str(metadata.get("/Title") or urlsplit(url).path.rsplit("/", 1)[-1]),
                "content": raw_text[:maximum] if extracted else "", "authors": [str(metadata["/Author"])] if metadata.get("/Author") else [],
                "metadata": {"pages": len(reader.pages), "content_type": "application/pdf"}, "extractor": "pypdf", "http_status": 200,
                "text_status": "full_text" if extracted else "needs_ocr", "truncated": size >= maximum,
                "warnings": [] if extracted else ["Scanned or image PDF, no text extracted; OCR required"],
                "assets": [{"url": url, "name": "Original PDF", "kind": "pdf"}]}
    except CrawlerError:
        raise
    except Exception as error:
        raise CrawlerError("PDF_PARSE_ERROR", f"PDF parse failed: {type(error).__name__}", "Download original file to check format")


def xml_root(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise CrawlerError("UNSAFE_XML", "XML with external entities or DTD is not supported")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise CrawlerError("XML_PARSE_ERROR", "XML format cannot be parsed", "Check feed or API content")


def parse_feed(data, url):
    root = xml_root(data)
    atom = "{http://www.w3.org/2005/Atom}"
    entries = []
    is_atom = root.tag.endswith("}feed") or root.tag == "feed"
    container = root if is_atom else root.find("channel")
    if container is None:
        raise CrawlerError("FEED_PARSE_ERROR", "No RSS/Atom channel found")
    prefix = atom if root.tag.startswith(atom) else ""
    title = container.findtext(prefix + "title") or url
    for node in container.findall(prefix + "entry" if is_atom else "item")[:100]:
        link = node.find(prefix + "link" if is_atom else "link")
        target = (link.get("href", "") if is_atom else link.text or "") if link is not None else ""
        summary = node.findtext(prefix + "summary") or node.findtext(prefix + "content") or node.findtext("description") or ""
        entries.append({"url": urljoin(url, target), "title": node.findtext(prefix + "title") or "", "abstract": clean_text(summary),
                        "published": node.findtext(prefix + "published") or node.findtext("pubDate") or node.findtext(prefix + "updated") or ""})
    return {"kind": "feed", "url": url, "title": title, "content": "\n\n".join(f"## {e['title']}\n{e['abstract']}\n{e['url']}" for e in entries),
            "entries": entries, "links": [{"url": e["url"], "title": e["title"]} for e in entries], "assets": [],
            "extractor": "rss-atom", "text_status": "full_text", "metadata": {"entries": len(entries)}}

