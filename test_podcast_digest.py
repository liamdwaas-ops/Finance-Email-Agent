import unittest
from unittest.mock import patch

from podcast_digest import Podcast, book_url, deliver_episodes, episode_key, parse_feed, render, summarize, transcript_from_html


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

    def test_summary_works_without_openai_key(self):
        transcript = "\n".join([
            "The discussion covers durable businesses and how management allocates capital over long periods.",
            "The guest explains why a strong balance sheet creates options during market downturns.",
            "They also discuss the book The Outsiders by William Thorndike and its lessons for CEOs.",
            "The final topic is valuation discipline and the danger of confusing a good company with a good investment.",
        ])
        with patch.dict("os.environ", {}, clear=True):
            result = summarize({"podcast": "Test", "title": "Episode"}, transcript)
        self.assertGreaterEqual(len(result["topics"]), 1)
        self.assertTrue(result["topics"][0]["quotes"])
        self.assertEqual(result["books"][0]["title"], "The Outsiders")

    def test_delivery_sends_each_episode_separately(self):
        episodes = [
            {"podcast": "Founders", "title": "One", "link": "https://example.com/one", "summary": {"overview": "Summary one.", "topics": [], "books": []}},
            {"podcast": "Acquired", "title": "Two", "link": "https://example.com/two", "summary": {"overview": "Summary two.", "topics": [], "books": []}},
        ]
        history = {}
        with patch("podcast_digest.send_email") as send, patch("podcast_digest.save_history") as save:
            self.assertEqual(deliver_episodes(episodes, history), 2)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[0], "One")
        self.assertIn(episode_key(episodes[0]), history)
        self.assertEqual(save.call_count, 2)

    def test_delivery_does_not_email_when_there_are_no_episodes(self):
        with patch("podcast_digest.send_email") as send:
            self.assertEqual(deliver_episodes([], {}), 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
