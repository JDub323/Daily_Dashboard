"""Reading `metrics.jsonl` back.

The log has been written since the first generation and read by nothing, which
means it has accumulated every schema the app has ever had with no reader to
notice. Most of what is tested here is tolerance: the parser has to survive
lines it was not designed for, and has to say so rather than quietly totalling
less than was actually spent.
"""

from __future__ import annotations

import datetime as dt
import json

from dashboard import spend

TODAY = dt.date(2026, 8, 3)


def write(path, *records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def line(date, kind="word", usd=0.01, **extra):
    return {"ts": f"{date}T09:00:00", "date": date, "kind": kind, "est_cost_usd": usd, **extra}


def summarise(path, served=0):
    entries, bad = spend.load(path)
    return spend.summarise(entries, bad, TODAY, served)


# --- reading the log -------------------------------------------------------


def test_a_missing_log_is_nothing_spent_not_an_error(tmp_path):
    """The normal state of a fresh checkout."""
    entries, bad = spend.load(tmp_path / "nope.jsonl")
    assert (entries, bad) == ([], 0)


def test_a_line_with_no_kind_is_the_math_generation_it_was(tmp_path):
    """`kind` was added in the change that started logging the *other* four
    generations, so a line without it is by construction a math problem."""
    path = write(tmp_path / "m.jsonl", {"date": "2026-08-01", "est_cost_usd": 0.15})
    entries, bad = spend.load(path)
    assert bad == 0
    assert entries[0].kind == "math"


def test_the_real_legacy_line_still_parses(tmp_path):
    """Copied from the top of the actual metrics.jsonl: flat token fields, no
    `kind`, no nested `metrics`."""
    legacy = {
        "ts": "2026-07-25T16:56:39",
        "date": "2026-07-25",
        "model": "claude-sonnet-5",
        "topic": "generating functions",
        "rating": 1528,
        "wall_seconds": 697.0,
        "ok": True,
        "error": None,
        "source": "IMO 1987 Problem 1",
        "tier": "olympiad-easy",
        "input_tokens": 6456,
        "output_tokens": 10259,
        "web_searches": 4,
        "est_cost_usd": 0.1555,
    }
    entries, bad = spend.load(write(tmp_path / "m.jsonl", legacy))
    assert bad == 0
    assert entries == [spend.Entry(dt.date(2026, 7, 25), "math", 0.1555)]


def test_unreadable_lines_are_counted_not_swallowed(tmp_path):
    """A partial read is not a wrong total, but it is not a complete one, and
    the strip has to be able to say which it is showing."""
    path = tmp_path / "m.jsonl"
    path.write_text(
        json.dumps(line("2026-08-03"))
        + "\nnot json at all\n"
        + json.dumps({"no": "date"})
        + "\n"
        + json.dumps(line("2026-08-03"))
        + "\n",
        encoding="utf-8",
    )
    entries, bad = spend.load(path)
    assert len(entries) == 2
    assert bad == 2


def test_blank_lines_are_not_failures(tmp_path):
    path = tmp_path / "m.jsonl"
    path.write_text(f"\n{json.dumps(line('2026-08-03'))}\n\n", encoding="utf-8")
    entries, bad = spend.load(path)
    assert (len(entries), bad) == (1, 0)


def test_a_call_logged_without_a_cost_counts_as_a_free_call(tmp_path):
    """It happened. Dropping it would make the call count disagree with the
    log, which is the one thing the log is for."""
    path = write(tmp_path / "m.jsonl", {"date": "2026-08-03", "kind": "word"})
    entries, bad = spend.load(path)
    assert bad == 0
    assert entries[0].usd == 0.0


def test_only_the_tail_is_parsed(tmp_path):
    path = tmp_path / "m.jsonl"
    path.write_text(
        "\n".join(json.dumps(line("2026-08-03")) for _ in range(spend.MAX_LINES + 50)) + "\n",
        encoding="utf-8",
    )
    entries, _ = spend.load(path)
    assert len(entries) == spend.MAX_LINES


# --- the windows -----------------------------------------------------------


def test_the_windows_are_inclusive_of_today_and_of_their_first_day(tmp_path):
    path = write(
        tmp_path / "m.jsonl",
        line("2026-08-03", usd=1.0),  # today
        line("2026-07-28", usd=2.0),  # 6 days back — inside 7d
        line("2026-07-27", usd=4.0),  # 7 days back — outside 7d, inside 30d
        line("2026-07-05", usd=8.0),  # 29 days back — the first day of 30d
        line("2026-07-04", usd=16.0),  # one day further — outside every window
    )
    today, week, month = summarise(path).windows
    assert (today.days, today.usd) == (1, 1.0)
    assert (week.days, week.usd) == (7, 3.0)
    assert (month.days, month.usd) == (30, 15.0)


def test_a_future_dated_line_is_not_counted_as_today(tmp_path):
    """A clock that moved backwards leaves records dated after today. They are
    outside every window, which is the only defensible reading."""
    path = write(tmp_path / "m.jsonl", line("2026-08-09", usd=5.0))
    assert [w.usd for w in summarise(path).windows] == [0.0, 0.0, 0.0]


def test_the_breakdown_is_dearest_first(tmp_path):
    path = write(
        tmp_path / "m.jsonl",
        line("2026-08-03", kind="word", usd=0.01),
        line("2026-08-03", kind="math", usd=0.40),
        line("2026-08-03", kind="theorem", usd=0.05),
    )
    today = summarise(path).windows[0]
    assert [b.kind for b in today.by_kind] == ["math", "theorem", "word"]
    assert today.by_kind[0].calls == 1


def test_calls_of_one_kind_are_summed_and_counted(tmp_path):
    path = write(
        tmp_path / "m.jsonl",
        line("2026-08-03", kind="theorem", usd=0.01),
        line("2026-08-02", kind="theorem", usd=0.02),
    )
    (bucket,) = summarise(path).windows[1].by_kind
    assert (bucket.kind, bucket.calls, bucket.usd) == ("theorem", 2, 0.03)


def test_an_empty_log_still_reports_three_windows(tmp_path):
    """The strip renders on day one, before anything has been generated."""
    result = summarise(write(tmp_path / "m.jsonl"))
    assert [w.label for w in result.windows] == ["today", "7d", "30d"]
    assert result.tracking_since is None


# --- did the batch do its job ----------------------------------------------


def test_the_free_problems_are_the_gap_between_two_sources(tmp_path):
    """Problems served comes from `math_attempt`, problems paid from this log.
    A batch-served problem is in the first and not the second, so the
    difference is what the batch and the pool covered."""
    path = write(
        tmp_path / "m.jsonl",
        line("2026-08-01", kind="math", usd=0.20),
        line("2026-07-30", kind="word", usd=0.01),
    )
    result = summarise(path, served=7)
    assert result.problems_served == 7
    assert result.problems_paid == 1
    assert result.last_live_generation == dt.date(2026, 8, 1)


def test_a_month_of_free_problems_reports_none_paid(tmp_path):
    result = summarise(write(tmp_path / "m.jsonl", line("2026-08-03", kind="word")), served=30)
    assert result.problems_paid == 0
    assert result.last_live_generation is None


def test_a_live_generation_outside_the_window_is_not_the_last_one(tmp_path):
    """`last_live_generation` reads as "you last paid on this date". A date
    from outside the reporting window would be an answer to a question the
    strip is not asking."""
    path = write(tmp_path / "m.jsonl", line("2026-06-01", kind="math", usd=0.3))
    assert summarise(path).last_live_generation is None
