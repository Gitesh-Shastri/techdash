#!/usr/bin/env python3
"""techdash -- one local dashboard for everything happening in tech.

Pulls ~120 feeds (Hacker News, the tech press, package registries, GitHub
releases for your stack, the MCP ecosystem, AI labs, papers, forums), caches
them on disk, and serves a single page at http://localhost:8787.

Standard library only. No pip install, no API keys, no accounts.

    python3 techdash.py              # serve, open browser
    python3 techdash.py --no-open    # serve without opening a browser
    python3 techdash.py --port 9000
    python3 techdash.py --once       # refresh the cache and exit (for cron)

Set GITHUB_TOKEN in the environment to raise the GitHub search rate limit.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import io
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree

import sources as srcmod
from sources import SOURCES, TICKERS, TRENDING_QUERIES

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
STATIC_DIR = os.path.join(HERE, "static")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 techdash/1.0"
)
# Yahoo Finance 429s browser-shaped User-Agents (it wants a cookie+crumb
# session for those) but serves a plain one happily. Quotes use this instead.
PLAIN_USER_AGENT = "Mozilla/5.0 techdash/1.0"
TIMEOUT = 25
PARALLEL_WORKERS = 10
POLITE_GAP_SECONDS = 25  # spacing between requests in the rate-limited lane
MAX_BACKOFF_MULTIPLIER = 8

# Release tags are a zoo: go1.27rc3, v2.14.0b1, @3.0.0-next.23, 1.2.3-canary.4.
# Anything matching here is hidden by the UI's "stable only" switch.
PRERELEASE = re.compile(
    r"canary|nightly|snapshot|preview|alpha|beta"
    r"|rc[.\-_]?\d|\d+rc\d"          # 1.2.3-rc1, go1.27rc3
    r"|\d+[ab]\d+"                    # 2.14.0b1, 1.0a2
    r"|[-_.]next[.\-]?\d"             # @3.0.0-next.23
    r"|[-.]dev\d",                    # 1.2.3.dev4
    re.I,
)
TAG_STRIP = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# HTTP boundary. Everything that touches the network goes through here so that
# retries, gzip, and failure modes live in exactly one place.
# ---------------------------------------------------------------------------

_SSL = ssl.create_default_context()


class FetchError(Exception):
    pass


def fetch(url: str, accept: str = "*/*", user_agent: str = USER_AGENT) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": accept,
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"{type(exc.reason).__name__}: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchError("timeout") from exc
    except Exception as exc:  # noqa: BLE001 - the network surprises us constantly
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


def fetch_json(url: str):
    return json.loads(fetch(url, accept="application/json"))


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def clean(text: str | None) -> str:
    """Feed text arrives as HTML, sometimes double-escaped. Flatten it."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    stripped = TAG_STRIP.sub(" ", unescaped)
    return WS.sub(" ", html.unescape(stripped)).strip()


SUMMARY_CHARS = 300
BOILERPLATE = re.compile(
    r"^(comments?|read more|continue reading|link|submitted by|\[link\]|\.\.\.)\W*$", re.I,
)
# arXiv prefixes every abstract with its own bookkeeping.
SUMMARY_PREFIX = re.compile(
    r"^(arXiv:\S+\s*)?(Announce Type:\s*\S+\s*)?(Abstract:\s*)?", re.I,
)
# Techmeme opens each blurb with "Reporter / Outlet : " before repeating the
# headline. The spaces around the colon are what make this safe to strip.
BYLINE = re.compile(r"^[@\w][\w .'&/@-]{1,60}\s:\s")


SMART_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "–": "-", "—": "-", " ": " "})


def fold(text: str) -> str:
    """Lowercase and flatten smart punctuation, so two renderings of the same
    sentence compare equal."""
    return text.strip().lower().translate(SMART_QUOTES)


