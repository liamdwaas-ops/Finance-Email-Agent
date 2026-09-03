import unittest
from unittest.mock import patch

from podcast_digest import Podcast, book_url, parse_feed, render, transcript_from_html


class PodcastDigestTests(unittest.TestCase):
    def test_parses_rss_episode(self):
        feed = b'''<rss><channel><item><title>Episode 1</title><link>https://example.com/1</link><pubDate>Thu, 03 Sep 2026 08:00:00 +0000</pubDate><description>Notes</description></item></channel></rss>'''
        with patch("podcast_digest.fetch", return_value=feed):
            episodes = parse_feed(Podcast("Test", "https://example.com", "https://example.com/feed"))
        self.assertEqual(episodes[0]["title"], "Episode 1")
        self.assertEqual(episodes[0]["description"], "Notes")

    def test_extracts_transcript_blocks_and_ignores_scripts(self):
        page = "<script><p>ignore me</p></script><h2>Transcript</h2><p>Host: Welcome to the conversation about durable businesses and markets.</p><blockquote>A useful exact quotation from the guest.</blockquote>"
        transcript = transcript_from_html(page)
        self.assertIn("Welcome to the conversation", transcript)
        self.assertNotIn("ignore me", transcript)

    def test_render_references_books_and_escapes_content(self):
        episodes = [{
            "podcast": "Founders",
            "title": "A <great> episode",
            "link": "https://example.com/episode",
            "summary": {
                "overview": "A long overview.",
                "topics": [{"title": "Markets", "summary": "The main idea.", "quotes": ["Exact words"]}],
                "books": [{"title": "The <Book>", "author": "An Author", "why_it_matters": "Context"}],
            },
        }]
        plain, markup = render(episodes)
        self.assertIn("The <Book>", plain)
        self.assertIn("https://books.google.com/books?q=", plain)
        self.assertIn("&lt;great&gt;", markup)
        self.assertIn("Books discussed", markup)

    def test_book_url_is_a_search_reference(self):
        self.assertIn("The+Memo", book_url("The Memo", "Howard Marks"))


if __name__ == "__main__":
    unittest.main()
