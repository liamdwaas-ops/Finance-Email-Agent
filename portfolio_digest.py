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
import time
import urllib.parse
import urllib.error
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
MAX_PER_HOLDING = 4
MAX_MARKET_STORIES = 6
MAX_CANDIDATES_PER_QUERY = 6


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
    Holding("NVIDIA (NVDA; VOO top-five holding)", 'NVIDIA OR "Nvidia Corporation"', ("nvidia",)),
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
    Holding("NUKZ ETF", 'NUKZ ETF OR "Range Nuclear Renaissance Index"', ("nukz", "range nuclear"), "nuclear-energy holdings and policy"),
    Holding("Cameco (CCJ; NUKZ top-five holding)", 'Cameco OR CCJ', ("cameco", "ccj")),
    Holding("GE Vernova (GEV; NUKZ top-five holding)", '"GE Vernova" OR GEV', ("ge vernova", "gev")),
    Holding("Rolls-Royce (NUKZ top-five holding)", '"Rolls-Royce" AND nuclear', ("rolls-royce", "rolls royce")),
    Holding("Endesa (NUKZ top-five holding)", 'Endesa AND nuclear', ("endesa",)),
    Holding("CEZ (NUKZ top-five holding)", '"CEZ AS" OR "CEZ Group" AND nuclear', ("cez as", "cez group")),
    Holding("Health Care Select Sector SPDR (XLV)", 'XLV ETF OR "Health Care Select Sector SPDR"', ("xlv", "health care select"), "sector and policy"),
    Holding("Eli Lilly (LLY; XLV top-five holding)", '"Eli Lilly" OR LLY', ("eli lilly", "lilly")),
    Holding("Johnson & Johnson (JNJ; XLV top-five holding)", '"Johnson & Johnson" OR JNJ', ("johnson & johnson", "johnson and johnson", "jnj")),
    Holding("AbbVie (ABBV; XLV top-five holding)", 'AbbVie OR ABBV', ("abbvie", "abbv")),
    Holding("UnitedHealth (UNH; XLV top-five holding)", 'UnitedHealth OR UNH', ("unitedhealth", "unh")),
    Holding("Merck (MRK; XLV top-five holding)", '"Merck & Co" OR Merck OR MRK', ("merck", "mrk")),
    Holding("Vanguard S&P 500 ETF (VOO)", 'VOO ETF OR "S&P 500"', ("voo", "s&p 500", "federal reserve"), "index-wide macro and major constituents"),
    Holding("Apple (AAPL; VOO top-five holding)", 'Apple OR AAPL', ("apple", "aapl")),
    Holding("Microsoft (MSFT; VOO top-five holding)", 'Microsoft OR MSFT', ("microsoft", "msft")),
    Holding("Amazon (AMZN; VOO top-five holding)", 'Amazon OR AMZN', ("amazon", "amzn")),
    Holding("Alphabet (GOOGL; VOO top-five holding)", 'Alphabet OR Google OR GOOGL', ("alphabet", "google", "googl")),
)

PRICE_ASSETS = (
    ("Bitcoin (BTC)", "BTC-USD", 0.05, False),
    ("Ethereum (ETH)", "ETH-USD", 0.05, False),
    ("Tether (USDT)", "USDT-USD", 0.003, True),
    ("USD Coin (USDC)", "USDC-USD", 0.003, True),
    ("Ethena USDe", "USDE-USD", 0.003, True),
    ("Vanguard S&P 500 ETF (VOO)", "VOO", 0.025, False),
    ("Range Nuclear Renaissance ETF (NUKZ)", "NUKZ", 0.04, False),
    ("Health Care Select Sector SPDR (XLV)", "XLV", 0.025, False),
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
    r"\bcookie(?:s| settings)?\b|\bprivacy(?: policy| dashboard)?\b|\bconsent\b|"
    r"\bsubscribe\b|\bsign[ -]?in\b|\blog[ -]?in\b|\bregister\b|"
    r"\badvertisement\b|\bsponsored\b|\bterms (?:of use|and conditions)\b|"
    r"\bdisclaimer\b|\ball rights reserved\b|\baccept (?:all )?cookies\b|"
    r"\bmanage (?:your )?preferences\b|\bpersonal data\b|\blegitimate interest\b|"
    r"\byour privacy choices\b|\bdo not sell (?:or share)?\b|\bopt[ -]?out\b|"
    r"\bdata processing\b|\bdata (?:is|are) (?:used|shared|collected)\b|"
    r"\b(?:our|third[ -]?party) partners\b|\bpartner(?:s)? (?:use|store|process|share)\b|"
    r"\bvendor(?:s| list)?\b|\bconsent framework\b|\bmarketing purposes\b|"
    r"\badvertising purposes\b|\bwe and our (?:partners|vendors)\b",
    re.IGNORECASE,
)