def summarise_text(text: str | None, title: str) -> str:
    """Trim a feed blurb to a few lines, cut at a word boundary.

    Returns "" when the blurb adds nothing -- some feeds repeat the headline as
    the description, and a paragraph that just restates the title is noise.
    """
    body = SUMMARY_PREFIX.sub("", clean(text), count=1).strip()
    body = BYLINE.sub("", body, count=1).strip()
    if len(body) < 25 or BOILERPLATE.match(body):
        return ""

    # Techmeme et al. lead the blurb with the headline again. Cut whatever
    # prefix the two share -- comparing lengths would over-trim, because the
    # title often carries a "(Author/Outlet)" suffix the body doesn't.
    shared = 0
    for body_char, title_char in zip(fold(body), fold(title)):
        if body_char != title_char:
            break
        shared += 1
    if shared >= 40:
        body = body[shared:].lstrip(" -–—:·()")
    if len(body) < 25:
        return ""
    if len(body) <= SUMMARY_CHARS:
        return body
    cut = body[:SUMMARY_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space > SUMMARY_CHARS * 0.6 else cut).rstrip(" ,;:.-") + "…"


def to_epoch(value: str | None) -> float | None:
    """Parse the half-dozen date formats feeds actually use in the wild."""
    if not value:
        return None
    text = value.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            continue
    return None


def item(source, title, url, ts=None, meta="", tag=None, prerelease=False,
         summary="") -> dict | None:
    title = clean(title)
    if not title or not url:
        return None
    return {
        "title": title[:300],
        "url": url,
        "src": source.name,
        "sid": source.id,
        "sec": source.section,
        "tag": tag or source.tag,
        "ts": ts,
        "meta": meta,
        "pre": prerelease,
        "sum": summarise_text(summary, title),
    }


# ---------------------------------------------------------------------------
# Loaders. Every source resolves to one of these: (source, fetch) -> [item].
# ---------------------------------------------------------------------------

def _child_text(node, *names) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return found.text
    return ""


def _entry_link(node) -> str:
    """RSS puts the URL in <link> text; Atom puts it in a <link href>."""
    for link in node.findall("link"):
        href = link.get("href")
        rel = link.get("rel", "alternate")
        if href and rel == "alternate":
            return href
    for link in node.findall("link"):
        if link.get("href"):
            return link.get("href")
    text = _child_text(node, "link", "id", "guid")
    return text.strip() if text.startswith("http") else ""


def is_release_feed(source) -> bool:
    """Release feeds get project-prefixed titles and prerelease detection.

    Most live in the "releases" section, but the MCP SDK feeds sit under AI and
    still publish rc/alpha tags that "stable only" must be able to hide.
    """
    return source.section == "releases" or source.url.endswith("releases.atom")


def load_rss(source, get) -> list[dict]:
    raw = get(source.url)
    # Strip namespaces so RSS and Atom can share one code path.
    text = re.sub(rb"\sxmlns(:\w+)?=\"[^\"]*\"", b"", raw, count=40)
    text = re.sub(rb"<(/?)\w+:", rb"<\1", text)
    root = ElementTree.fromstring(text)

    nodes = root.findall(".//item") or root.findall(".//entry")
    results = []
    for node in nodes[: source.limit]:
        title = _child_text(node, "title")
        url = _entry_link(node)
        ts = to_epoch(_child_text(node, "pubDate", "published", "updated", "date", "created"))
        author = clean(_child_text(node, "creator", "author"))
        if not author:
            author_node = node.find("author")
            if author_node is not None:
                author = clean(_child_text(author_node, "name"))
        release = is_release_feed(source)
        pre = release and bool(PRERELEASE.search(title or ""))
        if release:
            # Most release feeds title entries with a bare tag ("v19.2.0"), which
            # is meaningless once cards are merged -- so prefix the project. Some
            # already say their own name; don't stutter at those.
            title = clean(title)
            if not title.lower().startswith(source.name.lower()):
                title = f"{source.name} {title}".strip()
            meta = ""
        else:
            meta = author[:40]
        # description/summary are the intended blurb; content/encoded is the
        # full body, which some feeds use instead. Take whichever exists.
        blurb = _child_text(node, "description", "summary", "content", "encoded")
        entry = item(source, title, url, ts, meta, prerelease=pre, summary=blurb)
        if entry:
            results.append(entry)
    return results


SINCE_PLACEHOLDER = re.compile(r"\{since:(\d+)d\}")


def expand_since(url: str) -> str:
    """Turn {since:7d} into a unix timestamp.

    Algolia's relevance-sorted searches happily return the all-time top hits,
    which is how you end up with a 6-year-old "Ask HN" on today's dashboard.
    Sources that sort by points must pin a window.
    """
    return SINCE_PLACEHOLDER.sub(
        lambda match: str(int(time.time() - int(match.group(1)) * 86400)), url,
    )


