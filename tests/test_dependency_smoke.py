"""Offline regression checks for the locked web and parsing dependencies."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Fake values let SDK constructors initialize without reading local key files.
os.environ["NEWSDATA_KEY"] = "offline-test-key"
os.environ["GEMINI_API_KEY"] = "offline-test-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import app
from newspaper import Article


class DependencySmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_homepage_without_network(self):
        with patch.object(app.main, "get_topics", return_value={"top": [], "notable": []}):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<html", response.data.lower())

    def test_empty_stream(self):
        response = self.client.get("/api/article/stream")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"event: done", response.data)
        self.assertIn(b"Please enter a search term", response.data)

    def test_html_sanitizer_removes_active_content(self):
        cleaned = app._clean('<p>Hello</p><script>alert(1)</script><a href="javascript:alert(1)" onclick="alert(1)">link</a>')
        self.assertIn("<p>Hello</p>", cleaned)
        for unsafe in ("<script", "javascript:", "onclick"):
            self.assertNotIn(unsafe, cleaned)

    def test_article_parser_without_network(self):
        article = Article("https://example.com/test")
        article.download(input_html='<html><head><title>Offline parser test</title></head><body><article><p>' + ('This is a local news paragraph. ' * 50) + '</p></article></body></html>')
        article.parse()
        self.assertEqual(article.title, "Offline parser test")
        self.assertIn("local news paragraph", article.text)


if __name__ == "__main__":
    unittest.main()
