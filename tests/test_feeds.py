"""Feeds: the cache's exception policy, the Yahoo parsing, and gcalcli's argv.

No network. Every fetch is monkeypatched.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess

import pytest

from dashboard import feeds
from dashboard.feeds import FeedError, Feeds

from .conftest import TODAY


@pytest.fixture
def fetcher(settings) -> Feeds:
    return Feeds(settings)


# --- the cache -------------------------------------------------------------


def test_a_fresh_value_is_cached(fetcher):
    calls = []

    def fn():
        calls.append(1)
        return "value"

    assert fetcher.cached("x", 60, fn).value == "value"
    assert fetcher.cached("x", 60, fn).value == "value"
    assert len(calls) == 1


def test_a_cached_hit_is_not_stale(fetcher):
    """Inside the TTL a cached value is *current*, not stale.

    The distinction is the whole point of the flag: serving from cache is what
    the cache is for, while serving past the TTL means the refetch was tried
    and the source did not answer.
    """
    first = fetcher.cached("x", 60, lambda: "value")
    second = fetcher.cached("x", 60, lambda: "value")
    assert (first.stale, second.stale) == (False, False)
    # And the timestamp is the fetch's, not the moment it was handed out again.
    assert second.fetched_at == first.fetched_at


def test_an_expired_entry_is_refetched(fetcher):
    fetcher.cached("x", 60, lambda: "old")
    fetcher._cache["x"] = feeds.Cached("old", 0, 60)
    assert fetcher.cached("x", 60, lambda: "new").value == "new"


@pytest.mark.parametrize(
    "error",
    [OSError("connection reset"), TimeoutError("timed out"), FeedError("all feeds failed")],
)
def test_a_transport_failure_serves_stale(fetcher, error):
    fetcher.cached("x", 60, lambda: "stale")
    fetcher._cache["x"] = feeds.Cached("stale", 0, 60)

    def boom():
        raise error

    got = fetcher.cached("x", 60, boom)
    assert got.value == "stale"
    # Flagged, so the panel can say so rather than showing it as live.
    assert got.stale is True
    # The fetch time is the original one: being served again did not make the
    # value any newer, and that is what the "as of" line has to report.
    assert got.fetched_at == 0


def test_a_stale_entry_keeps_being_retried(fetcher):
    """The fallback must not settle in for a full TTL.

    Leaving `fetched_at` at the original fetch keeps the entry expired, so the
    next request attempts the source again. Bumping it to "now" on failure
    would mean one outage bought the dead feed a quiet TTL.
    """
    fetcher._cache["x"] = feeds.Cached("stale", 0, 60)

    def boom():
        raise OSError("connection reset")

    assert fetcher.cached("x", 60, boom).stale is True
    assert fetcher.cached("x", 60, lambda: "back").value == "back"
    assert fetcher.cached("x", 60, boom).stale is False  # fresh again, cached


def test_our_own_parser_bug_propagates(fetcher):
    """The whole reason `cached` was narrowed.

    The old one caught everything, so a KeyError from our Yahoo parser was
    indistinguishable from Yahoo being down — and the panel then served
    week-old prices forever with no error anywhere.
    """
    fetcher.cached("x", 60, lambda: "stale")
    fetcher._cache["x"] = feeds.Cached("stale", 0, 60)

    def bug():
        raise KeyError("regularMarketPrice")

    with pytest.raises(KeyError):
        fetcher.cached("x", 60, bug)


def test_a_transport_failure_with_nothing_cached_still_raises(fetcher):
    def boom():
        raise OSError("connection reset")

    with pytest.raises(OSError):
        fetcher.cached("x", 60, boom)


def test_invalidate_clears_a_prefixed_family(fetcher):
    fetcher.cached("calendar:2026-07-26", 60, lambda: "a")
    fetcher.cached("calendar:2026-07-27", 60, lambda: "b")
    fetcher.cached("news", 60, lambda: "c")
    fetcher.invalidate("calendar")
    assert set(fetcher._cache) == {"news"}


# --- markets ---------------------------------------------------------------


def yahoo(closes, meta):
    return json.dumps(
        {"chart": {"result": [{"indicators": {"quote": [{"close": closes}]}, "meta": meta}]}}
    )


def test_a_normal_quote_computes_the_change(fetcher, monkeypatch):
    monkeypatch.setattr(
        feeds,
        "http_get",
        lambda *a, **k: yahoo(
            [100.0, 110.0], {"regularMarketPrice": 110.0, "regularMarketPreviousClose": 100.0}
        ),
    )
    row = fetcher._yahoo_row("AAPL", "AAPL", "query1")
    assert row.price == 110.0
    assert row.change_pct == 10.0


def test_a_single_close_no_longer_raises_indexerror(fetcher, monkeypatch):
    """The old code read closes[-2] unguarded; a newly-listed ticker with one
    close crashed the whole panel."""
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: yahoo([101.0], {}))
    row = fetcher._yahoo_row("NEW", "NEW", "query1")
    assert row.price == 101.0
    assert row.change_pct is None
    assert row.error is None


def test_no_price_at_all_is_a_feed_error(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: yahoo([], {}))
    with pytest.raises(FeedError, match="no price"):
        fetcher._yahoo_row("DEAD", "DEAD", "query1")


def test_nulls_in_the_close_series_are_dropped(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: yahoo([100.0, None, 120.0], {}))
    row = fetcher._yahoo_row("X", "X", "query1")
    assert row.spark == [100.0, 120.0]
    assert row.change_pct == 20.0


def test_both_hosts_errors_are_reported_not_shadowed(fetcher, monkeypatch):
    """The old code kept only the last attempt's error, so the reported
    failure was frequently not the one that mattered."""
    seen = []

    def failing(url, *a, **k):
        host = url.split("://")[1].split(".")[0]
        seen.append(host)
        raise OSError(f"{host} refused")

    monkeypatch.setattr(feeds, "http_get", failing)
    monkeypatch.setattr(fetcher.settings.markets, "tickers", ["AAPL"])
    with pytest.raises(FeedError):
        fetcher._fetch_markets(["AAPL"])
    assert seen == ["query1", "query2"]


def test_one_dead_ticker_does_not_kill_the_panel(fetcher, monkeypatch):
    def half(url, *a, **k):
        if "AAPL" in url:
            raise OSError("nope")
        return yahoo([100.0, 110.0], {})

    monkeypatch.setattr(feeds, "http_get", half)
    monkeypatch.setattr(fetcher.settings.markets, "tickers", ["AAPL", "MSFT"])
    rows = fetcher._fetch_markets(["AAPL", "MSFT"])
    assert rows[0].error and "query1" in rows[0].error and "query2" in rows[0].error
    assert rows[1].price == 110.0


# --- gcalcli ---------------------------------------------------------------


def test_gcalcli_receives_an_argv_list_not_a_shell_string(fetcher, monkeypatch):
    """Was a hand-quoted string through `bash -lc`, so every argument had to
    survive POSIX quoting by hand and every call re-sourced the login profile."""
    captured = {}

    def spy(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", spy)
    fetcher.gcalcli("quick", "Dinner with O'Brien; rm -rf /")

    assert isinstance(captured["argv"], list)
    assert "bash" not in captured["argv"]
    assert "-lc" not in captured["argv"]
    # The apostrophe and the semicolon reach gcalcli untouched, because
    # nothing between here and it is a shell.
    assert captured["argv"][-1] == "Dinner with O'Brien; rm -rf /"


def test_a_nonzero_exit_becomes_a_feed_error(fetcher, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "auth failed"),
    )
    with pytest.raises(FeedError, match="auth failed"):
        fetcher.gcalcli("agenda")


def test_a_missing_binary_is_a_feed_error_not_a_traceback(fetcher, monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(FeedError, match="not found on PATH"):
        fetcher.gcalcli("agenda")


def test_a_timeout_is_a_feed_error(fetcher, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("gcalcli", 60)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(FeedError, match="timed out"):
        fetcher.gcalcli("agenda")


# --- agenda parsing --------------------------------------------------------

TSV = (
    "start_date\tstart_time\tend_date\tend_time\ttitle\n"
    "2026-07-26\t10:00\t2026-07-26\t11:00\tStandup\n"
    "2026-07-27\t14:30\t2026-07-27\t15:00\tDentist\n"
)


def test_the_header_row_is_skipped():
    events = feeds._parse_agenda(TSV)
    assert [e.title for e in events] == ["Standup", "Dentist"]
    assert events[0].date == TODAY
    assert events[0].start == "10:00"


def test_a_malformed_row_is_skipped_not_fatal():
    events = feeds._parse_agenda(TSV + "garbage\n" + "notadate\ta\tb\tc\td\n")
    assert len(events) == 2


def test_an_empty_agenda_is_an_empty_list():
    assert feeds._parse_agenda("") == []


# --- news ------------------------------------------------------------------

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>First</title><link>https://example.com/1</link></item>
<item><title>Second</title><link>https://www.ft.com/2</link></item>
</channel></rss>"""


