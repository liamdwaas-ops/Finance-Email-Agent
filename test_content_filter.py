import unittest

from portfolio_digest import ArticleParser, article_summary


class ArticleContentFilterTests(unittest.TestCase):
    def test_rejects_privacy_and_partner_sentences(self):
        text = (
            "We and our partners use cookies and personal data for advertising purposes. "
            "The company reported revenue growth after expanding its data-centre contract. "
            "Management said the agreement adds capacity this year."
        )
        summary = article_summary(text)
        self.assertNotIn("partners", summary.lower())
        self.assertIn("reported revenue growth", summary)

    def test_ignores_popup_container_paragraphs(self):
        parser = ArticleParser()
        parser.feed(
            '<div class="cookie-consent"><p>Accept all cookies and manage your preferences.</p></div>'
            '<article><p>The protocol launched a governance upgrade with new collateral limits.</p>'
            '<p>The change takes effect next week across all supported markets.</p></article>'
        )
        self.assertEqual(len(parser.article_paragraphs), 2)
        self.assertNotIn("cookies", " ".join(parser.paragraphs).lower())


if __name__ == "__main__":
    unittest.main()
