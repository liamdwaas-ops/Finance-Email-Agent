#!/usr/bin/env python3
"""Email detailed summaries of new episodes from the investor's podcast list."""
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
from html.parser import HTMLParser
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "sent_podcast_episodes.json"
LOOKBACK_DAYS = 14
USER_AGENT = "PodcastDigestAgent/1.0"


@dataclass(frozen=True)
class Podcast:
    name: str
    website: str
    feed_url: str


PODCASTS = (
    Podcast("The All-In Podcast", "https://allin.com/episodes", "https://feeds.megaphone.fm/all-in"),
    Podcast("Acquired", "https://www.acquired.fm/", "https://feeds.transistor.fm/acquired"),
    Podcast("The Memo by Howard Marks", "https://www.oaktreecapital.com/insights", ""),
    Podcast("The Insight: Conversations by Oaktree", "https://www.oaktreecapital.com/insights", ""),
    Podcast("Founders", "https://www.founderspodcast.com/", "https://feeds.transistor.fm/founders-podcast"),
    Podcast("Cheeky Pint", "https://cheekypint.transistor.fm/episodes", "https://feeds.transistor.fm/cheekypint"),
    Podcast("Invest Like The Best", "https://colossus.com/series/invest-like-the-best/", "https://feeds.megaphone.fm/investlikethebest"),
)