def test_a_paywalled_domain_is_proxied(fetcher):
    proxied = fetcher.proxify("https://www.ft.com/content/x")
    assert proxied.startswith(fetcher.settings.news.proxy_prefix)


def test_a_free_domain_is_left_alone(fetcher):
    assert fetcher.proxify("https://arstechnica.com/x") == "https://arstechnica.com/x"


def test_every_feed_failing_is_a_feed_error(fetcher, monkeypatch):
    def boom(*a, **k):
        raise OSError("dns")

    monkeypatch.setattr(feeds, "http_get", boom)
    with pytest.raises(FeedError, match="all news feeds failed"):
        fetcher._fetch_news()


def test_quotas_bound_each_category(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: RSS)
    picks = feeds.apply_quotas(fetcher._fetch_news(), fetcher.settings.news.quotas)
    counts = {}
    for item in picks:
        counts[item.cat] = counts.get(item.cat, 0) + 1
    for quota in fetcher.settings.news.quotas:
        assert counts.get(quota.cat, 0) <= quota.n


def _distinct_per_feed(url, *a, **k):
    """Each feed returns four stories of its own, so the pool is bigger than
    the quotas can take. The shared RSS fixture cannot show this: every feed
    serving the *same* two links is exactly what the in-fetch dedupe
    collapses, which leaves nothing for the quotas to trim."""
    host = url.split("/")[2]
    items = "".join(
        f"<item><title>{host} {n}</title><link>https://{host}/story-{n}</link></item>"
        for n in range(4)
    )
    return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'


