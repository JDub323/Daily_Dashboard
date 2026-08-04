"""Everything that reaches outside the process: RSS, Yahoo, arXiv, lichess,
gcalcli.

The important change here is `cached`. The old one caught *every* exception
and served stale on failure, so a `KeyError` from our own Yahoo parser was
indistinguishable from Yahoo being down -- and the panel then served week-old
prices forever with no error anywhere. Now only transport failures fall back
to stale; a bug in our parsing propagates, gets logged with a traceback, and
shows up in the panel as an error, which is the only way it ever gets fixed.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any, Generic, NamedTuple, TypeVar

from . import logs
from .config import Settings
from .models.api import CalendarEvent, CalendarResponse, MarketRow, NewsItem
from .models.common import Base
from .models.domain import ChessPuzzle

log = logs.get(__name__)

T = TypeVar("T")

USER_AGENT = "Mozilla/5.0 (daily-dashboard)"

# Transport failures: the feed is unreachable, slow, or returned garbage.
# These are the only conditions under which stale data is the right answer.
# `urllib.error.HTTPError` and `URLError` are both `OSError` subclasses.
TRANSPORT_ERRORS = (
    OSError,
    TimeoutError,
    http.client.HTTPException,
    ET.ParseError,
    json.JSONDecodeError,
    UnicodeDecodeError,
)


# Query parameters that identify a *referral*, never an article. Feeds vary
# these between fetches, so a byte-comparison of two URLs for the same story
# can differ on nothing but the campaign tag.
TRACKING_PARAMS = ("fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "at_medium", "at_campaign")
TRACKING_PREFIXES = ("utm_",)


def normalize_link(url: str) -> str:
    """The identity of a story: same article, same site, same author.

    Deliberately conservative. The whole query string is *not* dropped --
    plenty of sites carry the article id there (`?id=`, `?storyId=`), and
    dropping it would collapse a whole publication into one key. Only known
    referral parameters go, plus the fragment, plus case in the scheme and
    host (which are case-insensitive per RFC 3986; the path is not).

    Trailing slashes are left alone. They are a real difference in the URL,
    and the case they would fix -- two *different* feeds carrying the same
    story -- is not one this can solve anyway: different publications mean
    different domains, so no normalisation makes those equal. That is fine.
    The same story from two outlets is two stories here, by request.
    """
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parts.scheme or not parts.netloc:
        return url.strip()

    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
        and not any(k.lower().startswith(p) for p in TRACKING_PREFIXES)
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urllib.parse.urlencode(kept),
            "",  # the fragment is a position in the page, not a document
        )
    )


def apply_quotas(items: list[NewsItem], quotas: list[Any]) -> list[NewsItem]:
    """Take the first `n` of each category, in the order the quotas are written.

    Split out of `_fetch_news` so it runs *after* the seen-link filter. Pure,
    and module-level rather than a method, because the filtering between the
    fetch and this needs a database and `Feeds` has no connection.
    """
    by_cat: dict[str, list[NewsItem]] = {}
    for item in items:
        by_cat.setdefault(item.cat, []).append(item)
    picks: list[NewsItem] = []
    for quota in quotas:
        picks += by_cat.get(quota.cat, [])[: quota.n]
    return picks


class FeedError(RuntimeError):
    """A feed could not be fetched or produced nothing usable.

    Treated like a transport error by `cached`: raised by the aggregate
    fetchers when every underlying source failed, which is a real outage
    rather than a bug in the parsing.
    """


class ArxivEntry(Base):
    """A candidate from the arXiv query, before it becomes a stored `Paper`."""

    title: str
    link: str
    summary: str
    published: dt.date | None = None
    fresh: bool = False


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class Cached(NamedTuple, Generic[T]):
    """A cached value, and how much to trust it.

    `fetched_at` is when the value came off the wire, not when it was last
    handed out: an hour-old quote is an hour old however many requests it has
    answered since.

    `stale` is the distinction the panel actually needs. Inside the TTL a
    cached value is simply *current* -- that is what the cache is for. Past the
    TTL, a cached value means the refetch was attempted and the source did not
    answer, so what is on screen is a fallback. Both cases used to leave the
    process as the same bare list, which is why a Yahoo outage rendered
    identically to live quotes.
    """

    value: T
    fetched_at: float
    ttl: int
    stale: bool = False


class Feeds:
    """External data, with a stale-tolerant cache.

    One instance lives on the app; the cache is per-process, which is all a
    single-device localhost server needs.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, Cached[Any]] = {}

    # -- cache --------------------------------------------------------------

    def cached(self, name: str, ttl: int, fn: Callable[[], T]) -> Cached[T]:
        now = time.time()
        entry = self._cache.get(name)
        if entry and now - entry.fetched_at < ttl:
            return entry
        try:
            value = fn()
        except (*TRANSPORT_ERRORS, FeedError) as e:
            if entry:
                age = int(now - entry.fetched_at)
                log.warning("%s unreachable (%s); serving cache from %ss ago", name, e, age)
                # Flagged from here on, and `fetched_at` deliberately keeps the
                # original fetch time: the value did not become newer by being
                # served again, and leaving the timestamp alone is also what
                # keeps the entry expired, so the next request retries rather
                # than settling into the fallback for a full TTL.
                fallback = entry._replace(stale=True, ttl=ttl)
                self._cache[name] = fallback
                return fallback
            log.warning("%s unreachable and nothing cached: %s", name, e)
            raise
        self._cache[name] = Cached(value, now, ttl)
        return self._cache[name]

    def invalidate(self, prefix: str) -> None:
        for key in [k for k in self._cache if k == prefix or k.startswith(f"{prefix}:")]:
            del self._cache[key]

    # -- news ---------------------------------------------------------------

    def proxify(self, link: str) -> str:
        if any(d in link for d in self.settings.news.paywalled):
            return self.settings.news.proxy_prefix + urllib.parse.quote(link, safe="")
        return link

    def news(self) -> Cached[list[NewsItem]]:
        """Every item every feed offered, in feed order. *Not* quota-sliced.

        The quotas moved to `apply_quotas`, downstream of the seen-link
        filter, and that ordering is the whole point: slicing to two politics
        headlines and *then* dropping the ones already read leaves you with
        one. Filtering first and slicing second keeps the category full.
        """
        return self.cached("news", self.settings.news.cache_seconds, self._fetch_news)

    def _rss(self, sources: list[tuple[str, str]], label: str) -> list[NewsItem]:
        """`(url, category)` pairs -> every item they offered, in source order.

        Shared by the headline pool and the per-issue debate feeds. One dead
        feed is normal and is logged rather than raised; every feed dead is a
        `FeedError`, which is what `cached` needs to see before it will serve a
        stale copy.
        """
        items: list[NewsItem] = []
        seen_in_fetch: set[str] = set()
        failures: list[str] = []
        for url, cat in sources:
            try:
                root = ET.fromstring(http_get(url).encode())
            except TRANSPORT_ERRORS as e:
                failures.append(f"{url}: {e}")
                continue
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not (title and link):
                    continue
                key = normalize_link(link)
                # Within one fetch as well as across days: a feed that lists
                # an item twice, or two feeds on the same site, would
                # otherwise put the identical URL on screen twice at once.
                if key in seen_in_fetch:
                    continue
                seen_in_fetch.add(key)
                items.append(NewsItem(title=title, link=self.proxify(link), cat=cat, key=key))
        if failures:
            log.info("%d/%d %s feeds failed: %s", len(failures), len(sources), label, failures)
        if not items:
            raise FeedError(f"all {label} feeds failed")
        return items

    def _fetch_news(self) -> list[NewsItem]:
        cfg = self.settings.news
        return self._rss([(str(f.url), f.cat) for f in cfg.feeds], "news")

    def issue_news(self, issue: Any) -> Cached[list[NewsItem]]:
        """Headline candidates for one debate issue.

        With no `feeds` of its own an issue reads the `[news]` pool, which is
        already being fetched for the headlines panel -- so the common case
        costs nothing and needs no configuration. Issues that name their own
        feeds get a cache entry of their own, keyed by slug, and those items
        are *added* to the general pool rather than replacing it: a dedicated
        immigration feed should widen what can be found, not narrow it to one
        outlet's framing.
        """
        if not issue.feeds:
            return self.news()
        general = self.news().value
        own = self.cached(
            f"issue:{issue.slug}",
            self.settings.news.cache_seconds,
            lambda: self._rss(
                [(str(url), f"debate:{issue.slug}") for url in issue.feeds], issue.slug
            ),
        )
        keys = {item.key for item in own.value}
        return own._replace(value=[*own.value, *(i for i in general if i.key not in keys)])

    # -- markets ------------------------------------------------------------

    def markets(self) -> Cached[list[MarketRow]]:
        return self.cached("markets", self.settings.markets.cache_seconds, self._fetch_markets)

    def _fetch_markets(self) -> list[MarketRow]:
        rows: list[MarketRow] = []
        any_ok = False
        for ticker in self.settings.markets.tickers:
            symbol = "^GSPC" if ticker == "^SPX" else ticker  # yahoo's S&P symbol
            row, errors = None, []
            for host in ("query1", "query2"):
                try:
                    row = self._yahoo_row(ticker, symbol, host)
                    any_ok = True
                    break
                except (*TRANSPORT_ERRORS, FeedError) as e:
                    # Collect rather than overwrite: the old code kept only
                    # the last attempt's error, so the reported failure was
                    # frequently not the one that mattered.
                    errors.append(f"{host}: {e}")
            rows.append(row or MarketRow(ticker=ticker, error="; ".join(errors)[:160]))
        if rows and not any_ok:
            raise FeedError("every ticker failed: " + (rows[0].error or "?"))
        return rows

    def _yahoo_row(self, ticker: str, symbol: str, host: str) -> MarketRow:
        raw = http_get(
            f"https://{host}.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?range=1mo&interval=1d",
            headers={"Accept": "application/json"},
        )
        result = json.loads(raw)["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None][-30:]
        meta = result["meta"]

        # A newly-listed ticker can have a single close, and the two meta
        # fields are not guaranteed to be present. The old code indexed
        # closes[-2] unguarded and raised IndexError for exactly that case.
        last = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        prev = meta.get("regularMarketPreviousClose") or (closes[-2] if len(closes) > 1 else None)
        if last is None:
            raise FeedError(f"{symbol}: no price in the response")
        if not prev:
            # Price without a comparison point: still worth showing.
            return MarketRow(ticker=ticker, price=last, spark=closes)
        return MarketRow(
            ticker=ticker,
            price=last,
            change_pct=round((last - prev) / prev * 100, 2),
            spark=closes,
        )

    # -- calendar -----------------------------------------------------------

    def gcalcli(self, *args: str) -> str:
        """Run gcalcli with an argv list.

        Was a hand-quoted string through `bash -lc`, which meant every call
        re-sourced the full login profile and every argument had to survive
        POSIX quoting by hand. `subprocess.run` with a list passes the
        arguments through untouched, so an apostrophe in an event title is no
        longer a shell metacharacter.
        """
        cfg = self.settings.calendar
        argv = [*cfg.command, *args]
        if os.name == "nt":
            argv = ["wsl", "-e", *argv]
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True, timeout=cfg.timeout_seconds, check=False
            )
        except FileNotFoundError as e:
            raise FeedError(f"{cfg.command[0]} not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise FeedError(f"{cfg.command[0]} timed out after {cfg.timeout_seconds}s") from e
        if r.returncode != 0:
            message = (r.stderr or r.stdout).strip()
            log.error("gcalcli %s exited %s: %s", args, r.returncode, message)
            raise FeedError(message[:200] or "gcalcli failed")
        return r.stdout

    def calendar(self, today: dt.date) -> Cached[CalendarResponse]:
        return self.cached(
            f"calendar:{today.isoformat()}",
            self.settings.calendar.cache_seconds,
            lambda: self._fetch_calendar(today),
        )

    def _fetch_calendar(self, today: dt.date) -> CalendarResponse:
        week_end = (today + dt.timedelta(days=7)).isoformat()
        day = self.gcalcli("agenda", f"{today} 00:00", f"{today} 23:59", "--tsv")
        week = self.gcalcli("agenda", str(today), week_end, "--tsv")
        return CalendarResponse(today=_parse_agenda(day), week=_parse_agenda(week))

    def add_calendar_event(self, text: str) -> str:
        out = self.gcalcli("quick", text)
        # The agenda is now wrong; drop it rather than show a stale one.
        # Every cached day, not just "today" -- `quick` parses the date out of
        # the text, so the new event can land on any of them, and the server
        # is not the authority on which one is today anyway.
        self.invalidate("calendar")
        return out

    # -- arXiv --------------------------------------------------------------

    def papers(self, today: dt.date) -> Cached[list[ArxivEntry]]:
        """The whole candidate list, freshest first.

        Was `paper()`, returning only the top one. `max_results` already
        fetches twenty and threw nineteen away, so the day arXiv has nothing
        new the top entry is yesterday's -- which is how the same paper came
        round twice and got marked read twice. Returning the list lets the
        caller skip the ones already surfaced.
        """
        return self.cached(
            "papers", self.settings.arxiv.cache_seconds, lambda: self._fetch_papers(today)
        )

    def _fetch_papers(self, today: dt.date) -> list[ArxivEntry]:
        cfg = self.settings.arxiv
        url = (
            "http://export.arxiv.org/api/query?search_query="
            + urllib.parse.quote(cfg.query)
            + f"&sortBy=submittedDate&sortOrder=descending&max_results={cfg.max_results}"
        )
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(http_get(url).encode())
        papers = [
            ArxivEntry(
                title=re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip(),
                link=e.findtext("a:id", "", ns),
                summary=re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip()[:400],
                published=_maybe_date(e.findtext("a:published", "", ns)[:10]),
            )
            for e in root.findall("a:entry", ns)
        ]
        if not papers:
            raise FeedError("arXiv returned nothing")
        yesterday = today - dt.timedelta(days=1)
        fresh = {p.link for p in papers if p.published and p.published >= yesterday}
        # Freshest first, but everything stays in the list: on a quiet day the
        # caller still needs candidates to fall through to.
        return sorted(
            (p.model_copy(update={"fresh": p.link in fresh}) for p in papers),
            key=lambda p: not p.fresh,
        )

    # -- lichess ------------------------------------------------------------

    def chess_puzzle(self, rating: int) -> ChessPuzzle:
        """Not cached: a fresh puzzle per request is the point.

        `pgn`, `solution` and `initialPly` are dropped with the inline board.
        `rating` stays -- it is the elo opponent.
        """
        difficulty = _difficulty(rating)
        raw = http_get(f"https://lichess.org/api/puzzle/next?difficulty={difficulty}")
        p = json.loads(raw)["puzzle"]
        return ChessPuzzle(
            id=p["id"],
            rating=p["rating"],
            themes=p.get("themes", []),
            url=f"https://lichess.org/training/{p['id']}",
        )


# --- helpers ---------------------------------------------------------------


def _difficulty(rating: int) -> str:
    """Anonymous lichess difficulties are offsets from ~1500."""
    if rating < 1050:
        return "easiest"
    if rating < 1350:
        return "easier"
    if rating < 1650:
        return "normal"
    if rating < 1950:
        return "harder"
    return "hardest"


def _maybe_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_agenda(tsv: str) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    for line in tsv.strip().splitlines():
        parts = line.split("\t")
        if parts and parts[0] == "start_date":  # gcalcli tsv header row
            continue
        if len(parts) >= 5:
            date = _maybe_date(parts[0])
            if date is None:
                log.debug("skipping unparseable agenda row: %r", line)
                continue
            events.append(CalendarEvent(date=date, start=parts[1], end=parts[3], title=parts[4]))
    return events
