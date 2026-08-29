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

import base64
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

from . import logs, portfolio
from .config import Settings
from .models.api import CalendarEvent, CalendarInfo, CalendarResponse, MarketRow, NewsItem
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


class WrittenEvent(NamedTuple):
    """What came back from writing one event.

    `event_id` is the point of it, and it is optional: it is decoded out of a
    URL gcalcli prints, and a decode that fails must not fail the write. An
    item holding None here is one whose Google event can never be found again
    by id -- it stays on the calendar when the item is ticked, and the panel
    says so rather than deleting by title and hoping.
    """

    event_id: str | None
    url: str | None
    output: str


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


def _yahoo_symbol(ticker: str) -> str:
    """What Yahoo calls a ticker the rest of the world spells differently.

    Two rewrites, both of which produce a 404 rather than an error that says
    what is wrong:

    `^SPX` is the S&P under its Yahoo name `^GSPC` -- longstanding, and the
    reason this function exists at all.

    A dot in a class share becomes a hyphen. Brokerages write Berkshire's B
    shares as `BRK.B` and Yahoo wants `BRK-B`; the positions file therefore
    supplies the one spelling the quote feed will not accept, which showed up
    the moment holdings started reaching this code. The display ticker keeps
    the dot, because that is what the file says and what the row is labelled
    with -- only the URL is rewritten.
    """
    if ticker == "^SPX":
        return "^GSPC"
    return ticker.replace(".", "-")


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

        Shared by every RSS source the app reads. One dead
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

    # -- markets ------------------------------------------------------------

    def markets(self) -> Cached[list[MarketRow]]:
        # The ticker set is in the cache key, not just the payload. It is
        # derived from a file you edit -- adding a position and reloading has
        # to show the new symbol, and with a bare "markets" key it would have
        # shown the old list for up to ten more minutes and looked like the
        # edit had not taken. Stale entries for previous ticker sets age out on
        # their own; there are at most a handful of them in a session.
        tickers = self._market_tickers()
        return self.cached(
            f"markets:{','.join(tickers)}",
            self.settings.markets.cache_seconds,
            lambda: self._fetch_markets(tickers),
        )

    def _market_tickers(self) -> list[str]:
        """The configured tickers, then anything held that is not already there.

        Config order first on purpose: the index and the majors are the frame
        the holdings are read against, and a portfolio that grows should not
        push ^SPX off the top of the panel.

        A failure here is swallowed. The movements list is not the portfolio
        panel -- if the positions file is missing or unreadable, the right
        outcome is the four configured tickers, not a market panel that has
        broken because of a spreadsheet.
        """
        tickers = list(self.settings.markets.tickers)
        if not self.settings.markets.include_holdings:
            return tickers
        try:
            held = portfolio.tickers(self.settings.path("positions"))
        except OSError:
            log.warning("could not read the positions file; showing configured tickers only")
            return tickers
        known = {t.upper() for t in tickers}
        return tickers + [t for t in held if t.upper() not in known]

    def _fetch_markets(self, tickers: list[str]) -> list[MarketRow]:
        rows: list[MarketRow] = []
        any_ok = False
        for ticker in tickers:
            symbol = _yahoo_symbol(ticker)
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

    def gcalcli(self, *args: str, timeout: float | None = None) -> str:
        """Run gcalcli with an argv list.

        Was a hand-quoted string through `bash -lc`, which meant every call
        re-sourced the full login profile and every argument had to survive
        POSIX quoting by hand. `subprocess.run` with a list passes the
        arguments through untouched, so an apostrophe in an event title is no
        longer a shell metacharacter.

        stdin is closed, deliberately, and it is the fix for the bug that made
        adding an event impossible from the day it was written. gcalcli drops
        to an *interactive prompt* whenever a subcommand cannot resolve its
        target calendar on its own -- and with nine calendars on this account
        and no gcalclirc to name a default, `quick` could never resolve one. It
        printed a numbered menu and called `input()`. Inheriting the parent's
        stdin meant that menu blocked until `timeout_seconds` expired, so the
        panel reported "gcalcli timed out after 60s" for what was really a
        missing argument. A closed stdin turns the same class of mistake into
        an immediate EOFError, with the menu it was about to draw sitting in
        stderr where it reads as the bug report it is.
        """
        result = self._run_gcalcli(args, timeout=timeout)
        if result.returncode == 0:
            return result.stdout

        message = (result.stderr or result.stdout).strip()

        # A calendar deleted in Google stays in gcalcli's own cache, which is
        # written once and never expires -- this account's was three months
        # old. `agenda` with no `--calendar` then queries every cached calendar
        # including the dead one, Google answers 404 notFound, and gcalcli exits
        # 1. One deleted calendar took down the entire week, and the only cure
        # was knowing to run `gcalcli --refresh` by hand.
        #
        # So the cure is automatic, once, and only for this signature: refresh
        # the calendar list and try again. Anything else, or a second failure,
        # is reported as it always was -- a blanket retry would double every
        # genuine error and hide the fact that it happened twice.
        if _is_stale_cache(message):
            log.warning("gcalcli names a calendar Google no longer has; refreshing its cache")
            self._run_gcalcli(("--refresh", "list"), timeout=timeout)
            # The list this process is holding named the dead calendar too.
            self.invalidate("calendarlist")
            result = self._run_gcalcli(args, timeout=timeout)
            if result.returncode == 0:
                log.info("gcalcli recovered after a cache refresh")
                return result.stdout
            message = (result.stderr or result.stdout).strip()

        log.error("gcalcli %s exited %s: %s", args, result.returncode, message)
        raise FeedError(_short_gcalcli_error(message))

    def _run_gcalcli(
        self, args: tuple[str, ...] | list[str], *, timeout: float | None
    ) -> subprocess.CompletedProcess[str]:
        cfg = self.settings.calendar
        argv = [*cfg.command, *args]
        if os.name == "nt":
            argv = ["wsl", "-e", *argv]
        limit = cfg.timeout_seconds if timeout is None else timeout
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as e:
            raise FeedError(f"{cfg.command[0]} not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise FeedError(f"{cfg.command[0]} timed out after {limit}s") from e

    def calendar(self, start: dt.date, days: int = 7) -> Cached[CalendarResponse]:
        return self.cached(
            f"calendar:{start.isoformat()}:{days}",
            self.settings.calendar.cache_seconds,
            lambda: self._fetch_calendar(start, days),
        )

    def _fetch_calendar(self, start: dt.date, days: int) -> CalendarResponse:
        # One agenda call, not two. The old pair asked for today and then for
        # today-plus-seven, which fetched today twice and put its events in
        # both lists.
        end = start + dt.timedelta(days=days)
        tsv = self.gcalcli("agenda", str(start), str(end), "--tsv", "--details", "calendar")
        return CalendarResponse(
            start=start,
            days=days,
            events=_parse_agenda(tsv),
            calendars=self.calendars(),
        )

    def calendar_history(self, today: dt.date) -> list[CalendarEvent]:
        """Years of events, for the classifier to be fitted on.

        One agenda call covering the whole window rather than one per calendar:
        `--details calendar` labels each row, which is the entire reason the
        training set costs nothing to build.
        """
        cfg = self.settings.calendar
        start = today - dt.timedelta(days=cfg.history_days)
        # Forward as well as back. Next semester's classes are already on the
        # calendar in August, and they are the most useful rows in the set.
        end = today + dt.timedelta(days=cfg.history_days_ahead)
        return self.cached(
            "calendarhistory",
            cfg.history_cache_seconds,
            lambda: _parse_agenda(
                self.gcalcli("agenda", str(start), str(end), "--tsv", "--details", "calendar")
            ),
        ).value

    def calendars(self) -> list[CalendarInfo]:
        """The account's calendar list, cached hard.

        Separately cached from the agenda and for far longer: a calendar is
        created every few months, and this is on the path of every add.
        """
        return self.cached(
            "calendarlist",
            self.settings.calendar.list_cache_seconds,
            self._fetch_calendars,
        ).value

    def _fetch_calendars(self) -> list[CalendarInfo]:
        return _parse_calendar_list(self.gcalcli("list"))

    def add_calendar_event(
        self,
        *,
        calendar: str,
        title: str,
        when: str,
        duration: int | None = None,
        all_day: bool = False,
    ) -> WrittenEvent:
        """Write one event, with its target named.

        Was `gcalcli quick <text>` -- no `--calendar`, which on an account with
        more than one writable calendar is not a thing gcalcli can do. See the
        note in `gcalcli()`; it hung rather than failing.

        `add` rather than `quick`, so the fields are ours. `quick` hands the
        whole string to Google's parser and returns whatever it decided, which
        cannot be shown to anyone before it is already written. Here the date
        and the title have been resolved locally and can be previewed.

        `--details url` is not cosmetic. gcalcli's `add` prints *nothing* by
        default and returns no identifier -- the one line it will print, and
        only under this flag, is the event's htmlLink, whose `eid` decodes to
        the Google event id. That id is the only durable handle on what was
        just written, and without it a todo can never point at its own event:
        completing one would have to search Google by title and hope.
        """
        args = [
            "add",
            "--calendar", calendar,
            "--title", title,
            "--when", when,
            "--details", "url",
            "--noprompt",  # belt and braces: stdin is closed anyway
        ]
        if all_day:
            args.append("--allday")
        # gcalcli wants a duration for every event, all-day included -- there
        # it counts days, not minutes.
        args += ["--duration", str(duration if duration is not None else (1 if all_day else 60))]

        out = self.gcalcli(*args, timeout=self.settings.calendar.write_timeout_seconds)
        # Every cached window, not just the one holding today: the event can
        # land on any date the text named. `invalidate` adds the `:` itself, so
        # this drops every `calendar:<date>:<days>` and leaves `calendarlist`
        # -- which adding an event cannot have changed -- alone.
        self.invalidate("calendar")
        url = _first_url(out)
        return WrittenEvent(event_id=_event_id_from_url(url), url=url, output=out.strip())

    def delete_calendar_event(
        self, *, calendar: str, title: str, date: dt.date, event_id: str | None
    ) -> bool:
        """Remove one event. Returns False if it was already gone.

        This is the most dangerous call in the file and it is shaped around
        that. gcalcli has no delete-by-id: `delete` takes a *search string* and
        removes everything it matches, and `--iamaexpert` -- which is required,
        since stdin is closed and the confirmation prompt would EOF -- removes
        them without asking. So the search is run first, by this code, and the
        delete only happens once the answer is known to be a single event.

        Three narrowings make that answer trustworthy: one calendar, one day,
        and an exact title match applied here rather than by Google, whose `q`
        is a full-text search and will happily return "Chapter 3 notes" for
        "Chapter 3". If the id we recorded at write time is not among the
        matches the event is already gone, which is a success with nothing to
        do rather than an error. If anything else about the set is surprising,
        this refuses and says so -- an ambiguous delete is not worth guessing
        at, because the thing being guessed at is somebody's calendar.
        """
        end = date + dt.timedelta(days=1)
        tsv = self.gcalcli(
            "search", title, str(date), str(end),
            "--tsv", "--details", "id", "--calendar", calendar,
            timeout=self.settings.calendar.write_timeout_seconds,
        )
        rows = _parse_search(tsv)
        exact = [row for row in rows if row.title == title]

        if event_id is not None and not any(row.id == event_id for row in exact):
            log.info("calendar delete: %r on %s is already gone", title[:60], date)
            return False
        if len(exact) != 1:
            raise FeedError(
                f"{len(exact)} events on {calendar} named {title!r} on {date}"
                " — left alone rather than guessing which to delete"
            )

        self.gcalcli(
            "delete", title, str(date), str(end),
            "--calendar", calendar, "--iamaexpert",
            timeout=self.settings.calendar.write_timeout_seconds,
        )
        self.invalidate("calendar")
        return True

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


def _is_stale_cache(message: str) -> bool:
    """Does this failure mean gcalcli asked about a calendar that is gone?

    Matched on both halves rather than on "404" alone: a 404 naming an *event*
    is an ordinary race with something being deleted mid-agenda, and refreshing
    the calendar list would not help it.
    """
    return "notFound" in message and "/calendars/" in message


def _short_gcalcli_error(message: str) -> str:
    """The one line worth putting in a panel.

    gcalcli fails by letting a Python traceback reach stderr, and the old code
    showed the *first* 200 characters of it -- which is `sys.exit(main())` and
    two frames of gcalcli's own argument parsing, identical for every possible
    error. The exception is on the last line, so that is what gets shown, and
    the traceback stays in the log where it is useful.
    """
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "gcalcli failed"
    last = lines[-1]
    # "googleapiclient.errors.HttpError: <HttpError 404 ...>" -> drop the
    # module path, which is never the interesting half.
    if ":" in last and last.split(":", 1)[0].count(".") >= 2:
        last = last.split(":", 1)[1].strip()
    return last[:200] or "gcalcli failed"


_URL = re.compile(r"https?://\S+")


def _first_url(text: str) -> str | None:
    match = _URL.search(text)
    return match.group(0).rstrip(".,)") if match else None


def _event_id_from_url(url: str | None) -> str | None:
    """`...event?eid=<base64>` -> the Google event id.

    The `eid` is base64url of "<event id> <calendar id>", which is not a
    documented contract so much as a stable accident -- so every step of the
    decode is allowed to fail, and failing returns None rather than raising.
    A todo with no event id is a todo whose completion cannot delete anything
    and says so, which is a small loss; a failed *add* because a URL was
    spelled unexpectedly would be a large one.
    """
    if not url:
        return None
    try:
        query = urllib.parse.urlparse(url).query
        eid = urllib.parse.parse_qs(query).get("eid", [None])[0]
        if not eid:
            return None
        # base64url, and Google strips the padding.
        raw = base64.urlsafe_b64decode(eid + "=" * (-len(eid) % 4))
        first = raw.decode("utf-8", "replace").split(" ", 1)[0].strip()
        return first or None
    except Exception:
        log.debug("could not decode an event id out of %r", url[:120])
        return None


class SearchRow(NamedTuple):
    id: str
    title: str


def _parse_search(tsv: str) -> list[SearchRow]:
    """`search --tsv --details id` -> the rows, addressed by column name.

    Same header-driven approach as `_parse_agenda`, and for the same reason:
    which column holds what is a function of the `--details` flags.
    """
    lines = tsv.strip().splitlines()
    if not lines:
        return []
    index = {name: i for i, name in enumerate(lines[0].split("\t"))}
    # The two columns this function exists to read, and the whole of the header
    # check. It used to additionally require `start_date` in column 0 -- which
    # is what `--details calendar` produces, and *not* what `--details id`
    # does: gcalcli puts `id` first there, so every search parsed to zero rows.
    #
    # That was not a cosmetic failure. `delete_calendar_event` reads this to
    # find the event it is about to remove, and no rows meant "the id we
    # recorded is not among the matches", which it reports as *already gone*.
    # So every tick marked its event deleted and left it standing on the
    # calendar, silently, for as long as this has been here.
    #
    # A header-driven parser exists precisely so column order does not matter;
    # pinning position 0 gave that up and kept none of the safety. Requiring
    # both names is the guard that was wanted -- a line of event data will not
    # contain the literal words `id` and `title` in tab-separated fields.
    if "id" not in index or "title" not in index:
        log.warning("gcalcli search had no usable header; first line was %r", lines[0][:120])
        return []

    rows: list[SearchRow] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if max(index["id"], index["title"]) >= len(parts):
            continue
        rows.append(
            SearchRow(id=parts[index["id"]].strip(), title=parts[index["title"]].strip())
        )
    return rows


def _parse_agenda(tsv: str) -> list[CalendarEvent]:
    """gcalcli's `--tsv` output -> events, addressed by column *name*.

    Was five fixed indices. `--details calendar` appends a sixth column and
    `--details all` puts nine *before* the title, so any position taken as a
    constant is a column the next flag moves. gcalcli always emits the header
    row, so there is no reason to guess.
    """
    lines = tsv.strip().splitlines()
    if not lines:
        return []

    header = lines[0].split("\t")
    if header[0] != "start_date":
        log.warning("gcalcli agenda had no header row; first line was %r", lines[0][:120])
        return []
    index = {name: i for i, name in enumerate(header)}

    def field(parts: list[str], name: str) -> str:
        i = index.get(name, -1)
        return parts[i].strip() if 0 <= i < len(parts) else ""

    events: list[CalendarEvent] = []
    for line in lines[1:]:
        parts = line.split("\t")
        date = _maybe_date(field(parts, "start_date"))
        if date is None:
            log.debug("skipping unparseable agenda row: %r", line)
            continue
        # Google's end date is exclusive for all-day events, so a one-day
        # holiday ends on the following morning. Only worth carrying when it
        # actually disagrees with the start.
        end_date = _maybe_date(field(parts, "end_date"))
        events.append(
            CalendarEvent(
                date=date,
                start=field(parts, "start_time") or None,
                end=field(parts, "end_time") or None,
                title=field(parts, "title") or "(untitled)",
                calendar=field(parts, "calendar"),
                end_date=end_date if end_date and end_date != date else None,
            )
        )
    return events


def _parse_calendar_list(out: str) -> list[CalendarInfo]:
    """`gcalcli list` -> the account's calendars.

    `list` is the one subcommand with no `--tsv`, so this reads the drawn
    table: two leading spaces, the access role, then the title, which may
    contain spaces of its own and so takes the rest of the line.
    """
    calendars: list[CalendarInfo] = []
    roles = ("owner", "writer", "reader", "freebusy")
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] in roles:
            calendars.append(CalendarInfo(access=parts[0], name=parts[1].strip()))
    return calendars
