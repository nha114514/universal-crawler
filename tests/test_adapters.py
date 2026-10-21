import unittest
import json

from crawler_system.papers import arxiv_items, crossref_item, search_papers
from crawler_system.video import parse_subtitles, video_item
from crawler_system.router import classify


class AdapterTests(unittest.TestCase):
    def test_paper_search_excludes_component_dois_and_fills_results(self):
        from urllib.parse import parse_qs, urlsplit
        from crawler_system.network import Response
        class FixtureNetwork:
            def fetch(self, url):
                if "arxiv.org" in url:
                    body = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
                else:
                    rows = int(parse_qs(urlsplit(url).query)["rows"][0])
                    records = [
                        {"DOI": "10.1000/paper/fig-10", "title": ["Figure 10"], "type": "component"},
                        {"DOI": "10.1000/paper", "title": ["Journal paper"], "type": "journal-article"},
                        {"DOI": "10.1000/conference", "title": ["Conference paper"], "type": "proceedings-article"},
                    ]
                    body = json.dumps({"message": {"items": records[:rows]}}).encode()
                return Response(url, 200, {}, body)
        result = search_papers("query", 2, FixtureNetwork())
        self.assertEqual([item["title"] for item in result["items"]], ["Journal paper", "Conference paper"])
        self.assertEqual(result["errors"], [])

    def test_paper_search_includes_both_sources_with_a_small_limit(self):
        from crawler_system.network import Response
        class FixtureNetwork:
            def fetch(self, url):
                if "arxiv.org" in url:
                    body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2401.00001</id><title>One</title></entry><entry><id>https://arxiv.org/abs/2401.00002</id><title>Two</title></entry></feed>'
                else:
                    body = json.dumps({"message": {"items": [{"DOI": "10.1000/published", "title": ["Published paper"]}]}}).encode()
                return Response(url, 200, {}, body)
        items = search_papers("query", 2, FixtureNetwork())["items"]
        self.assertEqual({item["extractor"] for item in items}, {"arxiv-api", "crossref-api"})

    def test_arxiv_authors_and_abstract_not_full_text(self):
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:a="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2401.00001v2</id><title>A title</title><summary>Only abstract</summary><author><name>Alice</name></author><author><name>Bob</name></author><published>2024-01-01</published><a:doi>10.1000/test</a:doi><link href="https://arxiv.org/pdf/2401.00001v2" title="pdf"/></entry></feed>'
        paper = arxiv_items(atom)[0]
        self.assertEqual(paper["authors"], ["Alice", "Bob"])
        self.assertEqual(paper["content"], "")
        self.assertEqual(paper["text_status"], "abstract")
        self.assertEqual(paper["assets"][0]["kind"], "pdf")

    def test_crossref_partial_metadata(self):
        paper = crossref_item({"DOI": "10.1000/test", "title": ["Title"], "abstract": "<jats:p>Abstract</jats:p>", "author": [{"given": "Alice", "family": "Smith"}], "published": {"date-parts": [[2024, 1, 2]]}})
        self.assertEqual(paper["content"], "")
        self.assertEqual(paper["abstract"], "Abstract")
        self.assertEqual(paper["published"], "2024-01-02")

    def test_subtitles_vtt_and_json3(self):
        vtt = b'WEBVTT\n\n00:00:01.500 --> 00:00:03.000\n<b>Hello</b>\n\n00:00:03.000 --> 00:00:05.000\nWorld\n'
        rows = parse_subtitles(vtt, "vtt")
        self.assertEqual(rows[0], {"start": 1.5, "end": 3.0, "text": "Hello"})
        rows = parse_subtitles(b'{"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"Hello"}]}]}', "json3")
        self.assertEqual(rows[0]["end"], 3)

    def test_missing_captions_not_success(self):
        item = video_item({"id": "abc", "webpage_url": "https://example.org/video", "title": "Video", "duration": 42})
        self.assertEqual(item["subtitle_status"], "missing")
        self.assertEqual(item["subtitle_text"], "")

    def test_url_doi_and_keyword_routing(self):
        self.assertEqual(classify("10.1000/test"), "doi")
        self.assertEqual(classify("https://doi.org/10.1000/test"), "doi")
        self.assertEqual(classify("https://arxiv.org/abs/2401.00001"), "arxiv")
        self.assertEqual(classify("https://example.org/article"), "url")
        self.assertEqual(classify("machine learning"), "query")
        self.assertEqual(classify("file:///C:/private.txt"), "url")


if __name__ == "__main__":
    unittest.main()
