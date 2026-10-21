"""Public discovery directory, not a claim that every remote platform works."""
import re
from urllib.parse import urlsplit, urlunsplit

# Live checked public entry aliases. Unlisted URLs are never rewritten.
# evidence/universal-public-aliases.json records the actual HTTP responses.
_PUBLIC_ALIASES = {host: 'www.' + host for host in (
    'bilibili.com', 'douyin.com', 'iqiyi.com', 'mgtv.com', 'youku.com', 'acfun.cn', 'ixigua.com',
    'taptap.cn', 'toutiao.com', 'thepaper.cn', 'people.com.cn', 'xinhuanet.com', 'cctv.com',
    'sohu.com', '163.com', 'ifeng.com', 'ithome.com', 'ximalaya.com', 'cnki.net', 'runoob.com',
    'taobao.com', 'jd.com', 'huxiu.com',
)}


def preferred_public_url(url):
    parsed = urlsplit(url)
    alias = _PUBLIC_ALIASES.get(parsed.netloc.lower())
    if alias and parsed.scheme in {'http', 'https'}:
        return urlunsplit((parsed.scheme, alias, parsed.path, parsed.query, parsed.fragment))
    return url


# id, display name, source region, category, domains, selected-provider prefix.
_ROWS = [
    ('bilibili', 'Bilibili', 'domestic', 'video', 'bilibili.com b23.tv', 'bili'),
    ('douyin', 'Douyin', 'domestic', 'video', 'douyin.com iesdouyin.com', 'dy'),
    ('kuaishou', 'Kuaishou', 'domestic', 'video', 'kuaishou.com', 'ks'),
    ('youku', 'Youku', 'domestic', 'video', 'youku.com', ''),
    ('iqiyi', 'iQIYI', 'domestic', 'video', 'iqiyi.com', ''),
    ('tencent-video', 'Tencent Video', 'domestic', 'video', 'v.qq.com', ''),
    ('mgtv', 'Mango TV', 'domestic', 'video', 'mgtv.com', ''),
    ('xigua', 'Xigua Video', 'domestic', 'video', 'ixigua.com', ''),
    ('acfun', 'AcFun', 'domestic', 'video', 'acfun.cn', ''),
    ('haokan', 'Baidu Haokan', 'domestic', 'video', 'haokan.baidu.com', ''),
    ('xiaohongshu', 'Xiaohongshu', 'domestic', 'social', 'xiaohongshu.com xhslink.com', 'xhs'),
    ('zhihu', 'Zhihu', 'domestic', 'social', 'zhihu.com', 'zhihu'),
    ('weibo', 'Weibo', 'domestic', 'social', 'weibo.com weibo.cn', 'wb'),
    ('tieba', 'Baidu Tieba', 'domestic', 'social', 'tieba.baidu.com', 'tieba'),
    ('douban', 'Douban', 'domestic', 'social', 'douban.com', ''),
    ('v2ex', 'V2EX', 'domestic', 'social', 'v2ex.com', ''),
    ('wechat', 'WeChat Official Accounts', 'domestic', 'social', 'mp.weixin.qq.com', ''),
    ('jianshu', 'Jianshu', 'domestic', 'social', 'jianshu.com', ''),
    ('nga', 'NGA Community', 'domestic', 'social', 'nga.cn ngabbs.com', ''),
    ('taptap', 'TapTap', 'domestic', 'social', 'taptap.cn', ''),
    ('xueqiu', 'Xueqiu', 'domestic', 'social', 'xueqiu.com', ''),
    ('toutiao', 'Toutiao', 'domestic', 'news', 'toutiao.com', ''),
    ('thepaper', 'The Paper', 'domestic', 'news', 'thepaper.cn', ''),
    ('xinhuanet', 'Xinhua News', 'domestic', 'news', 'xinhuanet.com news.cn', ''),
    ('people', "People's Daily Online", 'domestic', 'news', 'people.com.cn', ''),
    ('cctv', 'CCTV', 'domestic', 'news', 'cctv.com', ''),
    ('chinanews', 'China News Service', 'domestic', 'news', 'chinanews.com', ''),
    ('netease', 'NetEase News', 'domestic', 'news', '163.com', ''),
    ('sohu', 'Sohu', 'domestic', 'news', 'sohu.com', ''),
    ('sina', 'Sina', 'domestic', 'news', 'sina.com.cn sina.com', ''),
    ('tencent-news', 'Tencent News', 'domestic', 'news', 'news.qq.com', ''),
    ('ifeng', 'iFeng', 'domestic', 'news', 'ifeng.com', ''),
    ('36kr', '36Kr', 'domestic', 'news', '36kr.com', ''),
    ('huxiu', 'Huxiu', 'domestic', 'news', 'huxiu.com', ''),
    ('ithome', 'IT Home', 'domestic', 'news', 'ithome.com', ''),
    ('ximalaya', 'Ximalaya', 'domestic', 'audio', 'ximalaya.com', ''),
    ('xiaoyuzhou', 'Xiaoyuzhou', 'domestic', 'audio', 'xiaoyuzhoufm.com', ''),
    ('qq-music', 'QQ Music', 'domestic', 'audio', 'y.qq.com', ''),
    ('netease-music', 'NetEase Cloud Music', 'domestic', 'audio', 'music.163.com', ''),
    ('qingting', 'Qingting FM', 'domestic', 'audio', 'qingting.fm', ''),
    ('cnki', 'CNKI', 'domestic', 'paper', 'cnki.net', ''),
    ('wanfang', 'Wanfang Data', 'domestic', 'paper', 'wanfangdata.com.cn', ''),
    ('cqvip', 'CQVIP', 'domestic', 'paper', 'cqvip.com', ''),
    ('sciengine', 'Science China / SciEngine', 'domestic', 'paper', 'sciengine.com', ''),
    ('aminer', 'AMiner', 'domestic', 'paper', 'aminer.cn', ''),
    ('chinaxiv', 'ChinaXiv', 'domestic', 'paper', 'chinaxiv.org', ''),
    ('sciencepaper', 'Sciencepaper Online', 'domestic', 'paper', 'paper.edu.cn', ''),
    ('csdn', 'CSDN', 'domestic', 'tech', 'csdn.net', ''),
    ('cnblogs', 'Cnblogs', 'domestic', 'tech', 'cnblogs.com', ''),
    ('juejin', 'Juejin', 'domestic', 'tech', 'juejin.cn', ''),
    ('segmentfault', 'SegmentFault', 'domestic', 'tech', 'segmentfault.com', ''),
    ('gitee', 'Gitee', 'domestic', 'tech', 'gitee.com', ''),
    ('sspai', 'sspai', 'domestic', 'tech', 'sspai.com', ''),
    ('baike', 'Baidu Baike', 'domestic', 'reference', 'baike.baidu.com', ''),
    ('runoob', 'Runoob', 'domestic', 'reference', 'runoob.com', ''),
    ('wenku', 'Baidu Wenku', 'domestic', 'reference', 'wenku.baidu.com', ''),
    ('taobao', 'Taobao / Tmall', 'domestic', 'commerce', 'taobao.com tmall.com', ''),
    ('jd', 'JD.com', 'domestic', 'commerce', 'jd.com', ''),
    ('pinduoduo', 'Pinduoduo', 'domestic', 'commerce', 'pinduoduo.com yangkeduo.com', ''),
    ('smzdm', 'SMZDM', 'domestic', 'commerce', 'smzdm.com', ''),
    ('youtube', 'YouTube', 'foreign', 'video', 'youtube.com youtu.be', 'youtube'),
    ('tiktok', 'TikTok', 'foreign', 'video', 'tiktok.com', ''),
    ('vimeo', 'Vimeo', 'foreign', 'video', 'vimeo.com', ''),
    ('dailymotion', 'Dailymotion', 'foreign', 'video', 'dailymotion.com', ''),
    ('twitch', 'Twitch', 'foreign', 'video', 'twitch.tv', ''),
    ('rumble', 'Rumble', 'foreign', 'video', 'rumble.com', ''),
    ('niconico', 'Niconico', 'foreign', 'video', 'nicovideo.jp', ''),
    ('x', 'X / Twitter', 'foreign', 'social', 'x.com twitter.com', 'x'),
    ('reddit', 'Reddit', 'foreign', 'social', 'reddit.com', ''),
    ('facebook', 'Facebook', 'foreign', 'social', 'facebook.com', ''),
    ('instagram', 'Instagram', 'foreign', 'social', 'instagram.com', ''),
    ('threads', 'Threads', 'foreign', 'social', 'threads.net threads.com', ''),
    ('linkedin', 'LinkedIn', 'foreign', 'social', 'linkedin.com', ''),
    ('quora', 'Quora', 'foreign', 'social', 'quora.com', ''),
    ('pinterest', 'Pinterest', 'foreign', 'social', 'pinterest.com', ''),
    ('tumblr', 'Tumblr', 'foreign', 'social', 'tumblr.com', ''),
    ('bluesky', 'Bluesky', 'foreign', 'social', 'bsky.app', ''),
    ('medium', 'Medium', 'foreign', 'social', 'medium.com', ''),
    ('substack', 'Substack', 'foreign', 'social', 'substack.com', ''),
    ('bbc', 'BBC', 'foreign', 'news', 'bbc.com bbc.co.uk', ''),
    ('reuters', 'Reuters', 'foreign', 'news', 'reuters.com', ''),
    ('apnews', 'Associated Press', 'foreign', 'news', 'apnews.com', ''),
    ('guardian', 'The Guardian', 'foreign', 'news', 'theguardian.com', ''),
    ('nytimes', 'New York Times', 'foreign', 'news', 'nytimes.com', ''),
    ('cnn', 'CNN', 'foreign', 'news', 'cnn.com', ''),
    ('dw', 'Deutsche Welle', 'foreign', 'news', 'dw.com', ''),
    ('aljazeera', 'Al Jazeera', 'foreign', 'news', 'aljazeera.com', ''),
    ('nikkei', 'Nikkei', 'foreign', 'news', 'nikkei.com', ''),
    ('soundcloud', 'SoundCloud', 'foreign', 'audio', 'soundcloud.com', ''),
    ('spotify', 'Spotify', 'foreign', 'audio', 'open.spotify.com', ''),
    ('apple-podcasts', 'Apple Podcasts', 'foreign', 'audio', 'podcasts.apple.com', ''),
    ('bandcamp', 'Bandcamp', 'foreign', 'audio', 'bandcamp.com', ''),
    ('arxiv', 'arXiv', 'foreign', 'paper', 'arxiv.org', ''),
    ('pubmed', 'PubMed / PMC', 'foreign', 'paper', 'pubmed.ncbi.nlm.nih.gov pmc.ncbi.nlm.nih.gov', ''),
    ('openalex', 'OpenAlex', 'foreign', 'paper', 'openalex.org', ''),
    ('crossref', 'Crossref / DOI', 'foreign', 'paper', 'crossref.org doi.org', ''),
    ('semantic-scholar', 'Semantic Scholar', 'foreign', 'paper', 'semanticscholar.org', ''),
    ('biorxiv', 'bioRxiv / medRxiv', 'foreign', 'paper', 'biorxiv.org medrxiv.org', ''),
    ('nature', 'Nature', 'foreign', 'paper', 'nature.com', ''),
    ('science', 'Science', 'foreign', 'paper', 'science.org', ''),
    ('sciencedirect', 'ScienceDirect', 'foreign', 'paper', 'sciencedirect.com', ''),
    ('springer', 'Springer', 'foreign', 'paper', 'springer.com link.springer.com', ''),
    ('wiley', 'Wiley', 'foreign', 'paper', 'wiley.com', ''),
    ('ieee', 'IEEE Xplore', 'foreign', 'paper', 'ieeexplore.ieee.org', ''),
    ('acm', 'ACM Digital Library', 'foreign', 'paper', 'dl.acm.org', ''),
    ('zenodo', 'Zenodo', 'foreign', 'paper', 'zenodo.org', ''),
    ('google-scholar', 'Google Scholar', 'foreign', 'paper', 'scholar.google.com', ''),
    ('researchgate', 'ResearchGate', 'foreign', 'paper', 'researchgate.net', ''),
    ('jstor', 'JSTOR', 'foreign', 'paper', 'jstor.org', ''),
    ('plos', 'PLOS', 'foreign', 'paper', 'plos.org journals.plos.org', ''),
    ('frontiers', 'Frontiers', 'foreign', 'paper', 'frontiersin.org', ''),
    ('mdpi', 'MDPI', 'foreign', 'paper', 'mdpi.com', ''),
    ('ssrn', 'SSRN', 'foreign', 'paper', 'ssrn.com', ''),
    ('osf', 'OSF', 'foreign', 'paper', 'osf.io', ''),
    ('github', 'GitHub', 'foreign', 'tech', 'github.com', ''),
    ('gitlab', 'GitLab', 'foreign', 'tech', 'gitlab.com', ''),
    ('stackoverflow', 'Stack Overflow / Exchange', 'foreign', 'tech', 'stackoverflow.com stackexchange.com', ''),
    ('hackernews', 'Hacker News', 'foreign', 'tech', 'news.ycombinator.com', ''),
    ('devto', 'DEV Community', 'foreign', 'tech', 'dev.to', ''),
    ('readthedocs', 'Read the Docs', 'foreign', 'tech', 'readthedocs.io', ''),
    ('huggingface', 'Hugging Face', 'foreign', 'tech', 'huggingface.co', ''),
    ('kaggle', 'Kaggle', 'foreign', 'tech', 'kaggle.com', ''),
    ('wikipedia', 'Wikipedia', 'foreign', 'reference', 'wikipedia.org', ''),
    ('archive', 'Internet Archive', 'foreign', 'reference', 'archive.org', ''),
    ('mdn', 'MDN', 'foreign', 'reference', 'developer.mozilla.org mdn.mozilla.net', ''),
    ('amazon', 'Amazon', 'foreign', 'commerce', 'amazon.com amazon.co.uk amazon.co.jp', ''),
    ('ebay', 'eBay', 'foreign', 'commerce', 'ebay.com', ''),
    ('etsy', 'Etsy', 'foreign', 'commerce', 'etsy.com', ''),
]