def load_hn(source, get) -> list[dict]:
    payload = json.loads(get(expand_since(source.url)))
    results, seen = [], set()
    for hit in payload.get("hits", []):
        if len(results) >= source.limit:
            break
        # The same story often appears under several objectIDs (resubmissions).
        headline = (hit.get("title") or hit.get("story_title") or "").strip().lower()
        if not headline or headline in seen:
            continue
        seen.add(headline)
        story_id = hit.get("objectID")
        title = hit.get("title") or hit.get("story_title")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        points = hit.get("points") or 0
        comments = hit.get("num_comments") or 0
        meta = f"{points} pts · {comments} comments"
        # HN carries no blurb, so the destination domain is the only extra
        # signal available -- and it's a good one (github vs arxiv vs blogspam).
        domain = urllib.parse.urlparse(url).netloc.removeprefix("www.")
        if domain and domain != "news.ycombinator.com":
            meta += f" · {domain}"
        entry = item(source, title, url, hit.get("created_at_i"), meta)
        if entry:
            entry["discuss"] = f"https://news.ycombinator.com/item?id={story_id}"
            results.append(entry)
    return results


TICKER_GAP_SECONDS = 0.6


def load_tickers(source, get) -> list[dict]:
    """Yahoo's chart endpoint is the one quote API still open without a key.

    It 429s on concurrent bursts, so these go out one at a time, over a plain
    User-Agent. If every symbol fails we raise, which makes the Store keep
    yesterday's prices rather than blanking the card.
    """
    results = []
    del get  # quotes need their own headers; see PLAIN_USER_AGENT
    for index, (symbol, label) in enumerate(TICKERS):
        if index:
            time.sleep(TICKER_GAP_SECONDS)
        quoted = urllib.parse.quote(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quoted}?range=5d&interval=1d"
        try:
            raw = fetch(url, accept="application/json", user_agent=PLAIN_USER_AGENT)
            meta = json.loads(raw)["chart"]["result"][0]["meta"]
        except Exception:  # noqa: BLE001 - one dead symbol must not kill the card
            continue
        price = meta.get("regularMarketPrice")
        previous = meta.get("previousClose") or meta.get("chartPreviousClose")
        if price is None or not previous:
            continue
        results.append({
            "title": label,
            "url": f"https://finance.yahoo.com/quote/{quoted}",
            "src": source.name, "sid": source.id, "sec": source.section,
            "tag": "market", "ts": meta.get("regularMarketTime"),
            "meta": f"{price:,.2f} {meta.get('currency', '')}".strip(),
            "pre": False,
            "quote": {
                "symbol": symbol,
                "price": price,
                "change": round((price - previous) / previous * 100, 2),
                # Tiles keep registry order; the global time sort would otherwise
                # shuffle them on every refresh.
                "ord": index,
            },
        })
    if not results:
        raise FetchError("every quote request failed (Yahoo rate limit?)")
    return results


def load_gh_trending(source, get) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    seen, results = set(), []
    for extra, tag in TRENDING_QUERIES:
        query = f"created:>{since} stars:>50 {extra}".strip()
        url = ("https://api.github.com/search/repositories?q="
               + urllib.parse.quote(query) + "&sort=stars&order=desc&per_page=8")
        try:
            payload = json.loads(get(url))
        except Exception:  # noqa: BLE001 - search is rate limited; partial is fine
            continue
        for repo in payload.get("items", []):
            name = repo.get("full_name")
            if not name or name in seen:
                continue
            seen.add(name)
            stars = repo.get("stargazers_count") or 0
            language = repo.get("language") or ""
            description = clean(repo.get("description")) or "no description"
            entry = item(
                source,
                name,
                repo.get("html_url"),
                to_epoch(repo.get("created_at")),
                f"★ {stars:,}" + (f" · {language}" if language else ""),
                tag=tag if tag != "any"
                else srcmod.LANGUAGE_TAGS.get(language.lower(), "github"),
                summary=description,
            )
            if entry:
                entry["stars"] = stars
                results.append(entry)
    results.sort(key=lambda row: row.get("stars", 0), reverse=True)
    return results[: source.limit]


