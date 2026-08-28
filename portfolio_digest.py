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
from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

from googlenewsdecoder import gnewsdecoder

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "sent_stories.json"
PENDING_DIGEST_FILE = DATA_DIR / "prepared_digest.json"
AEST = timezone(timedelta(hours=10), name="AEST")
LOOKBACK_DAYS = 2


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
TALK_SHOW_PROMOTION = re.compile(
    r"(?:to appear|appearing|guest appearance|joins? .* as a guest|will join|"
    r"guest|appearance).*?(?:talk show|podcast|show|interview)|"
    r"(?:talk show|podcast|show|interview).*?(?:guest|appearance)",
    re.IGNORECASE,
)
TRON_RELATED = re.compile(r"\btron\b|\btrx\b|\bjustin sun\b", re.IGNORECASE)
CHAIN_DEVELOPMENT = re.compile(
    r"developer|development|devnet|testnet|client|roadmap|foundation|research|"
    r"eip[- ]?\d+|bip[- ]?\d+|core release|staking (?:update|change)",
    re.IGNORECASE,
)
MAJOR_CHAIN_UPGRADE = re.compile(
    r"(?:major |mainnet |network |protocol )?upgrade|hard fork|activation|"
    r"fork (?:scheduled|date)|upgrade (?:announced|scheduled|launch)",
    re.IGNORECASE,
)
LOW_INFORMATION_TEXT = re.compile(
    r"^(?:mon|tues|wednes|thurs|fri|satur|sun)day(?:,?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?(?:\s+[A-Z]{2,4})?)?[.,]?$|"
    r"^(?:published|updated|last updated)\s*[:\-]? *(?:mon|tues|wednes|thurs|fri|satur|sun)day.*$|"
    r"^\d{1,2}:\d{2}\s*(?:am|pm)(?:\s+[A-Z]{2,4})?[.,]?$",
    re.IGNORECASE,
)
THE_BLOCK_SURVEY = re.compile(r"\bsurvey\b|\bpoll\b|\brespondents?\b|\bquestionnaire\b", re.IGNORECASE)
LEADING_TIMESTAMP = re.compile(
    r"^(?:(?:published|updated|last updated)\s*[:\-]?\s*)?"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day(?:,?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?(?:\s+[A-Z]{2,4})?)?|"
    r"\d{1,2}:\d{2}\s*(?:am|pm)(?:\s+[A-Z]{2,4})?)"
    r"\s*(?:[|:—–-]\s*)?",
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
    r"\badvertising purposes\b|\bwe and our (?:partners|vendors)\b|"
    r"\bmotley fool (?:premium|membership|services|disclaimer|subscription)\b|"
    r"\bfree (?:article|membership|trial)\b|\bjoin (?:the )?motley fool\b|"
    r"\bmembership (?:sign[ -]?up|offer|benefit|service)\b|"
    r"\bmotley fool(?:'s)? disclosure policy\b",
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
            if len(paragraph) > 40 and not NON_ARTICLE_TEXT.search(paragraph) and not LOW_INFORMATION_TEXT.search(paragraph):
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


def article_summary(text: str, source: str = "") -> str:
    text = clean(text).split("The post appeared first", 1)[0].strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chosen: list[str] = []
    for sentence in sentences:
        sentence = LEADING_TIMESTAMP.sub("", sentence).strip()
        if len(sentence) < 45 or NON_ARTICLE_TEXT.search(sentence) or LOW_INFORMATION_TEXT.search(sentence):
            continue
        if source.lower() == "the block" and THE_BLOCK_SURVEY.search(sentence):
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
        with urllib.request.urlopen(request, timeout=12) as response:
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
    summary = article_summary(" ".join(body_paragraphs[:6]), story["source"])
    if not summary and story["source"].lower() not in {"yahoo finance", "yahoo finance australia"}:
        summary = article_summary(parser.description, story["source"])
    if not summary:
        return None
    return {**story, "link": resolved_link, "summary": summary}


def enrich_many(stories: list[dict[str, str]]) -> list[dict[str, str]]:
    """Open all independent source articles concurrently without limiting their count."""
    if not stories:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(stories))) as executor:
        futures = [executor.submit(enrich, story) for story in stories]
        return [detailed for future in futures if (detailed := future.result()) is not None]


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