def test_the_fetch_no_longer_applies_the_quotas_itself(fetcher, monkeypatch):
    """They moved downstream of the seen-link filter. Quota-slicing first and
    filtering second is what would leave a category short."""
    monkeypatch.setattr(feeds, "http_get", _distinct_per_feed)
    pool = fetcher._fetch_news()
    picks = feeds.apply_quotas(pool, fetcher.settings.news.quotas)
    assert len(pool) > len(picks)
    assert len(picks) == sum(q.n for q in fetcher.settings.news.quotas)


DUPLICATED = """<?xml version="1.0"?><rss><channel>
<item><title>A story</title><link>https://example.com/a?utm_source=rss</link></item>
<item><title>The same story, retitled</title><link>https://example.com/a#top</link></item>
</channel></rss>"""


def test_a_repeated_link_in_one_fetch_appears_once(fetcher, monkeypatch):
    """Same URL, different headline: a desk re-titling a live story must not
    put it on screen twice at once."""
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: DUPLICATED)
    items = fetcher._fetch_news()
    keys = [i.key for i in items]
    assert len(keys) == len(set(keys))
    assert all(i.title == "A story" for i in items)  # the first one wins


# --- link identity ---------------------------------------------------------


def test_tracking_parameters_are_not_part_of_a_story_identity():
    a = feeds.normalize_link("https://ex.com/a?utm_source=rss&utm_medium=feed&id=7")
    b = feeds.normalize_link("https://ex.com/a?id=7&fbclid=xyz")
    assert a == b == "https://ex.com/a?id=7"


