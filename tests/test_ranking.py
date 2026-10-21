import unittest
from unittest.mock import patch

from crawler_system.network import Response


class RankingTests(unittest.TestCase):
    def test_pagerank_normalizes_dangling_nodes_and_favors_linked_page(self):
        from crawler_system.ranking import page_rank
        scores = page_rank({"a": {"b"}, "b": set(), "c": {"b"}, "isolated": set()})
        self.assertAlmostEqual(sum(scores.values()), 1.0, places=7)
        self.assertGreater(scores["b"], scores["a"])
        self.assertAlmostEqual(scores["a"], scores["c"], places=7)
        self.assertEqual(page_rank({}), {})

    def test_link_graph_uses_observed_links_and_canonical_dedup(self):
        from crawler_system.ranking import link_graph, page_rank, prioritize
        graph = link_graph([{"url": "https://source.example/", "links": [{"url": "https://hub.example/?utm_source=x"}, {"url": "https://hub.example/"}, {"url": "http://127.0.0.1/"}]}])
        self.assertEqual(graph["https://source.example/"], {"https://hub.example/"})
        candidates = [{"url": "https://low.example/"}, {"url": "https://hub.example/"}]
        ranked = prioritize(candidates, graph)
        self.assertEqual(ranked[0]["url"], "https://hub.example/")
        self.assertEqual(ranked[0]["metadata"]["rank_scope"], "local-discovered-link-graph")
        self.assertGreater(ranked[0]["metadata"]["local_pagerank"], 0)

    def test_crawl_priority_reacts_to_new_links(self):
        from crawler_system.router import crawl_site
        visited = []
        pages = {
            "https://site.example/": ["a", "b"],
            "https://site.example/a": ["c"],
            "https://site.example/b": [],
            "https://site.example/c": [],
        }
        class Network:
            def fetch(self, url):
                return Response(url, 200, {}, b"User-agent: *\nAllow: /")
        def collect(url, *args):
            visited.append(url)
            return {"url": url, "title": url, "links": [{"url": "https://site.example/" + child} for child in pages[url]]}
        with patch("crawler_system.router.validate_url"), patch("crawler_system.router.Network", return_value=Network()), patch("crawler_system.router.collect", side_effect=collect):
            result = crawl_site("https://site.example/", depth=2, max_pages=4)
        self.assertEqual(visited, ["https://site.example/", "https://site.example/a", "https://site.example/c", "https://site.example/b"])
        self.assertEqual(result["ranking"]["algorithm"], "pagerank")

    def test_cross_site_follow_is_bounded_and_checks_each_robots(self):
        from crawler_system.router import crawl_site
        robots = []
        class Network:
            def fetch(self, url):
                robots.append(url)
                content = b"User-agent: *\nDisallow: /" if "blocked.example" in url else b"User-agent: *\nAllow: /"
                return Response(url, 200, {}, content)
        def collect(url, *args):
            return {"url": url, "title": url, "links": [{"url": "https://outside.example/page"}, {"url": "https://blocked.example/private"}] if "seed.example" in url else []}
        with patch("crawler_system.router.validate_url"), patch("crawler_system.router.Network", return_value=Network()), patch("crawler_system.router.collect", side_effect=collect):
            result = crawl_site("https://seed.example/", depth=1, max_pages=3, follow_external=True)
        self.assertEqual(len(result["items"]), 2)
        self.assertIn("https://outside.example/robots.txt", robots)
        self.assertTrue(any(error["code"] == "ROBOTS_DISALLOWED" for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
