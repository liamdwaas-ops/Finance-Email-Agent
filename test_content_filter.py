import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portfolio_digest import (
    ArticleParser, COMPETITOR_BASELINES, COMPETITOR_WATCHLIST, HOLDINGS, Holding, LinkParser,
    MAX_COMPETITOR_STORIES, MAX_MARKET_STORIES, MAX_STORIES,
    TALK_SHOW_PROMOTION, article_summary, excluded_from_digest, holding_story_limit, is_recent,
    load_pending_digest, prioritised_competitor_groups, prioritised_holding_groups, render,
    price_impact_score, save_pending_digest,
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

    def test_decrypt_signup_copy_is_rejected(self):
        text = (
            "Sign up for Decrypt's Daily Digest to get the latest crypto stories in your inbox. "
            "The protocol completed a financing agreement that expands available liquidity for users."
        )
        summary = article_summary(text, "Decrypt")
        self.assertNotIn("sign up", summary.lower())
        self.assertIn("financing agreement", summary)

    def test_rejects_tron_and_minor_bitcoin_development(self):
        bitcoin = Holding("Bitcoin (BTC)", "Bitcoin", ("bitcoin", "btc"))
        self.assertTrue(excluded_from_digest("Tron and TRX added a new integration."))
        self.assertTrue(excluded_from_digest("Bitcoin developers released a new testnet client.", bitcoin))
        self.assertFalse(excluded_from_digest("Bitcoin hard fork upgrade date was announced.", bitcoin))

    def test_rejects_named_show_and_investment_hypothetical_stories(self):
        for phrase in ("The Exchange", "Squawk Box", "Money Talk", "Mad Money", "Closing Bell", "If you invested"):
            with self.subTest(phrase=phrase):
                self.assertTrue(excluded_from_digest(f"Portfolio update from {phrase} today."))

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
        self.assertEqual(holding_story_limit(Holding("Health Care Select Sector SPDR (XLV)", "XLV", ("xlv",))), 2)
        self.assertEqual(holding_story_limit(Holding("Vanguard S&P 500 ETF (VOO)", "VOO", ("voo",))), 2)
        self.assertEqual(holding_story_limit(Holding("Peer watch", "Peer", ("peer",), competitor_for="Costco (COST)")), 3)
        self.assertIsNone(holding_story_limit(Holding("Costco (COST)", "Costco", ("costco",))))

    def test_core_holdings_are_sourced_before_the_broader_portfolio(self):
        priority, remaining = prioritised_holding_groups()
        self.assertEqual(len(priority), 10)
        self.assertEqual(priority[0].name, "SharkNinja (SN)")
        self.assertEqual(priority[-1].name, "Hyperliquid (HYPE)")
        self.assertNotIn("Bitcoin (BTC)", {holding.name for holding in priority})
        self.assertIn("Bitcoin (BTC)", {holding.name for holding in remaining})

    def test_equity_holdings_have_four_listed_competitor_baselines(self):
        portfolio_names = {holding.name for holding in HOLDINGS}
        self.assertEqual(len(COMPETITOR_WATCHLIST), 23)
        self.assertEqual(set(COMPETITOR_BASELINES), {holding.competitor_for for holding in COMPETITOR_WATCHLIST})
        self.assertTrue(set(COMPETITOR_BASELINES).issubset(portfolio_names))
        self.assertTrue(all(len(peers) == 4 for peers in COMPETITOR_BASELINES.values()))
        self.assertTrue(all(holding.aliases and holding.detail for holding in COMPETITOR_WATCHLIST))
        priority, remaining = prioritised_competitor_groups()
        self.assertEqual(len(priority), 9)
        self.assertEqual(len(priority) + len(remaining), len(COMPETITOR_WATCHLIST))

    def test_six_story_slots_are_reserved_for_competitor_context(self):
        self.assertEqual(MAX_COMPETITOR_STORIES, 6)

    def test_story_caps_and_price_impact_priority(self):
        self.assertEqual(MAX_STORIES, 25)
        self.assertEqual(MAX_MARKET_STORIES, 4)
        earnings = {"title": "Company raises guidance after earnings beat", "summary": "Revenue and profit exceeded expectations."}
        product = {"title": "Company launches a new product", "summary": "The innovation expands its range."}
        self.assertGreater(price_impact_score(earnings), price_impact_score(product))

    def test_competitor_stories_are_labelled_with_the_related_holding_and_baseline(self):
        competitor_watch = COMPETITOR_WATCHLIST[0]
        story = {
            "summary": "Whirlpool reported a material product launch that expands its appliance range.",
            "link": "https://example.com/whirlpool",
            "source": "Reuters",
        }
        plain, markup = render({competitor_watch: [story]}, [], [])
        self.assertIn("Competitor watch — SharkNinja (SN)", plain)
        self.assertIn("Baseline peers: Whirlpool (WHR)", plain)
        self.assertIn("Competitor watch — SharkNinja (SN)", markup)

    def test_first_party_links_and_metadata_dates_are_available_for_screening(self):
        links = LinkParser("https://news.example.com/index")
        links.feed('<a href="/release">Company announces a material product launch</a>')
        self.assertEqual(links.links, [("Company announces a material product launch", "https://news.example.com/release")])
        parser = ArticleParser()
        parser.feed(
            f'<meta property="article:published_time" content="{datetime.now(UTC).isoformat()}">'
        )
        self.assertTrue(is_recent({"published": parser.published}))


if __name__ == "__main__":
    unittest.main()
