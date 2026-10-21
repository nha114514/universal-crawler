import unittest
from unittest.mock import patch

from crawler_system.network import Response
from crawler_system.parsers import parse_html
from crawler_system.router import collect
from crawler_system.web import collect_url
from tests.test_dynamic_pages import StubNetwork


URL = 'https://www.iqiyi.com/v_11mr3ddg90s.html?qyTrace=test&posterid=123'
SHELL = '<html><head><title>爱奇艺-在线视频网站-海量正版高清视频在线观看</title><meta name="description" content="爱奇艺-在线视频网站-海量正版高清视频在线观看"></head><body><div id="root">精彩即将开始</div><script src="app.js"></script></body></html>'
DETAIL = '''<html><head><title>逆行战场-电影-完整版视频在线观看-爱奇艺</title>
<meta name="description" content="一支队伍深入敌后营救战友的公开电影简介。">
</head><body><div><a href="/">首页导航</a><img src="https://example.org/unrelated-cover.png"></div>
<div class="meta_titleTextBlock__KkAoN">逆行战场</div>
<div class="meta_tagList__Dw7N-">7.2 分 2026年 历史战争</div>
<video src="blob:https://www.iqiyi.com/temporary-player"></video></body></html>'''


class IqiyiDetailTests(unittest.TestCase):
    def test_real_detail_is_video_metadata_without_navigation_or_fake_media(self):
        item = parse_html(Response(URL, 200, {}, DETAIL.encode(), True))
        self.assertEqual(item['kind'], 'video')
        self.assertEqual(item['title'], '逆行战场')
        self.assertIn('营救战友', item['abstract'])
        self.assertEqual(item['text_status'], 'metadata_only')
        self.assertEqual(item['content'], '')
        self.assertEqual(item['subtitle_status'], 'unchecked')
        self.assertEqual(item['metadata']['media_url_status'], 'not_extracted')
        self.assertIn('2026年', item['metadata']['detail_facts'])
        self.assertEqual(item['assets'], [{'url': URL, 'source_url': URL, 'name': 'Original Video (On-demand parsing)', 'kind': 'media'}])

    def test_auto_and_video_detail_do_not_enter_stalling_ytdlp_first(self):
        page = {'url': URL, 'kind': 'video', 'title': '逆行战场'}
        for mode in ['auto', 'video']:
            with self.subTest(mode=mode), patch('crawler_system.router.validate_url'), patch('crawler_system.router.collect_url', return_value=page) as fetch, patch('crawler_system.router.known_video', side_effect=AssertionError('must not enter yt-dlp detection')), patch('crawler_system.video.collect_video', side_effect=AssertionError('must not enter yt-dlp extraction')):
                self.assertEqual(collect(URL, mode=mode, network=object()), page)
                fetch.assert_called_once_with(URL, mode, unittest.mock.ANY, 'auto')

    def test_loading_poster_is_empty_and_dynamic_metadata_completes(self):
        shell = parse_html(Response(URL, 200, {}, SHELL.encode()))
        self.assertFalse(shell['content'])
        self.assertNotEqual(shell['kind'], 'video')
        network = StubNetwork(SHELL, DETAIL)
        item = collect_url(URL, network=network)
        self.assertEqual(network.calls, [False, True])
        self.assertEqual(item['kind'], 'video')
        self.assertFalse(item.get('retrieval_error'))

    def test_title_without_real_description_stays_incomplete(self):
        title_only = SHELL.replace('爱奇艺-在线视频网站-海量正版高清视频在线观看</title>', '逆行战场-电影-完整版视频在线观看-爱奇艺</title>')
        item = collect_url(URL, network=StubNetwork(title_only, title_only))
        self.assertNotEqual(item['kind'], 'video')
        self.assertTrue(item.get('retrieval_error'))

    def test_iqiyi_looking_host_does_not_gain_platform_metadata(self):
        item = parse_html(Response('https://iqiyi.com.example.org/v_11mr3ddg90s.html', 200, {}, DETAIL.encode(), True))
        self.assertNotEqual(item['kind'], 'video')


if __name__ == '__main__':
    unittest.main()
