"""Source-site regions, independent of the language of retrieved content."""
import re
from urllib.parse import urlsplit

from .errors import CrawlerError

REGIONS = ('domestic', 'foreign', 'unknown')
REGION_LABELS = {'domestic': '国内', 'foreign': '国外', 'unknown': '未分类', 'all': '综合'}

DOMESTIC_SITES = (
    'bilibili.com', 'b23.tv', 'douyin.com', 'iesdouyin.com', 'kuaishou.com',
    'xiaohongshu.com', 'xhslink.com', 'zhihu.com', 'weibo.com', 'weibo.cn',
    'baidu.com', 'qq.com', '163.com', '126.com', 'sohu.com', 'sina.com',
    'csdn.net', 'cnblogs.com', 'jianshu.com', 'douban.com', 'juejin.cn', 'runoob.com',
    '36kr.com', 'huxiu.com', 'ifanr.com', 'sspai.com', 'thepaper.cn',
    'people.com.cn', 'xinhuanet.com', 'cctv.com', 'china.com', 'chinanews.com',
    'ithome.com', 'smzdm.com', 'taobao.com', 'tmall.com', 'jd.com', 'pinduoduo.com',
    'iqiyi.com', 'youku.com', 'mgtv.com', 'ximalaya.com', 'xiaoyuzhoufm.com',
    'cnki.net', 'wanfangdata.com.cn', 'cqvip.com', 'sciengine.com',
    'aminer.cn', 'paper.cn', 'cas.cn', 'gitee.com', 'v2ex.com', 'segmentfault.com',
)
FOREIGN_SITES = (
    'youtube.com', 'youtu.be', 'vimeo.com', 'tiktok.com', 'twitch.tv',
    'x.com', 'twitter.com', 'reddit.com', 'facebook.com', 'instagram.com',
    'linkedin.com', 'threads.net', 'pinterest.com', 'tumblr.com', 'medium.com',
    'substack.com', 'quora.com', 'stackoverflow.com', 'stackexchange.com',
    'github.com', 'gitlab.com', 'wikipedia.org', 'wikimedia.org',
    'google.com', 'bing.com', 'microsoft.com', 'duckduckgo.com', 'yahoo.com',
    'arxiv.org', 'crossref.org', 'doi.org', 'openalex.org', 'semanticscholar.org',
    'ncbi.nlm.nih.gov', 'pubmed.ncbi.nlm.nih.gov', 'nih.gov', 'biorxiv.org',
    'medrxiv.org', 'nature.com', 'science.org', 'sciencedirect.com', 'elsevier.com',
    'springer.com', 'springeropen.com', 'wiley.com', 'tandfonline.com',
    'ieee.org', 'acm.org', 'researchgate.net', 'jstor.org', 'zenodo.org',
    'osf.io', 'pnas.org', 'plos.org', 'frontiersin.org', 'mdpi.com',
    'python.org', 'w3.org', 'readthedocs.io', 'mozilla.org', 'openai.com', 'bbc.com', 'bbc.co.uk',
    'reuters.com', 'apnews.com', 'nytimes.com', 'theguardian.com',
    'archive.org', 'archiveofourown.org', 'wattpad.com',
)
FOREIGN_TLDS = set('us uk de fr jp kr au ca sg hk tw nz nl se no fi dk ch at es it br in za ru ua pl cz ie be pt mx tr il id my ph th vn'.split())


def source_host(url):
    try:
        return (urlsplit(url).hostname or '').lower().rstrip('.').removeprefix('www.')
    except ValueError:
        return ''


def within(host, domain):
    return host == domain or host.endswith('.' + domain)


def classify_source(url, overrides=None):
    host = source_host(url)
    for domain, region in sorted((overrides or {}).items(), key=lambda entry: len(entry[0]), reverse=True):
        if region in REGIONS and within(host, domain):
            return region
    from .platforms import platform_for_url
    platform = platform_for_url(url)
    if platform:
        return platform['region']
    if host.endswith(('.cn', '.xn--fiqs8s')) or any(within(host, site) for site in DOMESTIC_SITES):
        return 'domestic'
    if any(within(host, site) for site in FOREIGN_SITES) or host.rsplit('.', 1)[-1] in FOREIGN_TLDS:
        return 'foreign'
    return 'unknown'


def explicit_platform_region(query):
    from .platforms import parse_prefix
    platform = parse_prefix(query)
    if platform:
        return platform[0]['region']
    prefix = re.match(r'^(xhs|小红书|bili|bilibili|B站|dy|抖音|ks|快手|wb|微博|tieba|贴吧|zhihu|知乎|youtube|yt|twitter|x)[:：]', query, re.I)
    if not prefix:
        return None
    return 'foreign' if prefix[1].lower() in {'youtube', 'yt', 'twitter', 'x'} else 'domestic'


def check_scope(query, region):
    if region not in {'all', 'domestic', 'foreign'}:
        raise CrawlerError('INVALID_SOURCE_SCOPE', 'Source scope invalid', 'Select all, domestic, or foreign')
    platform_region = explicit_platform_region(query)
    if region != 'all' and platform_region and platform_region != region:
        raise CrawlerError('SOURCE_SCOPE_MISMATCH', 'Specified platform does not match selected source scope', 'Change source scope or platform prefix')


def scoped_query(query, region, kind='web', overrides=None):
    if region == 'all':
        return query
    domestic = list(DOMESTIC_SITES) + [host for host, value in (overrides or {}).items() if value == 'domestic']
    if region == 'foreign':
        return query + ' -site:cn ' + ' '.join('-site:' + site for site in domestic)
    if kind == 'paper':
        sites = ['cn', 'cnki.net', 'sciengine.com', 'cqvip.com']
        query += ' (论文 OR paper OR PDF)'
    elif kind == 'video':
        sites = ['bilibili.com/video', 'douyin.com/video', 'kuaishou.com', 'youku.com', 'iqiyi.com']
    else:
        sites = ['cn', *domestic]
    return query + ' (' + ' OR '.join('site:' + site for site in dict.fromkeys(sites)) + ')'
