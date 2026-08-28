import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portfolio_digest import (
    ArticleParser, Holding, TALK_SHOW_PROMOTION, article_summary, excluded_from_digest,
    holding_story_limit, load_pending_digest, save_pending_digest,
)


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

    def test_strips_timestamps_and_motley_fool_membership_copy(self):
        text = (
            "Thursday, 10:30 AM AEST. Join the Motley Fool membership for more free articles. "
            "The company announced a binding agreement to build new data-centre capacity next year."
        )
        summary = article_summary(text, "The Motley Fool")
        self.assertNotIn("thursday", summary.lower())
        self.assertNotIn("membership", summary.lower())
        self.assertIn("binding agreement", summary)

    def test_the_block_survey_text_is_rejected(self):
        text = (
            "A survey of respondents found that investors expect a market recovery this year. "
            "The protocol completed a new financing agreement that expands its available liquidity."
        )
        summary = article_summary(text, "The Block")
        self.assertNotIn("survey", summary.lower())
        self.assertIn("financing agreement", summary)

    def test_rejects_tron_and_minor_bitcoin_development(self):
        bitcoin = Holding("Bitcoin (BTC)", "Bitcoin", ("bitcoin", "btc"))
        self.assertTrue(excluded_from_digest("Tron and TRX added a new integration."))
        self.assertTrue(excluded_from_digest("Bitcoin developers released a new testnet client.", bitcoin))
        self.assertFalse(excluded_from_digest("Bitcoin hard fork upgrade date was announced.", bitcoin))

    def test_identifies_talk_show_guest_promotions(self):
        self.assertTrue(TALK_SHOW_PROMOTION.search("Chief executive to appear as a guest on a talk show."))
        self.assertTrue(TALK_SHOW_PROMOTION.search("Podcast guest appearance announced for the founder."))

    def test_prepared_digest_round_trip_and_invalid_file(self):
        with TemporaryDirectory() as directory:
            pending = Path(directory) / "prepared_digest.json"
            with patch("portfolio_digest.PENDING_DIGEST_FILE", pending):
                save_pending_digest({"date": "2026-08-29", "plain": "prepared"})
                self.assertEqual(load_pending_digest(), {"date": "2026-08-29", "plain": "prepared"})
                pending.write_text("{invalid", encoding="utf-8")
                self.assertIsNone(load_pending_digest())

    def test_bitcoin_and_ethereum_each_have_a_two_story_limit(self):
        self.assertEqual(holding_story_limit(Holding("Bitcoin (BTC)", "Bitcoin", ("bitcoin",))), 2)
        self.assertEqual(holding_story_limit(Holding("Ethereum (ETH)", "Ethereum", ("ethereum",))), 2)
        self.assertIsNone(holding_story_limit(Holding("Costco (COST)", "Costco", ("costco",))))


if __name__ == "__main__":
    unittest.main()
