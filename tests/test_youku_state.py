import unittest
from crawler_system.network import Response
from crawler_system.parsers import parse_html


class YoukuStateTests(unittest.TestCase):
    def test_program_and_actual_video_ids_do_not_use_preview_clip(self):
        raw = '<title>优酷首页</title><script>window.__INITIAL_DATA__={"moduleList":[{"components":[{"itemList":[{"title":"公开节目","action_type":"JUMP_TO_SHOW","action_value":"abcdef123456","previewInfo":{"videoId":"XPreview"},"extra":undefined},{"name":"实际视频","videoIdStr":"XActual123","action_type":"JUMP_TO_VIDEO","action_value":"XActual123"}]}]}]};</script>'
        item = parse_html(Response('https://www.youku.com/ku/webhome', 200, {}, raw.encode()))
        self.assertEqual(len(item['entries']), 2)
        program, video = item['entries']
        self.assertEqual(program['kind'], 'web')
        self.assertIn('s=abcdef123456', program['url'])
        self.assertEqual(video['kind'], 'video')
        self.assertIn('vid=XActual123', video['url'])
        self.assertFalse(any('Preview' in asset['url'] for asset in item['assets']))

    def test_undefined_inside_title_is_preserved(self):
        from crawler_system.youku import initial_state
        raw = 'window.__INITIAL_DATA__={"title":"undefined is a word","optional":undefined,"escaped":"a\\\"b"};'
        value = initial_state(raw)
        self.assertEqual(value['title'], 'undefined is a word')
        self.assertIsNone(value['optional'])
        self.assertEqual(value['escaped'], 'a"b')

    def test_executable_expression_is_not_evaluated(self):
        from crawler_system.youku import initial_state
        self.assertIsNone(initial_state('window.__INITIAL_DATA__=(()=>{throw new Error("never execute")})()'))


if __name__ == '__main__':
    unittest.main()
