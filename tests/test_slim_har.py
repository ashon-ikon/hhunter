import unittest

from slim_har import is_important_html, slim_entry


class SlimHarTests(unittest.TestCase):
    def test_recognizes_har_sold_search_pages_as_important_html(self):
        self.assertTrue(is_important_html("https://www.har.com/realestatepro/sold-by-agent/cpt"))
        self.assertTrue(
            is_important_html(
                "https://www.har.com/search/dosearch?soldoffice=brda01&all_status=closd"
            )
        )

    def test_keeps_large_har_document_body_by_truncating_instead_of_dropping(self):
        entry = {
            "_resourceType": "document",
            "request": {
                "method": "GET",
                "url": "https://www.har.com/some-large-page",
                "httpVersion": "HTTP/2",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "headersSize": -1,
                "bodySize": 0,
            },
            "response": {
                "status": 200,
                "statusText": "OK",
                "httpVersion": "HTTP/2",
                "headers": [],
                "cookies": [],
                "content": {
                    "size": 4_000_000,
                    "mimeType": "text/html; charset=UTF-8",
                    "text": "A" * 4_000_000,
                },
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": 4_000_000,
            },
            "cache": {},
            "timings": {},
        }

        slimmed = slim_entry(entry, mode="share", max_body_bytes=750_000)

        self.assertIn("text", slimmed["response"]["content"])
        self.assertTrue(slimmed["response"]["content"].get("truncated", False))


if __name__ == "__main__":
    unittest.main()