# Recognised only when the user pastes one of their URLs: never listed in the
# directory or added to keyword searches (adult sites must not leak into those).
_URL_ONLY_ROWS = [
    ('pornhub', 'Pornhub', 'foreign', 'video', 'pornhub.com', ''),
]


def catalog(region='all', mode='auto', rows=_ROWS):
    categories = {'paper'} if mode == 'paper' else {'video', 'audio'} if mode == 'video' else None
    return [dict(id=key, name=name, region=source, category=category, domains=domains.split(),
                 native_prefix=prefix, search='local_protocol' if key in {'bilibili', 'youtube'} else 'public_index',
                 verification='unverified', access_note='Public index discovery; full text, subtitles, and downloads are confirmed by actual tasks; login or paid content may not be available')
            for key, name, source, category, domains, prefix in rows
            if (region == 'all' or source == region) and (categories is None or category in categories)]


def platform_for_url(url):
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    matches = [row for row in catalog() + catalog(rows=_URL_ONLY_ROWS) if any(host == domain or host.endswith('.' + domain) for domain in row['domains'])]
    return max(matches, key=lambda row: max(len(domain) for domain in row['domains']), default=None)


def parse_prefix(query):
    match = re.match(r'^([^\s:：]+)[:：]\s*(.*)$', query.strip(), re.S)
    if not match or match[1].lower() in {'http', 'https', 'site', 'doi'}:
        return None
    aliases = {'bili': 'bilibili', 'b站': 'bilibili', 'dy': 'douyin', '抖音': 'douyin',
               'ks': 'kuaishou', '快手': 'kuaishou', 'xhs': 'xiaohongshu', '小红书': 'xiaohongshu',
               'wb': 'weibo', '微博': 'weibo', 'tieba': 'tieba', '贴吧': 'tieba',
               'zhihu': 'zhihu', '知乎': 'zhihu', 'yt': 'youtube', 'twitter': 'x', 'fb': 'facebook', 'ig': 'instagram'}
    name = aliases.get(match[1].lower(), match[1].lower())
    row = next((entry for entry in catalog() if name in {entry['id'].lower(), entry['name'].lower()}), None)
    return (row, match[2].strip()) if row else None


def validate_platforms(values):
    known = {row['id'] for row in catalog()}
    if any(value not in known for value in values):
        raise ValueError('Unknown platform, please select from platform catalog')
    return list(dict.fromkeys(values))
