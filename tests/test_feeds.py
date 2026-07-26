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

    assert fetcher.cached("x", 60, fn) == "value"
    assert fetcher.cached("x", 60, fn) == "value"
    assert len(calls) == 1


def test_an_expired_entry_is_refetched(fetcher):
    fetcher.cached("x", 60, lambda: "old")
    fetcher._cache["x"] = (0, "old")
    assert fetcher.cached("x", 60, lambda: "new") == "new"


@pytest.mark.parametrize(
    "error",
    [OSError("connection reset"), TimeoutError("timed out"), FeedError("all feeds failed")],
)
def test_a_transport_failure_serves_stale(fetcher, error):
    fetcher.cached("x", 60, lambda: "stale")
    fetcher._cache["x"] = (0, "stale")

    def boom():
        raise error

    assert fetcher.cached("x", 60, boom) == "stale"


def test_our_own_parser_bug_propagates(fetcher):
    """The whole reason `cached` was narrowed.

    The old one caught everything, so a KeyError from our Yahoo parser was
    indistinguishable from Yahoo being down — and the panel then served
    week-old prices forever with no error anywhere.
    """
    fetcher.cached("x", 60, lambda: "stale")
    fetcher._cache["x"] = (0, "stale")

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
        fetcher._fetch_markets()
    assert seen == ["query1", "query2"]


def test_one_dead_ticker_does_not_kill_the_panel(fetcher, monkeypatch):
    def half(url, *a, **k):
        if "AAPL" in url:
            raise OSError("nope")
        return yahoo([100.0, 110.0], {})

    monkeypatch.setattr(feeds, "http_get", half)
    monkeypatch.setattr(fetcher.settings.markets, "tickers", ["AAPL", "MSFT"])
    rows = fetcher._fetch_markets()
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
    picks = fetcher._fetch_news()
    counts = {}
    for item in picks:
        counts[item.cat] = counts.get(item.cat, 0) + 1
    for quota in fetcher.settings.news.quotas:
        assert counts.get(quota.cat, 0) <= quota.n


# --- arXiv -----------------------------------------------------------------

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Old  paper</title><id>http://arxiv.org/abs/1</id>
<summary>Old   summary</summary><published>2020-01-01T00:00:00Z</published></entry>
<entry><title>Fresh paper</title><id>http://arxiv.org/abs/2</id>
<summary>Fresh summary</summary><published>2026-07-26T00:00:00Z</published></entry>
</feed>"""


def test_a_fresh_paper_wins_over_an_older_one(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: ATOM)
    entry = fetcher._fetch_paper(TODAY)
    assert entry.title == "Fresh paper"
    assert entry.fresh is True


def test_nothing_fresh_falls_back_to_the_newest(fetcher, monkeypatch):
    monkeypatch.setattr(feeds, "http_get", lambda *a, **k: ATOM)
    entry = fetcher._fetch_paper(TODAY + dt.timedelta(days=30))
    assert entry.fresh is False
    assert entry.title == "Old paper"  # whitespace collapsed


def test_an_empty_arxiv_response_is_a_feed_error(fetcher, monkeypatch):
    monkeypatch.setattr(
        feeds, "http_get", lambda *a, **k: '<feed xmlns="http://www.w3.org/2005/Atom"/>'
    )
    with pytest.raises(FeedError, match="arXiv returned nothing"):
        fetcher._fetch_paper(TODAY)


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