def load_crates(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    results = []
    for crate in payload.get("new_crates", [])[: source.limit]:
        name = crate.get("name")
        description = clean(crate.get("description")) or "no description"
        entry = item(
            source,
            name,
            f"https://crates.io/crates/{name}",
            to_epoch(crate.get("created_at")),
            f"v{crate.get('newest_version') or crate.get('max_version') or '?'}",
            summary=description,
        )
        if entry:
            results.append(entry)
    return results


def load_npm(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    now = time.time()
    results, seen = [], set()
    for change in payload.get("results", []):
        name = change.get("id")
        if not name or name.startswith("_") or name in seen:
            continue
        seen.add(name)
        entry = item(source, name, f"https://www.npmjs.com/package/{name}", now, "just published")
        if entry:
            results.append(entry)
        if len(results) >= source.limit:
            break
    return results


def load_go_index(source, get) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = get(f"https://index.golang.org/index?since={since}&limit=200").decode("utf-8", "replace")
    results, seen = [], set()
    for line in reversed(raw.strip().splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = row.get("Path", "")
        if not path or path in seen or "/gen/go/" in path:  # skip buf codegen spam
            continue
        seen.add(path)
        entry = item(source, path, f"https://pkg.go.dev/{path}",
                     to_epoch(row.get("Timestamp")), row.get("Version", ""))
        if entry:
            results.append(entry)
        if len(results) >= source.limit:
            break
    return results


def load_rubygems(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    results = []
    for gem in payload[: source.limit]:
        name = gem.get("name")
        description = clean(gem.get("info")) or "no description"
        entry = item(source, name,
                     f"https://rubygems.org/gems/{name}",
                     to_epoch(gem.get("version_created_at")),
                     f"v{gem.get('version', '?')}",
                     summary=description)
        if entry:
            results.append(entry)
    return results


def load_jsdelivr(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    results = []
    for rank, pkg in enumerate(payload[: source.limit], start=1):
        name = pkg.get("name")
        hits = (pkg.get("hits") or 0)
        entry = item(source, f"{rank}. {name}", f"https://www.npmjs.com/package/{name}",
                     None, f"{hits / 1e9:.1f}B CDN hits/wk" if hits > 1e9 else f"{hits:,} hits/wk")
        if entry:
            results.append(entry)
    return results


def load_mcp_registry(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    results, seen = [], set()
    # The registry lists every published version of a server; we want the
    # newest of each, so walk newest-first and keep the first sighting.
    rows = sorted(
        payload.get("servers", []),
        key=lambda row: (row.get("_meta", {})
                            .get("io.modelcontextprotocol.registry/official", {})
                            .get("publishedAt") or ""),
        reverse=True,
    )
    for row in rows:
        server = row.get("server", {})
        name = server.get("title") or server.get("name")
        if not name or server.get("name") in seen:
            continue
        seen.add(server.get("name"))
        description = clean(server.get("description")) or "no description"
        official = (row.get("_meta") or {}).get("io.modelcontextprotocol.registry/official", {})
        published = to_epoch(official.get("publishedAt") or official.get("updatedAt"))
        url = server.get("repository", {}).get("url") if isinstance(server.get("repository"), dict) else None
        if not url:
            remotes = server.get("remotes") or []
            url = remotes[0].get("url") if remotes else None
        if not url:
            url = "https://registry.modelcontextprotocol.io/?search=" + urllib.parse.quote(server.get("name", ""))
        entry = item(source, name, url, published,
                     f"v{server.get('version', '?')} · {official.get('status', '')}".strip(" ·"),
                     summary=description)
        if entry:
            results.append(entry)
    results.sort(key=lambda row: row.get("ts") or 0, reverse=True)
    return results[: source.limit]


# Reddit wants a unique, self-identifying UA on OAuth calls.
REDDIT_UA = "macos:techdash:v1.0 (personal dashboard)"
_reddit_token = {"value": "", "expires": 0.0}
_reddit_lock = threading.Lock()


def reddit_token() -> str:
    """Client-credentials token for a Reddit "script" app. Cached until expiry."""
    with _reddit_lock:
        if _reddit_token["value"] and time.time() < _reddit_token["expires"] - 60:
            return _reddit_token["value"]
        client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        if not (client_id and secret):
            raise FetchError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        request = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": REDDIT_UA,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise FetchError(f"reddit auth HTTP {exc.code}") from exc
        token = payload.get("access_token")
        if not token:
            raise FetchError("reddit auth returned no token")
        _reddit_token["value"] = token
        _reddit_token["expires"] = time.time() + payload.get("expires_in", 3600)
        return token


def load_reddit(source, get) -> list[dict]:
    del get  # Reddit needs the bearer token and its own User-Agent
    request = urllib.request.Request(source.url, headers={
        "Authorization": f"bearer {reddit_token()}",
        "User-Agent": REDDIT_UA,
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc

    results = []
    for child in payload.get("data", {}).get("children", [])[: source.limit]:
        post = child.get("data", {})
        title = post.get("title")
        permalink = f"https://www.reddit.com{post.get('permalink', '')}"
        external = post.get("url") or permalink
        domain = post.get("domain", "")
        meta = f"{post.get('score', 0)} pts · {post.get('num_comments', 0)} comments"
        if domain and not domain.startswith("self."):
            meta += f" · {domain}"
        entry = item(source, title, external, post.get("created_utc"), meta,
                     summary=post.get("selftext", ""))
        if entry:
            # Link posts point outward; the thread is the second link, as on HN.
            if external != permalink:
                entry["discuss"] = permalink
            results.append(entry)
    return results


def load_hf_papers(source, get) -> list[dict]:
    payload = json.loads(get(source.url))
    results = []
    for row in payload[: source.limit]:
        paper = row.get("paper", {}) if isinstance(row, dict) else {}
        title = paper.get("title") or row.get("title")
        paper_id = paper.get("id")
        if not (title and paper_id):
            continue
        upvotes = paper.get("upvotes") or 0
        entry = item(source, title, f"https://huggingface.co/papers/{paper_id}",
                     to_epoch(paper.get("publishedAt") or row.get("publishedAt")),
                     f"▲ {upvotes} upvotes",
                     summary=paper.get("summary") or paper.get("abstract") or "")
        if entry:
            results.append(entry)
    return results


LOADERS = {
    "hn": load_hn,
    "tickers": load_tickers,
    "gh_trending": load_gh_trending,
    "crates": load_crates,
    "npm": load_npm,
    "go_index": load_go_index,
    "rubygems": load_rubygems,
    "jsdelivr": load_jsdelivr,
    "mcp_registry": load_mcp_registry,
    "hf_papers": load_hf_papers,
    "reddit": load_reddit,
}


def dedupe(items: list[dict]) -> list[dict]:
    """Drop repeats within one source, keeping the first (newest) occurrence.

    HN's search returns the same story resubmitted under several IDs, and the
    MCP registry lists every published version of a server. Both look like
    duplicate rows on a card.
    """
    seen, unique = set(), []
    for entry in items:
        key = entry["title"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def loader_for(source):
    if source.kind == "rss":
        return load_rss
    loader = LOADERS.get(source.parser)
    if loader is None:
        raise FetchError(f"no loader registered for parser={source.parser!r}")
    return loader


def authenticated_fetch(url: str) -> bytes:
    """GitHub's search API is 10 req/min anonymous, 30 with a token."""
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL) as response:
            return response.read()
    return fetch(url)


# ---------------------------------------------------------------------------
# Store: disk-backed cache, one JSON file per source.
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        os.makedirs(cache_dir, exist_ok=True)
        self._load_from_disk()

    def _path(self, source_id: str) -> str:
        return os.path.join(self.cache_dir, f"{source_id}.json")

    def _load_from_disk(self) -> None:
        for source in SOURCES:
            try:
                with open(self._path(source.id), encoding="utf-8") as handle:
                    self.entries[source.id] = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue

    def get(self, source_id: str) -> dict:
        return self.entries.get(source_id, {})

    def record(self, source, items: list[dict] | None, error: str | None) -> None:
        previous = self.entries.get(source.id, {})
        fails = 0 if error is None else previous.get("fails", 0) + 1
        entry = {
            "id": source.id,
            "name": source.name,
            "section": source.section,
            "fetched_at": time.time() if error is None else previous.get("fetched_at"),
            "checked_at": time.time(),
            "fails": fails,
            "error": error,
            # On failure keep whatever we last had -- a stale card beats a blank one.
            "items": items if error is None else previous.get("items", []),
        }
        with self.lock:
            self.entries[source.id] = entry
            try:
                with open(self._path(source.id), "w", encoding="utf-8") as handle:
                    json.dump(entry, handle)
            except OSError:
                pass

    def is_stale(self, source) -> bool:
        entry = self.entries.get(source.id)
        if not entry:
            return True
        checked = entry.get("checked_at") or 0
        backoff = min(2 ** entry.get("fails", 0), MAX_BACKOFF_MULTIPLIER)
        return (time.time() - checked) > source.ttl * backoff

    def snapshot(self) -> dict:
        with self.lock:
            entries = list(self.entries.values())
        items, health = [], []
        for entry in entries:
            source = srcmod.BY_ID.get(entry.get("id"))
            if source is None:
                continue
            items.extend(entry.get("items", []))
            health.append({
                "id": source.id,
                "name": source.name,
                "section": source.section,
                "count": len(entry.get("items", [])),
                "fetched_at": entry.get("fetched_at"),
                "error": entry.get("error"),
                "note": source.note,
            })
        missing = [s.id for s in SOURCES if s.id not in self.entries]
        health.extend({"id": s, "name": srcmod.BY_ID[s].name, "section": srcmod.BY_ID[s].section,
                       "count": 0, "fetched_at": None, "error": "not fetched yet",
                       "note": srcmod.BY_ID[s].note} for s in missing)
        items.sort(key=lambda row: row.get("ts") or 0, reverse=True)
        # Health stays in registry order so the UI can lay cards out the way the
        # registry reads, rather than alphabetically.
        order = {s.id: n for n, s in enumerate(SOURCES)}
        return {
            "generated_at": time.time(),
            "sections": [{"id": i, "label": l, "blurb": b} for i, l, b in srcmod.SECTIONS],
            "items": items,
            "health": sorted(health, key=lambda row: order.get(row["id"], 999)),
            "total_sources": len(SOURCES),
        }


# ---------------------------------------------------------------------------
# Refresher: a parallel lane for well-behaved hosts, a slow serialized lane for
# the ones that rate-limit (Reddit).
# ---------------------------------------------------------------------------

class Refresher:
    def __init__(self, store: Store):
        self.store = store
        self.polite_queue: queue.Queue = queue.Queue()
        self.in_flight: set[str] = set()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.last_sweep = 0.0

    def refresh_one(self, source) -> None:
        with self.lock:
            if source.id in self.in_flight:
                return
            self.in_flight.add(source.id)
        try:
            items = dedupe(loader_for(source)(source, authenticated_fetch))
            self.store.record(source, items, None)
        except FetchError as exc:
            self.store.record(source, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the sweep
            self.store.record(source, None, f"parse failed: {type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.in_flight.discard(source.id)

    def sweep(self, force: bool = False) -> int:
        due = [s for s in SOURCES if force or self.store.is_stale(s)]
        parallel = [s for s in due if s.lane == "default"]
        polite = [s for s in due if s.lane == "polite"]
        for source in polite:
            self.polite_queue.put(source)
        if parallel:
            with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
                list(pool.map(self.refresh_one, parallel))
        self.last_sweep = time.time()
        return len(due)

    def drain_polite(self) -> None:
        """Work the rate-limited queue to empty, synchronously. For --once."""
        while True:
            try:
                source = self.polite_queue.get_nowait()
            except queue.Empty:
                return
            self.refresh_one(source)
            time.sleep(POLITE_GAP_SECONDS)

    def _polite_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                source = self.polite_queue.get(timeout=2)
            except queue.Empty:
                continue
            self.refresh_one(source)
            self.stop_event.wait(POLITE_GAP_SECONDS)

    def _sweep_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sweep()
            except Exception as exc:  # noqa: BLE001 - the loop must never die
                print(f"  ! sweep error: {exc}", file=sys.stderr)
            self.stop_event.wait(60)

    def start_background(self) -> None:
        threading.Thread(target=self._sweep_worker, daemon=True, name="sweep").start()
        threading.Thread(target=self._polite_worker, daemon=True, name="polite").start()


# ---------------------------------------------------------------------------
# Brief export.
#
# There is no LLM in this file and no API key. `techdash brief` flattens the
# cache into one Markdown digest; you hand that to Claude Code (`/brief`) and it
# writes cache/_brief.json back. The dashboard renders whatever is in that file.
# ---------------------------------------------------------------------------

BRIEF_INPUT = os.path.join(HERE, "brief-input.md")
BRIEF_PATH = os.path.join(CACHE_DIR, "_brief.json")
BRIEF_INDEX = os.path.join(CACHE_DIR, "_brief_index.json")

# Package registries publish hundreds of rows an hour. They earn a small quota
# in the digest rather than drowning the headlines.
FIREHOSE_SOURCES = {"npm-new", "pypi-new", "pypi-updates", "go-index", "rubygems", "jsdelivr"}
BRIEF_QUOTA = 5
BRIEF_FIREHOSE_QUOTA = 4
BRIEF_MAX_ENTRIES = 260
PUNCT = re.compile(r"[^a-z0-9 ]+")


def story_key(title: str) -> str:
    """Collapse the same story reported by several feeds onto one key."""
    words = PUNCT.sub(" ", fold(title)).split()
    return " ".join(words)[:55]


def collect_brief_entries(store: Store, hours: int) -> list[dict]:
    cutoff = time.time() - hours * 3600
    recent = [
        row for row in store.snapshot()["items"]
        if row.get("ts") and row["ts"] >= cutoff
        and row["sid"] != "tickers" and not row.get("pre")
    ]

    used: dict[str, int] = {}
    grouped: dict[str, dict] = {}
    for row in recent:
        quota = BRIEF_FIREHOSE_QUOTA if row["sid"] in FIREHOSE_SOURCES else BRIEF_QUOTA
        if used.get(row["sid"], 0) >= quota:
            continue
        used[row["sid"]] = used.get(row["sid"], 0) + 1

        key = story_key(row["title"])
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "title": row["title"],
                "summary": row.get("sum", ""),
                "tag": row.get("tag", ""),
                "section": row["sec"],
                "ts": row["ts"],
                "meta": row.get("meta", ""),
                "sources": [{"src": row["src"], "url": row["url"]}],
            }
            continue
        # Same story, another outlet: keep the richer summary, note the source.
        if len(row.get("sum", "")) > len(entry["summary"]):
            entry["summary"] = row["sum"]
        if not any(s["src"] == row["src"] for s in entry["sources"]):
            entry["sources"].append({"src": row["src"], "url": row["url"]})

    # Group by section before time, so the digest opens on what people are
    # reading rather than on whatever npm published in the last 60 seconds.
    rank = {section: n for n, (section, _, _) in enumerate(srcmod.SECTIONS)}
    order = {"pulse": 0, "news": 1, "ai": 2, "releases": 3, "libs": 4, "market": 5, "community": 6}
    entries = sorted(
        grouped.values(),
        key=lambda row: (order.get(row["section"], rank.get(row["section"], 9)), -row["ts"]),
    )
    return entries[:BRIEF_MAX_ENTRIES]


def write_brief_input(store: Store, hours: int) -> tuple[str, int]:
    """Write the digest Claude Code reads, plus the index it cites into."""
    entries = collect_brief_entries(store, hours)
    index = {}
    lines = [
        f"# techdash digest — last {hours}h",
        "",
        f"{len(entries)} stories, newest first. Cross-source duplicates are merged.",
        "Cite stories by their [n] number; the dashboard resolves numbers to links.",
        "",
    ]
    for number, entry in enumerate(entries, start=1):
        index[str(number)] = {
            "title": entry["title"],
            "sources": entry["sources"],
        }
        outlets = ", ".join(source["src"] for source in entry["sources"])
        # Feeds occasionally stamp a publish time slightly in the future.
        age = f"{max(0.0, (time.time() - entry['ts']) / 3600):.0f}h"
        lines.append(f"[{number}] ({entry['section']}/{entry['tag'] or '-'} · {outlets} · {age}) {entry['title']}")
        if entry["meta"]:
            lines.append(f"    meta: {entry['meta']}")
        if entry["summary"]:
            lines.append(f"    {entry['summary']}")
        lines.append("")

    with open(BRIEF_INPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    with open(BRIEF_INDEX, "w", encoding="utf-8") as handle:
        json.dump({"window_hours": hours, "built_at": time.time(), "entries": index}, handle)
    return BRIEF_INPUT, len(entries)


def load_brief() -> dict | None:
    """The brief as written by Claude Code, with citation numbers resolved."""
    try:
        with open(BRIEF_PATH, encoding="utf-8") as handle:
            brief = json.load(handle)
        with open(BRIEF_INDEX, encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    entries = index.get("entries", {})
    for item in brief.get("items", []):
        links = []
        for number in item.get("sources", []):
            entry = entries.get(str(number))
            if entry:
                links.extend(entry["sources"])
        item["links"] = links
    brief["built_at"] = index.get("built_at")
    brief["window_hours"] = index.get("window_hours")
    return brief


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


def make_handler(store: Store, refresher: Refresher):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass  # the console is for status, not for an access log

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload) -> None:
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")

        def _send_file(self, name: str) -> None:
            path = os.path.join(STATIC_DIR, os.path.basename(name))
            if not os.path.isfile(path):
                self._send(404, b"not found", "text/plain")
                return
            with open(path, "rb") as handle:
                body = handle.read()
            extension = os.path.splitext(path)[1]
            self._send(200, body, CONTENT_TYPES.get(extension, "application/octet-stream"))

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
            route = urllib.parse.urlparse(self.path).path
            if route in ("/", "/index.html"):
                self._send_file("index.html")
            elif route == "/api/data":
                self._send_json(store.snapshot())
            elif route == "/api/brief":
                self._send_json(load_brief() or {"empty": True})
            elif route == "/api/status":
                self._send_json({
                    "last_sweep": refresher.last_sweep,
                    "in_flight": len(refresher.in_flight),
                    "queued": refresher.polite_queue.qsize(),
                })
            elif route.startswith("/static/"):
                self._send_file(route[len("/static/"):])
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if urllib.parse.urlparse(self.path).path == "/api/refresh":
                threading.Thread(target=refresher.sweep, kwargs={"force": True}, daemon=True).start()
                self._send_json({"ok": True})
                return
            self._send(404, b"not found", "text/plain")

    return Handler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def summarise(store: Store) -> str:
    entries = store.entries.values()
    live = sum(1 for e in entries if not e.get("error") and e.get("items"))
    items = sum(len(e.get("items", [])) for e in entries)
    broken = [e["name"] for e in entries if e.get("error") and not e.get("items")]
    line = f"  {live}/{len(SOURCES)} sources live · {items:,} items cached"
    if broken:
        line += f" · quiet: {', '.join(sorted(broken)[:6])}"
        if len(broken) > 6:
            line += f" +{len(broken) - 6}"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description="Local tech news dashboard.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "brief"],
                        help="serve the dashboard (default), or export a digest for Claude Code")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="don't open a browser")
    parser.add_argument("--once", action="store_true", help="refresh the cache and exit")
    parser.add_argument("--hours", type=int, default=24, help="brief: how far back to look")
    parser.add_argument("--stale-ok", action="store_true",
                        help="brief: use the cache as-is instead of refreshing first")
    args = parser.parse_args()

    store = Store(CACHE_DIR)
    refresher = Refresher(store)

    if args.command == "brief":
        if not args.stale_ok:
            print("techdash: refreshing stale sources…")
            refresher.sweep()
        path, count = write_brief_input(store, args.hours)
        print(f"  {count} stories from the last {args.hours}h → {path}")
        print()
        print("  Now, in Claude Code, run:  /brief")
        print("  (or just say: \"write the techdash brief\")")
        return 0

    if args.once:
        print("techdash: refreshing all sources...")
        started = time.time()
        refresher.sweep(force=True)
        refresher.drain_polite()  # no background worker in --once mode
        print(summarise(store))
        print(f"  done in {time.time() - started:.1f}s")
        return 0

    cached = sum(len(e.get("items", [])) for e in store.entries.values())
    print(f"techdash → http://{args.host}:{args.port}")
    print(f"  {len(SOURCES)} sources · {cached:,} items restored from cache")
    print("  refreshing in the background; the page fills in as feeds land")

    refresher.start_background()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(store, refresher))
    server.daemon_threads = True
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + summarise(store))
        print("  stopped.")
        refresher.stop_event.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
