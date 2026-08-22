#!/usr/bin/env python3
"""Daily relevance-filtered news email for a defined investment portfolio."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from googlenewsdecoder import gnewsdecoder

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "sent_stories.json"
LOOKBACK_DAYS = 2
MAX_PER_HOLDING = 3
MAX_MARKET_STORIES = 5
MAX_CANDIDATES_PER_QUERY = 4


@dataclass(frozen=True)
class Holding:
    name: str
    query: str
    aliases: tuple[str, ...]
    detail: str = ""


HOLDINGS = (
    Holding("SharkNinja (SN)", 'SharkNinja OR "Shark Ninja"', ("sharkninja", "shark ninja")),
    Holding("Costco (COST)", "Costco", ("costco",)),
    Holding("Meta (META)", 'Meta OR "Meta Platforms"', ("meta", "facebook", "instagram", "whatsapp")),
    Holding("Cloudflare (NET)", "Cloudflare", ("cloudflare",)),
    Holding("Applied Digital (APLD)", '"Applied Digital" OR APLD', ("applied digital", "apld")),
    Holding("Seagate Technology (STX)", '"Seagate Technology" OR Seagate', ("seagate",)),
    Holding("SanDisk (SNDK)", 'SanDisk OR "Sandisk Corporation"', ("sandisk",)),
    Holding("Arm Holdings (ARM)", '"Arm Holdings" OR "Arm Ltd"', ("arm holdings", "arm ltd", "arm chip")),
    Holding("NVIDIA (NVDA)", 'NVIDIA OR "Nvidia Corporation"', ("nvidia",)),
    Holding("Hyperliquid (HYPE)", 'Hyperliquid OR "HYPE token"', ("hyperliquid", "hype token")),
    Holding("Aave (AAVE)", 'Aave OR "AAVE protocol"', ("aave", "aave protocol")),
    Holding("HyperLend", 'HyperLend OR "Hyper Lend"', ("hyperlend", "hyper lend")),
    Holding("Bitcoin (BTC)", 'Bitcoin OR BTC', ("bitcoin", "btc")),
    Holding("Ethereum (ETH)", 'Ethereum OR ETH', ("ethereum", "eth")),
    Holding("Kinetiq (KNTQ)", 'Kinetiq OR KNTQ', ("kinetiq", "kntq")),
    Holding("Tether (USDT)", 'Tether OR USDT', ("tether", "usdt")),
    Holding("USD Coin (USDC)", '"USD Coin" OR USDC OR Circle', ("usd coin", "usdc", "circle")),
    Holding("Ethena USDe", '"Ethena USDe" OR USDe', ("ethena usde", "usde")),
    Holding("Ethena (ENA)", 'Ethena OR "ENA token"', ("ethena", "ena token")),
    Holding("Rabby Wallet", '"Rabby Wallet" OR Rabby', ("rabby wallet", "rabby")),
    Holding("NUKZ ETF", 'NUKZ ETF OR "Range Nuclear Renaissance Index"', ("nukz", "nuclear"), "nuclear-energy holdings and policy"),
    Holding("Health Care Select Sector SPDR (XLV)", 'XLV ETF OR "Health Care Select Sector SPDR"', ("xlv", "health care select", "eli lilly", "unitedhealth", "johnson & johnson", "abbvie", "merck"), "major constituents, pharma, health policy"),
    Holding("Vanguard S&P 500 ETF (VOO)", 'VOO ETF OR "S&P 500"', ("voo", "s&p 500", "federal reserve"), "index-wide macro and major constituents"),
)

CATALYST = re.compile(
    r"earnings|revenue|guidance|forecast|outlook|profit|loss|dividend|buyback|"
    r"analyst|upgrade|downgrade|price target|product|launch|innovation|chip|ai |"
    r"data cent(?:er|re)|cloud|security|regulat|antitrust|tariff|sanction|policy|"
    r"federal reserve|interest rate|acquisition|partnership|contract|ceo|lawsuit|"
    r"investigation|approval|trial|drug|nuclear|energy|bitcoin|crypto|etf|treasury|"
    r"debt ceiling|government shutdown|tax|inflation|recession",
    re.IGNORECASE,
)
PRICE_ONLY = re.compile(r"^(?:why |how |)(?:[\w .'-]+ )?(?:stock|shares?) (?:is |are )?(?:up|down|soaring|falling|rising|dropping)", re.IGNORECASE)
ROUTINE_MOVE = re.compile(r"stock(?:s)? (?:trade|fall|rise|surge|drop)|shares? (?:trade|fall|rise|surge|drop)|price: posts", re.IGNORECASE)
SPECULATION = re.compile(
    r"which (?:stock|share|company).*(?:better|best)|better (?:buy|investment)|"
    r"should you buy|stocks? to buy|is .* a buy|price prediction|stock forecast|"
    r"could .* (?:soar|explode|double)|why .* stock|worth buying|versus|\bvs\.?",
    re.IGNORECASE,
)
REPUTABLE_SOURCES = {
    "abc news", "associated press", "blockworks", "cnbc", "coindesk", "decrypt",
    "dl news", "reuters", "the block", "the motley fool", "the motley fool australia",
    "unchained crypto", "yahoo finance", "yahoo finance australia",
}
MARKET_QUERIES = (
    '"Scott Bessent" OR Treasury OR "Federal Reserve"',
    '"S&P 500" OR "US stock market" OR Wall Street',
    'Nvidia OR Microsoft OR Apple OR Amazon OR Alphabet OR Tesla',
)
STOP_WORDS = frozenset("a an and are as at be by for from how in is it its of on or that the this to was what when where which with why".split())
NON_ARTICLE_TEXT = re.compile(
    r"cookie|privacy|consent|subscribe|sign in|log in|register|advertisement|"
    r"sponsored|terms (?:of use|and conditions)|disclaimer|all rights reserved|"
    r"accept (?:all )?cookies|manage (?:your )?preferences",
    re.IGNORECASE,
)


class ArticleParser(HTMLParser):
    """Extracts meta description and visible paragraph text without executing page code."""

    def __init__(self) -> None:
        super().__init__()
        self.description = ""
        self.paragraphs: list[str] = []
        self._buffer: list[str] = []
        self._in_paragraph = False
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        if tag == "meta" and attributes.get("name", "").lower() == "description":
            self.description = self.description or attributes.get("content", "")
        if tag == "meta" and attributes.get("property", "").lower() in {"og:description", "twitter:description"}:
            self.description = self.description or attributes.get("content", "")
        if tag == "p" and not self._ignored:
            self._in_paragraph, self._buffer = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        if tag == "p" and self._in_paragraph:
            paragraph = clean(" ".join(self._buffer))
            if len(paragraph) > 40 and not NON_ARTICLE_TEXT.search(paragraph):
                self.paragraphs.append(paragraph)
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph and not self._ignored:
            self._buffer.append(data)


def load_dotenv() -> None:
    paths = (ROOT / ".env", ROOT / ".env - Copy.example")
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def fetch_rss(query: str) -> list[dict[str, str]]:
    encoded = urllib.parse.quote_plus(f"({query}) when:3d")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-AU&gl=AU&ceid=AU:en"
    request = urllib.request.Request(url, headers={"User-Agent": "PortfolioNewsAgent/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        root = ET.fromstring(response.read())
    stories = []
    for item in root.findall("./channel/item"):
        title = clean(item.findtext("title", ""))
        link = item.findtext("link", "").strip()
        published = item.findtext("pubDate", "")
        source = clean(item.findtext("source", ""))
        if title and link:
            stories.append({"title": title, "link": link, "published": published, "source": source})
    return stories


def is_recent(story: dict[str, str]) -> bool:
    """Enforce the three-day limit independently of the search provider's query filter."""
    try:
        published = parsedate_to_datetime(story["published"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        return published >= datetime.now(UTC) - timedelta(days=3)
    except (TypeError, ValueError, IndexError):
        return False


def fingerprint(story: dict[str, str]) -> str:
    normalized = re.sub(r"[^a-z0-9 ]", "", story["title"].lower())
    return hashlib.sha256((normalized + "|" + story["link"].split("?")[0]).encode()).hexdigest()


def headline_key(story: dict[str, str]) -> str:
    normalized = re.sub(r"[^a-z0-9 ]", "", story["title"].lower())
    return "headline:" + hashlib.sha256(normalized.encode()).hexdigest()


def article_summary(text: str) -> str:
    text = clean(text).split("The post appeared first", 1)[0].strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chosen: list[str] = []
    for sentence in sentences:
        if len(sentence) < 45 or NON_ARTICLE_TEXT.search(sentence):
            continue
        chosen.append(sentence)
        if len(chosen) == 2 or len(" ".join(chosen)) >= 420:
            break
    return " ".join(chosen)[:520].rstrip()


def enrich(story: dict[str, str]) -> dict[str, str] | None:
    """Follow the news link and derive a factual brief from the article's visible content."""
    try:
        decoded = gnewsdecoder(story["link"], interval=1)
        resolved_link = decoded.get("decoded_url") if decoded.get("status") else None
        if not resolved_link:
            return None
        request = urllib.request.Request(resolved_link, headers={"User-Agent": "Mozilla/5.0 (PortfolioNewsAgent/1.0)"})
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read(1_500_000).decode("utf-8", errors="ignore")
            resolved_link = response.geturl()
    except Exception as error:
        print(f"Warning: could not open article '{story['title']}': {error}", file=sys.stderr)
        return None
    parser = ArticleParser()
    parser.feed(page)
    # Lead paragraphs carry the actual event, figures and implications; page metadata is
    # retained only as a fallback for sources that do not expose article text.
    summary = article_summary(" ".join(parser.paragraphs[:6])) or article_summary(parser.description)
    if not summary:
        return None
    return {**story, "link": resolved_link, "summary": summary}


def significant_tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{3,}", text.lower()) if word not in STOP_WORDS}


def is_duplicate(story: dict[str, str], selected: list[dict[str, str]]) -> bool:
    candidate = significant_tokens(story["title"] + " " + story.get("summary", ""))
    for prior in selected:
        prior_tokens = significant_tokens(prior["title"] + " " + prior.get("summary", ""))
        overlap = len(candidate & prior_tokens) / max(1, min(len(candidate), len(prior_tokens)))
        if overlap >= 0.72:
            return True
    return False


def story_markup(story: dict[str, str]) -> str:
    """Put the source URL on the first three summary words to keep the brief uncluttered."""
    words = story["summary"].split()
    linked = " ".join(words[:3])
    remainder = " ".join(words[3:])
    link = html.escape(story["link"], quote=True)
    return f'<li><a href="{link}">{html.escape(linked)}</a>{(" " + html.escape(remainder)) if remainder else ""} <em>({html.escape(story["source"])})</em></li>'


def story_plain(story: dict[str, str]) -> str:
    """Plain-text fallback; HTML-capable mail clients receive the embedded hyperlink."""
    return f"- {story['summary']} ({story['source']})"


def load_history() -> dict[str, str]:
    if not HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_history(history: dict[str, str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    retained = {key: value for key, value in history.items() if value >= cutoff}
    HISTORY_FILE.write_text(json.dumps(retained, indent=2), encoding="utf-8")


def relevant(holding: Holding, story: dict[str, str]) -> bool:
    title = story["title"]
    title_lower = title.lower()
    mentions_holding = any(
        alias in title_lower if " " in alias else bool(re.search(rf"\b{re.escape(alias)}\b", title_lower))
        for alias in holding.aliases
    )
    reputable_source = story["source"].lower() in REPUTABLE_SOURCES
    return is_recent(story) and reputable_source and mentions_holding and bool(CATALYST.search(title)) and not bool(PRICE_ONLY.search(title)) and not bool(ROUTINE_MOVE.search(title)) and not bool(SPECULATION.search(title))


def market_relevant(story: dict[str, str]) -> bool:
    title = story["title"]
    return is_recent(story) and story["source"].lower() in REPUTABLE_SOURCES and bool(CATALYST.search(title)) and not bool(PRICE_ONLY.search(title)) and not bool(ROUTINE_MOVE.search(title)) and not bool(SPECULATION.search(title))


def collect(history: dict[str, str]) -> tuple[dict[Holding, list[dict[str, str]]], list[dict[str, str]]]:
    results: dict[Holding, list[dict[str, str]]] = {}
    selected_all: list[dict[str, str]] = []
    for holding in HOLDINGS:
        selected = []
        try:
            candidates = fetch_rss(holding.query)
        except Exception as error:  # one unavailable source should not prevent delivery
            print(f"Warning: could not retrieve {holding.name}: {error}", file=sys.stderr)
            continue
        for story in candidates[:MAX_CANDIDATES_PER_QUERY]:
            key = fingerprint(story)
            if key in history or headline_key(story) in history or not relevant(holding, story):
                continue
            detailed = enrich(story)
            if not detailed or SPECULATION.search(detailed["title"] + " " + detailed["summary"]) or is_duplicate(detailed, selected_all):
                continue
            selected.append(detailed)
            selected_all.append(detailed)
            if len(selected) == MAX_PER_HOLDING:
                break
        if selected:
            results[holding] = selected
    market: list[dict[str, str]] = []
    for query in MARKET_QUERIES:
        try:
            candidates = fetch_rss(query)
        except Exception as error:
            print(f"Warning: could not retrieve market news: {error}", file=sys.stderr)
            continue
        for story in candidates[:MAX_CANDIDATES_PER_QUERY]:
            if fingerprint(story) in history or headline_key(story) in history or not market_relevant(story):
                continue
            detailed = enrich(story)
            if not detailed or SPECULATION.search(detailed["title"] + " " + detailed["summary"]) or is_duplicate(detailed, selected_all + market):
                continue
            market.append(detailed)
            if len(market) == MAX_MARKET_STORIES:
                break
        if len(market) == MAX_MARKET_STORIES:
            break
    return results, market


def render(grouped: dict[Holding, list[dict[str, str]]], market: list[dict[str, str]]) -> tuple[str, str]:
    now = datetime.now().astimezone()
    date = f"{now.day} {now.strftime('%B %Y')}"
    plain = [f"Portfolio news brief — {date}", ""]
    markup = [f"<h2>Portfolio news brief — {date}</h2>"]
    if not grouped and not market:
        plain.append("No material, non-duplicative developments were identified in the last 48 hours.")
        markup.append("<p>No material, non-duplicative developments were identified in the last 48 hours.</p>")
    if market:
        plain.append("Market and major-name developments")
        plain.extend(story_plain(s) for s in market)
        plain.append("")
        items = "".join(story_markup(s) for s in market)
        markup.append(f"<h3>Market and major-name developments</h3><ul>{items}</ul>")
    for holding, stories in grouped.items():
        plain.append(holding.name)
        plain.extend(story_plain(s) for s in stories)
        plain.append("")
        items = "".join(story_markup(s) for s in stories)
        markup.append(f"<h3>{html.escape(holding.name)}</h3><ul>{items}</ul>")
    footer = "Informational only — verify primary sources before making investment decisions."
    plain.extend([footer, "Routine price moves and stock-picking speculation are intentionally excluded."])
    markup.append(f"<hr><p><small>{footer}<br>Routine price moves and stock-picking speculation are intentionally excluded.</small></p>")
    return "\n".join(plain), "\n".join(markup)


def send_email(subject: str, plain: str, markup: str) -> None:
    user = os.environ.get("PORTFOLIO_GMAIL_USER")
    password = os.environ.get("PORTFOLIO_GMAIL_APP_PASSWORD")
    recipient = os.environ.get("PORTFOLIO_RECIPIENT", "liamdewaas@gmail.com")
    if not user or not password:
        raise RuntimeError("Set PORTFOLIO_GMAIL_USER and PORTFOLIO_GMAIL_APP_PASSWORD in .env or user environment variables.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = recipient
    message.set_content(plain)
    message.add_alternative(markup, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print the email without sending or updating history.")
    args = parser.parse_args()
    load_dotenv()
    history = load_history()
    grouped, market = collect(history)
    plain, markup = render(grouped, market)
    if args.dry_run:
        print(plain)
        return 0
    send_email("Daily Portfolio News Brief", plain, markup)
    timestamp = datetime.now(UTC).isoformat()
    for stories in grouped.values():
        for story in stories:
            history[fingerprint(story)] = timestamp
            history[headline_key(story)] = timestamp
    for story in market:
        history[fingerprint(story)] = timestamp
        history[headline_key(story)] = timestamp
    save_history(history)
    print(f"Sent {sum(map(len, grouped.values()))} story/stories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
