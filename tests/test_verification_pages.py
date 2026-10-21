import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler_system.network import Response
from crawler_system.store import Store
from crawler_system.web import collect_html


class VerificationPageTests(unittest.TestCase):
    def response(self, url='https://pubmed.ncbi.nlm.nih.gov/31452104/'):
        return Response(url,200,{'content-type':'text/html'}, ('<html><head><title>正在检查您的浏览器 - reCAPTCHA</title></head><body><main><p>正在检查浏览器以便让您访问 pubmed.ncbi.nlm.nih.gov… 如果系统在 5 秒后未自动将您重定向，请点击此处。</p>'+ '<p>Checking your browser before accessing this site. Redirecting shortly.</p>'*6 +'</main></body></html>').encode())

    def test_http_200_verification_is_not_readable_body(self):
        item=collect_html(self.response(),object(),'never')
        self.assertEqual(item['content'],'')
        self.assertEqual(item['text_status'],'metadata_only')
        self.assertEqual(item['retrieval_error']['code'],'WEB_VERIFICATION_REQUIRED')

    def test_rendered_verification_is_not_ready_content(self):
        response=self.response()
        response.rendered=True
        item=collect_html(response,object(),'always')
        self.assertEqual(item['content'],'')
        self.assertTrue(item['metadata']['verification_page'])

    def test_verification_correction_clears_only_previous_false_body(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite3')
            bad=store.save_item({'url':self.response().url,'title':'正在检查您的浏览器 - reCAPTCHA','content':'正在检查浏览器以便让您访问…','text_status':'full_text'})
            corrected=store.save_item(collect_html(self.response(),object(),'never'))
            self.assertEqual(corrected['id'],bad['id'])
            self.assertEqual(corrected['content'],'')
            valid_url='https://www.python.org/tutorial'
            good=store.save_item({'url':valid_url,'title':'Python tutorial','content':'Actual useful tutorial '*200,'text_status':'full_text'})
            blocked=collect_html(self.response(valid_url),object(),'never')
            retained=store.save_item(blocked)
            self.assertEqual(retained['content'],good['content'])

    def test_long_article_discussing_captcha_remains_readable(self):
        response=Response('https://www.python.org/article',200,{},('<title>How reCAPTCHA works</title><article>'+ '<p>Explaining browser verification and research.</p>'*100 +'</article>').encode())
        item=collect_html(response,object(),'never')
        self.assertEqual(item['text_status'],'full_text')
        self.assertIsNone(item['retrieval_error'])

    def test_video_metadata_does_not_preserve_web_navigation_as_body(self):
        from crawler_system.video import fallback_video
        page={'url':'https://www.bilibili.com/video/BV1Test','title':'A video','content':'Home / Explore / Videos / Navigation','abstract':'A real description','assets':[], 'warnings':[], 'metadata':{'video_objects':[{'name':'A video'}]}}
        with patch('crawler_system.web.collect_url',return_value=page):
            item=fallback_video(page['url'],object(),'unavailable')
        self.assertEqual(item['content'],'')
        self.assertEqual(item['abstract'],'A real description')