def test_a_meaningful_query_survives():
    """Dropping the whole query would collapse a publication into one key."""
    assert feeds.normalize_link("https://ex.com/story?id=7") != feeds.normalize_link(
        "https://ex.com/story?id=8"
    )


def test_the_fragment_and_host_case_are_not_identity():
    assert feeds.normalize_link("HTTPS://EX.com/a#section-2") == "https://ex.com/a"


def test_the_path_keeps_its_case():
    """Hosts are case-insensitive per RFC 3986; paths are not."""
    assert feeds.normalize_link("https://ex.com/A") != feeds.normalize_link("https://ex.com/a")


def test_a_non_url_is_returned_unchanged_rather_than_raising():
    assert feeds.normalize_link("  not a url  ") == "not a url"


# --- arXiv -----------------------------------------------------------------

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Old  paper</title><id>http://arxiv.org/abs/1</id>
<summary>Old   summary</summary><published>2020-01-01T00:00:00Z</published></entry>
<entry><title>Fresh paper</title><id>http://arxiv.org/abs/2</id>
<summary>Fresh summary</summary><published>2026-07-26T00:00:00Z</published></entry>
</feed>"""


def test_a_fresh_paper_wins_over_an_older_one(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: ATOM)
    papers = fetcher._fetch_papers(TODAY)
    assert papers[0].title == "Fresh paper"
    assert papers[0].fresh is True


def test_every_candidate_is_returned_not_just_the_pick(fetcher, monkeypatch):
    """The caller skips papers it has already surfaced, so it needs something
    to fall through to. Returning one entry is what let a quiet day on arXiv
    re-serve yesterday's paper."""
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: ATOM)
    assert [p.title for p in fetcher._fetch_papers(TODAY)] == ["Fresh paper", "Old paper"]


def test_nothing_fresh_falls_back_to_the_newest(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: ATOM)
    papers = fetcher._fetch_papers(TODAY + dt.timedelta(days=30))
    assert papers[0].fresh is False
    assert papers[0].title == "Old paper"  # whitespace collapsed


def test_an_empty_arxiv_response_is_a_feed_error(fetcher, monkeypatch):
    monkeypatch.setattr(
        feeds, "http_get", lambda *a, **k: '<feed xmlns="http://www.w3.org/2005/Atom"/>'
    )
    with pytest.raises(FeedError, match="arXiv returned nothing"):
        fetcher._fetch_papers(TODAY)


# --- lichess ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "difficulty"),
    [(900, "easiest"), (1200, "easier"), (1500, "normal"), (1800, "harder"), (2100, "hardest")],
)
def test_difficulty_tracks_rating(rating, difficulty):
    assert feeds._difficulty(rating) == difficulty


def test_the_puzzle_keeps_only_what_survived_deleting_the_board(fetcher, monkeypatch):
    payload = json.dumps(
        {
            "puzzle": {
                "id": "abcd1",
                "rating": 1520,
                "themes": ["fork"],
                "solution": ["e2e4"],
                "initialPly": 12,
            },
            "game": {"pgn": "1. e4 e5"},
        }
    )
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: payload)
    puzzle = fetcher.chess_puzzle(1500)
    assert puzzle.id == "abcd1"
    assert puzzle.rating == 1520
    assert not hasattr(puzzle, "pgn")
    assert not hasattr(puzzle, "solution")
