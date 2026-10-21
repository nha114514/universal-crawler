import unittest
from crawler_system.network import Response
from crawler_system.parsers import parse_html


class TencentListTests(unittest.TestCase):
    def test_public_cards_ignore_adverts_and_preview_ids(self):
        from crawler_system.platform_lists import tencent_entries
        payload = {'ret': 0, 'data': {'CardList': [{'children_list': {
            'ad_list': {'cards': [{'params': {'title': 'Advertisement', 'cid': 'ad123456789', 'vid': 'ad987654321'}}]},
            'list': {'cards': [{'params': {'title': 'A programme', 'cid': 'mzc00200programme', 'cut_vid': 'preview1234'}},
                               {'params': {'title': 'Actual episode', 'cid': 'mzc00200episode', 'vid': 'x1234567890', 'window_vid': 'preview5678'}}]}
        }}]}}
        entries = tencent_entries(payload)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['kind'], 'web')
        self.assertEqual(entries[1]['kind'], 'video')
        self.assertTrue(entries[1]['url'].endswith('/x1234567890.html'))
        self.assertFalse(any('preview' in row['url'] or 'Advertisement' in row['title'] for row in entries))

    def test_failed_response_and_malformed_rows_do_not_make_entries(self):
        from crawler_system.platform_lists import tencent_entries
        self.assertEqual(tencent_entries({'ret': 1, 'data': {'CardList': []}}), [])
        section = {'children_list': {'list': {'cards': [None, {'params': 'invalid'}]}}}
        self.assertEqual(tencent_entries({'ret': 0, 'data': {'CardList': [None, section]}}), [])

    def test_runtime_homepage_advert_is_not_merged_as_a_video(self):
        response = Response('https://v.qq.com/', 200, {}, b'<title>Public catalogue</title><body></body>', True)
        response.media_entries = [
            {'url': response.url, 'media_url': 'https://example.org/advert.mp4', 'title': '', 'authors': [], 'kind': 'video'},
            {'url': 'https://v.qq.com/x/cover/mzc00200programme.html', 'title': 'Actual programme', 'authors': [], 'kind': 'web'}]
        item = parse_html(response)
        self.assertEqual(len(item['entries']), 1)
        self.assertEqual(item['entries'][0]['kind'], 'web')
        self.assertFalse(any('advert' in asset['url'] for asset in item['assets']))