def load_pending_digest() -> dict[str, object] | None:
    """Return a prepared same-day email, if a prior source run completed."""
    if not PENDING_DIGEST_FILE.exists():
        return None
    try:
        payload = json.loads(PENDING_DIGEST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def save_pending_digest(payload: dict[str, object]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PENDING_DIGEST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def prepared_digest(history: dict[str, str]) -> dict[str, object]:
    """Source and render a digest now, leaving delivery for the 6am job."""
    pending = load_pending_digest()
    if args.prepare:
        if pending and pending.get("date") == datetime.now(AEST).date().isoformat():
            print("Today's AEST digest has already been prepared; skipping duplicate source run.")
            return 0
        payload = prepared_digest(history)
        save_pending_digest(payload)
        print(f"Prepared {payload['story_count']} story/stories for today's AEST delivery.")
        return 0
    if args.send_prepared and pending and pending.get("date") == datetime.now(AEST).date().isoformat():
        payload = pending
        print("Using the prepared AEST digest.")
    else:
        payload = prepared_digest(history)
        if args.send_prepared:
            print("No current prepared digest was available; sourced a delivery fallback.")
    story_keys: list[str] = []
    for stories in grouped.values():
        for story in stories:
            story_keys.extend((fingerprint(story), headline_key(story)))
    for story in market:
        story_keys.extend((fingerprint(story), headline_key(story)))
    return {
        "date": datetime.now(AEST).date().isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "plain": plain,
        "markup": markup,
        "story_keys": story_keys,
        "story_count": sum(map(len, grouped.values())) + len(market),
    }


def record_delivery(history: dict[str, str], payload: dict[str, object]) -> None:
    timestamp = datetime.now(UTC).isoformat()
    history[f"delivery:{datetime.now(AEST).date().isoformat()}"] = timestamp
    for key in payload.get("story_keys", []):
        if isinstance(key, str):
            history[key] = timestamp
    save_history(history)


def excluded_from_digest(text: str, holding: Holding | None = None, source: str = "") -> bool:
    """Apply editorial exclusions that are independent of an article's relevance."""
    if TRON_RELATED.search(text):
        return True
    if source.lower() == "the block" and THE_BLOCK_SURVEY.search(text):
        return True
    is_btc_or_eth = (
        holding is not None and holding.name.startswith(("Bitcoin (BTC)", "Ethereum (ETH)"))
    ) or bool(re.search(r"\b(?:bitcoin|btc|ethereum|eth)\b", text, re.IGNORECASE))
    return is_btc_or_eth and bool(CHAIN_DEVELOPMENT.search(text)) and not bool(MAJOR_CHAIN_UPGRADE.search(text))


def relevant(holding: Holding, story: dict[str, str]) -> bool:
    title = story["title"]
    title_lower = title.lower()
    mentions_holding = any(
        alias in title_lower if " " in alias else bool(re.search(rf"\b{re.escape(alias)}\b", title_lower))
        for alias in holding.aliases
    )
    reputable_source = story["source"].lower() in REPUTABLE_SOURCES
    return (
        is_recent(story) and reputable_source and mentions_holding and bool(CATALYST.search(title))
        and not bool(PRICE_ONLY.search(title)) and not bool(ROUTINE_MOVE.search(title))
        and not bool(SPECULATION.search(title)) and not bool(TALK_SHOW_PROMOTION.search(title))
        and not excluded_from_digest(title, holding, story["source"])
    )


def market_relevant(story: dict[str, str]) -> bool:
    title = story["title"]
    return (
        is_recent(story) and story["source"].lower() in REPUTABLE_SOURCES and bool(CATALYST.search(title))
        and not bool(PRICE_ONLY.search(title)) and not bool(ROUTINE_MOVE.search(title))
        and not bool(SPECULATION.search(title)) and not bool(TALK_SHOW_PROMOTION.search(title))
        and not excluded_from_digest(title, source=story["source"])
    )


def collect_holding(holding: Holding, history: dict[str, str]) -> tuple[Holding, list[dict[str, str]]]:
    """Fetch one holding independently so all holdings can be processed in parallel."""
    try:
        candidates = fetch_rss(holding.query)
    except Exception as error:
        print(f"Warning: could not retrieve {holding.name}: {error}", file=sys.stderr)
        return holding, []
    shortlisted = [
        story for story in candidates
        if fingerprint(story) not in history and headline_key(story) not in history and relevant(holding, story)
    ]
    return holding, enrich_many(shortlisted)


def collect_market(query: str, history: dict[str, str]) -> list[dict[str, str]]:
    try:
        candidates = fetch_rss(query)
    except Exception as error:
        print(f"Warning: could not retrieve market news: {error}", file=sys.stderr)
        return []
    shortlisted = [
        story for story in candidates
        if fingerprint(story) not in history and headline_key(story) not in history and market_relevant(story)
    ]
    return enrich_many(shortlisted)


def collect(history: dict[str, str]) -> tuple[dict[Holding, list[dict[str, str]]], list[dict[str, str]], list[dict[str, str]]]:
    results: dict[Holding, list[dict[str, str]]] = {}
    selected_all: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(collect_holding, holding, history) for holding in HOLDINGS]
        holding_batches = [future.result() for future in futures]
    for holding, detailed_stories in holding_batches:
        selected = []
        for detailed in detailed_stories:
            content = detailed["title"] + " " + detailed["summary"] if detailed else ""
            if not detailed or SPECULATION.search(content) or excluded_from_digest(content, holding, detailed["source"]) or is_duplicate(detailed, selected_all):
                continue
            selected.append(detailed)
            selected_all.append(detailed)
        if selected:
            results[holding] = selected
    market: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(MARKET_QUERIES)) as executor:
        market_batches = [future.result() for future in [executor.submit(collect_market, query, history) for query in MARKET_QUERIES]]
    for detailed_stories in market_batches:
        for detailed in detailed_stories:
            content = detailed["title"] + " " + detailed["summary"] if detailed else ""
            if not detailed or SPECULATION.search(content) or excluded_from_digest(content, source=detailed["source"]) or is_duplicate(detailed, selected_all + market):
                continue
            market.append(detailed)
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
    parser.add_argument("--prepare", action="store_true", help="Source and render today's digest without sending it.")
    parser.add_argument("--send-prepared", action="store_true", help="Send today's prepared digest, or source one as a fallback.")
    args = parser.parse_args()
    if sum((args.dry_run, args.prepare, args.send_prepared)) > 1:
        parser.error("use only one of --dry-run, --prepare, or --send-prepared")
    load_dotenv()
    history = load_history()
    delivery_key = f"delivery:{datetime.now(AEST).date().isoformat()}"
    if not args.dry_run and delivery_key in history:
        print("Today’s AEST digest has already been delivered; skipping fallback run.")
        return 0
    grouped, market, price_moves = collect(history)
    plain, markup = render(grouped, market, price_moves)
    if args.dry_run:
        print(payload["plain"])
        return 0
    send_email("Daily Portfolio News Brief", str(payload["plain"]), str(payload["markup"]))
    record_delivery(history, payload)
    if PENDING_DIGEST_FILE.exists():
        PENDING_DIGEST_FILE.unlink()
    print(f"Sent {payload['story_count']} story/stories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
