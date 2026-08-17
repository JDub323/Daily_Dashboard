"""The training-load math.

Two things are worth pinning down here, and they are not the arithmetic. The
first is *when each number refuses to answer* -- every threshold in
`dashboard/health.py` exists because the number below it is actively
misleading, and a silently-relaxed gate would put a wild ratio on the panel in
week one. The second is the clipped chronic window, which is the one place the
obvious implementation is wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import health
from dashboard.models import domain as d
from dashboard.models.common import SessionKind

from .conftest import TODAY


def session(days_ago: int, rpe: int = 5, minutes: int = 60) -> d.TrainingSession:
    return d.TrainingSession(
        id=days_ago + 1,
        date=TODAY - dt.timedelta(days=days_ago),
        kind=SessionKind.boxing,
        rpe=rpe,
        minutes=minutes,
    )


def daily(days: int, rpe: int = 5, minutes: int = 60) -> list[d.TrainingSession]:
    """One identical session a day for the last `days` days."""
    return [session(n, rpe, minutes) for n in range(days)]


# --- load ------------------------------------------------------------------


def test_load_is_rpe_times_minutes():
    assert health.srpe(8, 45) == 360
    assert session(0, rpe=8, minutes=45).load == 360


def test_two_sessions_on_a_day_sum():
    sessions = [session(0, 4, 30), session(0, 9, 45)]
    loads = health.daily_load(sessions, TODAY, TODAY)
    assert loads[TODAY] == 525


def test_rest_days_are_present_as_zero():
    """Not padding: monotony is a spread measurement, and dropping empty days
    would make every week look varied."""
    loads = health.daily_load([session(0)], TODAY - dt.timedelta(days=6), TODAY)
    assert len(loads) == 7
    assert sum(1 for v in loads.values() if v == 0) == 6


def test_sessions_outside_the_window_are_ignored():
    loads = health.daily_load([session(40)], TODAY - dt.timedelta(days=6), TODAY)
    assert sum(loads.values()) == 0


# --- acwr ------------------------------------------------------------------


def test_acwr_says_nothing_before_two_weeks():
    assert health.acwr(daily(13), TODAY) is None
    assert health.acwr(daily(14), TODAY) is not None


def test_acwr_is_one_when_this_week_matches_the_norm():
    assert health.acwr(daily(28), TODAY) == pytest.approx(1.0)


def test_acwr_reports_a_spike():
    """Four easy weeks, then a hard one."""
    sessions = [session(n, rpe=3) for n in range(7, 28)] + [session(n, rpe=9) for n in range(7)]
    assert health.acwr(sessions, TODAY) > 1.5


def test_acwr_reports_a_deload():
    sessions = [session(n, rpe=8) for n in range(7, 28)] + [session(n, rpe=2) for n in range(7)]
    assert health.acwr(sessions, TODAY) < 0.8


def test_the_chronic_window_is_clipped_to_the_history_that_exists():
    """The trap this function exists to avoid.

    Three weeks of identical training is, by definition, a completely normal
    week — the ratio is 1.0. A fixed 28-day denominator would divide by a mean
    that includes seven days of zeros from before logging began, inventing a
    spike out of the calendar alone.
    """
    assert health.acwr(daily(21), TODAY) == pytest.approx(1.0)


def test_acwr_is_absent_when_nothing_was_logged_in_the_window():
    """History is long enough, but every day of it is a rest day now."""
    assert health.acwr([session(60)], TODAY) is None


# --- monotony --------------------------------------------------------------


def test_monotony_says_nothing_before_a_full_week():
    """A window running off the start of history fills with zeros that are
    absence, not rest — and those zeros make monotony look better.

    Both weeks below rest today, so both have a real spread; the only thing
    separating them is whether the seven-day window fits inside the history.
    """
    six = [session(n) for n in range(1, 6)]  # oldest five days ago
    seven = [session(n) for n in range(1, 7)]  # oldest six days ago
    assert health.monotony(six, TODAY) is None
    assert health.monotony(seven, TODAY) is not None


def test_seven_identical_days_have_no_spread_so_no_ratio():
    assert health.monotony(daily(7), TODAY) is None


def test_a_flat_week_scores_higher_than_a_varied_one():
    """Six identical days beats hard-easy-hard, and the two weeks below carry
    almost exactly the same total load — which is the point. Monotony catches
    the training error that volume cannot see."""
    older = [session(n, rpe=5) for n in range(7, 14)]
    flat = [*[session(n, rpe=5) for n in range(1, 7)], *older]
    varied = [
        session(1, rpe=9),
        session(2, rpe=2),
        session(3, rpe=9),
        session(5, rpe=2),
        session(6, rpe=9),
        *older,
    ]
    assert health.monotony(flat, TODAY) > health.monotony(varied, TODAY)


def test_a_week_of_pure_rest_is_absent_rather_than_alarming():
    sessions = [session(n) for n in range(7, 21)]
    assert health.monotony(sessions, TODAY) is None


def test_strain_follows_monotony():
    assert health.strain(daily(6), TODAY) is None
    sessions = daily(14, rpe=5)
    sessions = [s for s in sessions if s.date != TODAY]  # one rest day for spread
    assert health.strain(sessions, TODAY) > 0


# --- weight ----------------------------------------------------------------


def points(values: list[float]) -> list[tuple[dt.date, float]]:
    n = len(values)
    return [(TODAY - dt.timedelta(days=n - 1 - i), v) for i, v in enumerate(values)]


def test_weight_says_nothing_under_ten_readings():
    assert health.weight_trend(points([172.0] * 9)) is None
    assert health.weight_trend(points([172.0] * 10)) is not None


def test_a_flat_weight_has_no_rate():
    ema, per_week = health.weight_trend(points([172.0] * 20))
    assert ema == pytest.approx(172.0)
    assert per_week == pytest.approx(0.0)


def test_a_steady_cut_is_reported_as_pounds_per_week():
    """Down a pound every four days — a touch under 1.8 lb/week."""
    values = [180.0 - 0.25 * n for n in range(28)]
    _, per_week = health.weight_trend(points(values))
    assert per_week == pytest.approx(-1.75)


def test_the_ema_lags_behind_a_single_bad_morning():
    """One salty dinner should not move the trend line much."""
    ema, _ = health.weight_trend(points([172.0] * 19 + [178.0]))
    assert 172.0 < ema < 174.5


def test_the_rate_reads_the_last_four_weeks_not_the_whole_history():
    """Held steady for months, then started cutting three weeks ago."""
    values = [180.0] * 60 + [180.0 - 0.3 * n for n in range(1, 22)]
    _, per_week = health.weight_trend(points(values))
    assert per_week < -1.0


# --- readiness -------------------------------------------------------------


def readiness(days_ago: int, score_each: int) -> d.Readiness:
    return d.Readiness(
        date=TODAY - dt.timedelta(days=days_ago),
        sleep_quality=score_each,
        fatigue=score_each,
        soreness=score_each,
        stress=score_each,
        motivation=score_each,
    )


def test_the_score_is_the_sum_of_five_items():
    assert readiness(0, 4).score == 20
    entry = d.Readiness(
        date=TODAY, sleep_quality=5, fatigue=1, soreness=3, stress=2, motivation=4
    )
    assert entry.score == 15


def test_the_baseline_needs_two_weeks_of_answers():
    assert health.readiness_baseline([readiness(n, 3 + n % 2) for n in range(13)], TODAY) is None
    assert (
        health.readiness_baseline([readiness(n, 3 + n % 2) for n in range(14)], TODAY) is not None
    )


def test_a_baseline_with_no_spread_is_absent():
    """Every morning answered identically tells you nothing about a dip."""
    assert health.readiness_baseline([readiness(n, 4) for n in range(20)], TODAY) is None


def test_the_baseline_ignores_answers_older_than_thirty_days():
    entries = [readiness(n, 5) for n in range(40, 60)] + [
        readiness(n, 3 + n % 2) for n in range(20)
    ]
    mean, _ = health.readiness_baseline(entries, TODAY)
    assert mean < 20


def test_a_mean_over_no_answers_is_absent_rather_than_zero():
    assert health.readiness_mean([readiness(30, 4)], TODAY, 7) is None


def test_a_dip_is_counted_in_consecutive_days():
    entries = [readiness(n, 4 if n % 2 else 3) for n in range(3, 30)]
    entries += [readiness(0, 1), readiness(1, 1), readiness(2, 1)]
    assert health.readiness_dip(entries, TODAY) == 3


def test_one_bad_morning_is_not_a_dip():
    entries = [readiness(n, 4 if n % 2 else 3) for n in range(1, 30)] + [readiness(0, 1)]
    assert health.readiness_dip(entries, TODAY) == 1


def test_a_skipped_morning_breaks_the_run_rather_than_extending_it():
    """A day nobody measured is not a day you were flat."""
    entries = [readiness(n, 4 if n % 2 else 3) for n in range(2, 30)]
    entries += [readiness(0, 1)]  # yesterday unanswered
    assert health.readiness_dip(entries, TODAY) == 1


def test_no_baseline_means_no_dip():
    assert health.readiness_dip([readiness(0, 1)], TODAY) == 0