class ArticleParser(HTMLParser):
    """Extracts meta description and visible paragraph text without executing page code."""

    def __init__(self) -> None:
        super().__init__()
        self.description = ""
        self.paragraphs: list[str] = []
        self.article_paragraphs: list[str] = []
        self._buffer: list[str] = []
        self._in_paragraph = False
        self._paragraph_in_article = False
        self._article_depth = 0
        self._ignored = 0
        self._ignored_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        element_text = " ".join(attributes.values())
        non_article_container = tag in {"script", "style", "noscript", "svg", "nav", "footer", "aside", "dialog", "form"}
        privacy_container = bool(NON_ARTICLE_TEXT.search(element_text))
        if non_article_container or privacy_container:
            self._ignored += 1
            self._ignored_tags.append(tag)
        if tag == "article" and not self._ignored:
            self._article_depth += 1
        if tag == "meta" and attributes.get("name", "").lower() == "description":
            self.description = self.description or attributes.get("content", "")
        if tag == "meta" and attributes.get("property", "").lower() in {"og:description", "twitter:description"}:
            self.description = self.description or attributes.get("content", "")
        if tag == "p" and not self._ignored:
            self._in_paragraph, self._buffer = True, []
            self._paragraph_in_article = self._article_depth > 0

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tags and tag == self._ignored_tags[-1]:
            self._ignored -= 1
            self._ignored_tags.pop()
        if tag == "p" and self._in_paragraph:
            paragraph = clean(" ".join(self._buffer))
            if len(paragraph) > 40 and not NON_ARTICLE_TEXT.search(paragraph):
                self.paragraphs.append(paragraph)
                if self._paragraph_in_article:
                    self.article_paragraphs.append(paragraph)
            self._in_paragraph = False
            self._paragraph_in_article = False
        if tag == "article" and self._article_depth:
            self._article_depth -= 1

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
    # Lead paragraphs carry the actual event, figures and implications. Yahoo pages
    # frequently put privacy/consent copy before their article body, so only semantic
    # <article> paragraphs are accepted from Yahoo; never its page metadata fallback.
    body_paragraphs = parser.article_paragraphs or parser.paragraphs
    summary = article_summary(" ".join(body_paragraphs[:6]))
    if not summary and story["source"].lower() not in {"yahoo finance", "yahoo finance australia"}:
        summary = article_summary(parser.description)
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


def notable_price_action() -> list[dict[str, str]]:
    """Return only material three-session moves; stablecoins are measured against their peg."""
    results: list[dict[str, str]] = []
    for name, ticker, threshold, is_stablecoin in PRICE_ASSETS:
        try:
            encoded = urllib.parse.quote(ticker, safe="")
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
            request = urllib.request.Request(url, headers={"User-Agent": "PortfolioNewsAgent/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read())
            closes = [value for value in payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] if value is not None]
            if len(closes) < 2:
                continue
            latest = closes[-1]
            reference = 1.0 if is_stablecoin else closes[max(0, len(closes) - 4)]
            move = (latest / reference - 1) if reference else 0
            if abs(move) < threshold:
                continue
            basis = "from its $1 peg" if is_stablecoin else "over roughly three sessions"
            results.append({
                "name": name,
                "summary": f"{name} is {move:+.1%} {basis} (latest observed price: ${latest:,.4f}).",
                "link": f"https://finance.yahoo.com/quote/{urllib.parse.quote(ticker, safe='')}",
            })
        except (KeyError, IndexError, TypeError, ValueError, urllib.error.URLError):
            continue
    return results


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


def collect(history: dict[str, str]) -> tuple[dict[Holding, list[dict[str, str]]], list[dict[str, str]], list[dict[str, str]]]:
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
    return results, market, notable_price_action()


def render(grouped: dict[Holding, list[dict[str, str]]], market: list[dict[str, str]], price_moves: list[dict[str, str]]) -> tuple[str, str]:
    now = datetime.now().astimezone()
    date = f"{now.day} {now.strftime('%B %Y')}"
    plain = [f"Portfolio news brief — {date}", ""]
    markup = [f"<h2>Portfolio news brief — {date}</h2>"]
    if not grouped and not market and not price_moves:
        plain.append("No material, non-duplicative developments were identified in the last 48 hours.")
        markup.append("<p>No material, non-duplicative developments were identified in the last 48 hours.</p>")
    if price_moves:
        plain.append("Notable price action")
        plain.extend(f"- {item['summary']}" for item in price_moves)
        plain.append("")
        items = "".join(f"<li><a href=\"{html.escape(item['link'], quote=True)}\">{html.escape(item['name'])}</a>: {html.escape(item['summary'].split(': ', 1)[-1])}</li>" for item in price_moves)
        markup.append(f"<h3>Notable price action</h3><ul>{items}</ul>")
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
    grouped, market, price_moves = collect(history)
    plain, markup = render(grouped, market, price_moves)
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