class VisibleTextParser(HTMLParser):
    IGNORED = {"script", "style", "noscript", "svg", "nav", "footer", "form"}
    BLOCKS = {"p", "li", "h1", "h2", "h3", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._buffer: list[str] | None = None
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED:
            self._ignored += 1
        if tag in self.BLOCKS and not self._ignored:
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS and self._buffer is not None and not self._ignored:
            text = clean(" ".join(self._buffer))
            if len(text) >= 35:
                self.blocks.append(text)
            self._buffer = None
        if tag in self.IGNORED and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if self._buffer is not None and not self._ignored:
            self._buffer.append(data)


class FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "link" and values.get("rel", "").lower() in {"alternate", "feed"} and "xml" in values.get("type", "").lower():
            self.urls.append(values.get("href", ""))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch(url: str, limit: int = 2_000_000) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read(limit)


def discover_feed(podcast: Podcast) -> str:
    """Use an explicit override first, then discover RSS/Atom from the show page."""
    key = re.sub(r"[^A-Z0-9]+", "_", podcast.name.upper()).strip("_")
    override = os.environ.get(f"PODCAST_FEED_{key}")
    if override:
        return override
    if podcast.feed_url:
        return podcast.feed_url
    parser = FeedLinkParser()
    parser.feed(fetch(podcast.website).decode("utf-8", errors="ignore"))
    return urllib.parse.urljoin(podcast.website, parser.urls[0]) if parser.urls else ""


def parse_feed(podcast: Podcast) -> list[dict[str, str]]:
    root = ET.fromstring(fetch(podcast.feed_url))
    entries = root.findall("./channel/item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    episodes: list[dict[str, str]] = []
    content_tag = "{http://purl.org/rss/1.0/modules/content/}encoded"
    for entry in entries:
        title = clean(entry.findtext("title", ""))
        link = entry.findtext("link", "")
        if not link:
            link_element = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_element.get("href", "") if link_element is not None else ""
        published = entry.findtext("pubDate", "") or entry.findtext("{http://www.w3.org/2005/Atom}published", "")
        description = entry.findtext("description", "") or entry.findtext(content_tag, "") or entry.findtext("{http://www.w3.org/2005/Atom}summary", "")
        if title and link:
            episodes.append({"podcast": podcast.name, "title": title, "link": link.strip(), "published": published, "description": description})
    return episodes


def is_recent(episode: dict[str, str]) -> bool:
    try:
        published = datetime.fromisoformat(episode["published"].replace("Z", "+00:00"))
    except ValueError:
        try:
            published = datetime.strptime(episode["published"], "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            return False
    return published >= datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)


def episode_key(episode: dict[str, str]) -> str:
    return hashlib.sha256(episode["link"].split("?")[0].encode()).hexdigest()


def transcript_from_html(page: str) -> str:
    parser = VisibleTextParser()
    parser.feed(page)
    blocks = parser.blocks
    start = next((index for index, block in enumerate(blocks) if re.search(r"transcript|full episode", block, re.I)), None)
    if start is not None:
        blocks = blocks[start + 1:]
    return "\n".join(blocks)


def episode_transcript(episode: dict[str, str]) -> str:
    page = fetch(episode["link"]).decode("utf-8", errors="ignore")
    transcript = transcript_from_html(page)
    return (transcript if len(transcript) >= 500 else clean(episode.get("description", "")))[:120_000]


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def load_history() -> dict[str, str]:
    try:
        payload = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(history: dict[str, str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    cutoff = (datetime.now(UTC) - timedelta(days=180)).isoformat()
    retained = {key: value for key, value in history.items() if value >= cutoff}
    HISTORY_FILE.write_text(json.dumps(retained, indent=2), encoding="utf-8")


def local_summary(episode: dict[str, str], transcript: str) -> dict[str, object]:
    """Create a useful no-API summary while retaining exact transcript language."""
    blocks = [clean(block) for block in transcript.splitlines() if len(clean(block)) >= 45]
    if not blocks:
        blocks = [clean(transcript)]
    topics: list[dict[str, object]] = []
    for offset in range(0, len(blocks), 4):
        section = blocks[offset:offset + 4]
        sentences = [sentence.strip() for block in section for sentence in re.split(r"(?<=[.!?])\s+", block) if len(sentence.strip()) >= 45]
        if not sentences:
            continue
        title = " ".join(sentences[0].split()[:9]).rstrip(".,:;")
        topics.append({
            "title": title,
            "summary": " ".join(sentences[:6]),
            "quotes": sentences[:2],
        })
    books: list[dict[str, str]] = []
    book_pattern = re.compile(r"(?:book|read|reading|recommend(?:ed)?|author(?:ed)?)\s+(?:called\s+|titled\s+)?[\"“']?([A-Z][A-Za-z0-9&:' -]{2,80}?)[\"”']?\s+by\s+([A-Z][A-Za-z.' -]{2,60})", re.I)
    seen_books: set[str] = set()
    for title, author in book_pattern.findall(transcript):
        title, author = clean(title).strip(" ,.;:!?"), clean(author).strip(" ,.;:!?")
        key = f"{title.lower()}|{author.lower()}"
        if key not in seen_books:
            books.append({"title": title, "author": author, "why_it_matters": "Explicitly discussed in the episode."})
            seen_books.add(key)
    overview = " ".join(clean(block) for block in blocks[:4])
    return {"overview": overview[:1800], "topics": topics, "books": books}


def summarize(episode: dict[str, str], transcript: str) -> dict[str, object]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return local_summary(episode, transcript)
    response = OpenAI(api_key=api_key).responses.create(
        model=os.environ.get("PODCAST_OPENAI_MODEL", "gpt-5-mini"),
        input=[
            {"role": "system", "content": "You are a meticulous podcast research editor. Summarize only what the transcript supports."},
            {"role": "user", "content": f"""Create a long, useful summary of this podcast episode for an investor learning markets and business.
Return JSON exactly as {{\"overview\": string, \"topics\": [{{\"title\": string, \"summary\": string, \"quotes\": [string]}}], \"books\": [{{\"title\": string, \"author\": string, \"why_it_matters\": string}}]}}.
Break the episode into every substantial topic in discussion order. Use 1-3 exact, verbatim transcript quotations per topic, each under 35 words. Do not invent or silently polish quotes. Include books only: list every book explicitly discussed, recommended, or quoted, but exclude companies, papers, films, and podcasts. Do not include a book merely because it is mentioned in a passing analogy. Use an empty books array when no book is discussed.

Podcast: {episode['podcast']}
Episode: {episode['title']}
Transcript or episode notes:
{transcript}"""},
        ],
        text={"format": {"type": "json_object"}},
    )
    result = json.loads(response.output_text)
    if not isinstance(result, dict) or not isinstance(result.get("topics"), list) or not isinstance(result.get("books"), list):
        raise ValueError("OpenAI returned an invalid podcast summary shape")
    return result


def book_url(title: str, author: str) -> str:
    return "https://books.google.com/books?q=" + urllib.parse.quote_plus(f"{title} {author}".strip())


def render(episodes: list[dict[str, object]]) -> tuple[str, str]:
    date = datetime.now().strftime("%d %B %Y")
    plain = [f"Podcast episode brief - {date}", ""]
    markup = [f"<h2>Podcast episode brief - {html.escape(date)}</h2>"]
    for episode in episodes:
        summary = episode["summary"]
        plain.extend([f"{episode['podcast']}: {episode['title']}", str(summary.get("overview", "")), ""])
        markup.extend([f"<h3>{html.escape(str(episode['podcast']))}: {html.escape(str(episode['title']))}</h3>", f"<p>{html.escape(str(summary.get('overview', '')))}</p>"])
        for topic in summary.get("topics", []):
            plain.extend([f"{topic['title']}:", topic["summary"]])
            markup.append(f"<h4>{html.escape(topic['title'])}</h4><p>{html.escape(topic['summary'])}</p>")
            for quote in topic.get("quotes", []):
                plain.append(f'  Quote: "{quote}"')
                markup.append(f"<blockquote>{html.escape(quote)}</blockquote>")
        books = summary.get("books", [])
        if books:
            plain.append("Books discussed:")
            markup.append("<h4>Books discussed</h4><ul>")
            for book in books:
                url = book_url(str(book["title"]), str(book.get("author", "")))
                plain.append(f"- {book['title']} by {book.get('author', 'unknown')}: {book.get('why_it_matters', '')} ({url})")
                markup.append(f'<li><a href="{html.escape(url, quote=True)}">{html.escape(str(book["title"]))}</a> by {html.escape(str(book.get("author", "unknown")))}: {html.escape(str(book.get("why_it_matters", "")))}</li>')
            markup.append("</ul>")
        plain.extend([f"Episode: {episode['link']}", ""])
        markup.append(f'<p><a href="{html.escape(str(episode["link"]), quote=True)}">Open episode</a></p>')
    if not episodes:
        plain.append("No new episodes with usable transcripts were found.")
        markup.append("<p>No new episodes with usable transcripts were found.</p>")
    plain.append("Informational research only. Verify quotations and book details against the original episode.")
    markup.append("<p><small>Informational research only. Verify quotations and book details against the original episode.</small></p>")
    return "\n".join(plain), "\n".join(markup)


def send_email(subject: str, plain: str, markup: str) -> None:
    user = os.environ.get("PODCAST_GMAIL_USER")
    password = os.environ.get("PODCAST_GMAIL_APP_PASSWORD")
    recipient = os.environ.get("PODCAST_RECIPIENT")
    if not user or not password or not recipient:
        raise RuntimeError("Set PODCAST_GMAIL_USER, PODCAST_GMAIL_APP_PASSWORD, and PODCAST_RECIPIENT.")
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, user, recipient
    message.set_content(plain)
    message.add_alternative(markup, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)


def collect(history: dict[str, str]) -> list[dict[str, object]]:
    episodes: list[dict[str, object]] = []
    for podcast in PODCASTS:
        feed_url = discover_feed(podcast)
        if not feed_url:
            print(f"Warning: no RSS/Atom feed found for {podcast.name}; set its PODCAST_FEED_* override.", file=sys.stderr)
            continue
        try:
            feed_podcast = Podcast(podcast.name, podcast.website, feed_url)
            candidates = [episode for episode in parse_feed(feed_podcast) if is_recent(episode) and episode_key(episode) not in history]
            for episode in candidates:
                transcript = episode_transcript(episode)
                if len(transcript) < 500:
                    print(f"Warning: no usable transcript for {episode['title']}.", file=sys.stderr)
                    continue
                episodes.append({**episode, "summary": summarize(episode, transcript)})
        except Exception as error:
            print(f"Warning: could not process {podcast.name}: {error}", file=sys.stderr)
    return episodes


def deliver_episodes(episodes: list[dict[str, object]], history: dict[str, str], dry_run: bool = False) -> int:
    """Send one message per episode and persist each successful delivery immediately."""
    if not episodes:
        print("No new podcast episodes; no email sent.")
        return 0
    sent = 0
    for episode in episodes:
        plain, markup = render([episode])
        subject = f"{episode['podcast']}: {episode['title']}"
        if dry_run:
            print(plain)
            print("\n" + ("-" * 72) + "\n")
            continue
        send_email(subject, plain, markup)
        history[episode_key(episode)] = datetime.now(UTC).isoformat()
        save_history(history)
        sent += 1
    return sent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print the brief without sending or updating history.")
    args = parser.parse_args()
    load_dotenv()
    history = load_history()
    episodes = collect(history)
    if args.dry_run:
        deliver_episodes(episodes, history, dry_run=True)
        return 0
    sent = deliver_episodes(episodes, history)
    print(f"Sent {sent} episode summaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
