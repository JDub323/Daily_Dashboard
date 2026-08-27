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


# --- what goes in and what comes out ---------------------------------------
#
# Every test in this section is really the same test asked six ways: *does the
# reading refuse to speak when the log was not being kept?* That is the whole
# risk in this half of the module. Load has the property that an unlogged
# session is simply a lighter week, which is wrong but not alarming; here an
# unlogged day looks exactly like constipation, dehydration or a fast, and a
# panel that cannot tell those apart is one that warns you about your Sundays
# until you stop reading it.


def visit(
    days_ago: int,
    hour: int,
    kind: str = "urine",
    color: str | None = "yellow",
    solidity: str | None = None,
    volume: str = "medium",
) -> d.BathroomLog:
    return d.BathroomLog(
        id=days_ago * 100 + hour,
        date=TODAY - dt.timedelta(days=days_ago),
        hour=hour,
        kind=kind,
        color=color if kind == "urine" else None,
        solidity=solidity if kind == "stool" else None,
        volume=volume,
    )


def day_of_urine(days_ago: int, *colors: str) -> list[d.BathroomLog]:
    """One day's voids, in order, starting at 07:00."""
    return [
        visit(days_ago, 7 + n, color=color) for n, color in enumerate(colors)
    ]


def meal(days_ago: int, hour: int = 12, size: str = "medium", quality: int = 3) -> d.MealLog:
    return d.MealLog(
        id=days_ago * 100 + hour,
        date=TODAY - dt.timedelta(days=days_ago),
        hour=hour,
        size=size,
        quality=quality,
    )


def days_of_meals(days: int, per_day: int = 3, size: str = "medium", quality: int = 3):
    return [
        meal(n, 8 + 4 * i, size, quality) for n in range(days) for i in range(per_day)
    ]


# -- hydration ---------------------------------------------------------------


def test_the_first_void_of_the_day_is_thrown_away():
    """Overnight urine is concentrated in everybody.

    The load-bearing test of this section: without the discard, a dark 07:00
    reading followed by two pale ones averages to a warning, and the panel
    would flag dehydration on a well-hydrated morning every day of the camp.
    """
    day = day_of_urine(0, "darker", "clear", "light")
    assert health.day_darkness(day) == 1.5


def test_a_day_with_too_few_voids_has_no_reading():
    assert health.day_darkness(day_of_urine(0, "darker", "darker")) is None
    assert health.day_darkness(day_of_urine(0, "darker", "darker", "darker")) == 4.0


def test_dark_urine_needs_a_run():
    logs = [v for n in range(2) for v in day_of_urine(n, "darker", "darker", "darker")]
    assert health.dark_urine_run(logs, TODAY) == 2


def test_a_pale_day_breaks_the_run():
    logs = [
        *day_of_urine(0, "darker", "darker", "darker"),
        *day_of_urine(1, "clear", "clear", "clear"),
        *day_of_urine(2, "darker", "darker", "darker"),
    ]
    assert health.dark_urine_run(logs, TODAY) == 1


def test_an_unlogged_day_breaks_the_run_rather_than_extending_it():
    """A gap is not evidence. Same rule as `readiness_dip`, and the reason is
    the same: a warning that grows while nobody is measuring is worse than no
    warning."""
    logs = [
        *day_of_urine(0, "darker", "darker", "darker"),
        # nothing on day 1
        *day_of_urine(2, "darker", "darker", "darker"),
    ]
    assert health.dark_urine_run(logs, TODAY) == 1


def test_hydration_mean_skips_absent_days_rather_than_zeroing_them():
    points = [(TODAY - dt.timedelta(days=n), 2000.0) for n in (0, 2, 4)]
    assert health.hydration_mean(points, TODAY) == 2000.0


def test_hydration_mean_is_silent_under_three_readings():
    points = [(TODAY, 2000.0), (TODAY - dt.timedelta(days=1), 1800.0)]
    assert health.hydration_mean(points, TODAY) is None


# -- the gut -----------------------------------------------------------------


def test_the_gut_readings_are_none_when_the_log_was_not_being_kept():
    logs = [visit(0, 8), visit(1, 8)]  # two days out of seven
    assert health.liquid_days(logs, TODAY) is None
    assert health.days_since_stool(logs, TODAY) is None


def test_liquid_days_counts_days_not_entries():
    logs = [visit(n, 8) for n in range(7)]
    logs += [
        visit(0, 9, kind="stool", solidity="liquid"),
        visit(0, 11, kind="stool", solidity="liquid"),
        visit(3, 9, kind="stool", solidity="liquid"),
    ]
    assert health.liquid_days(logs, TODAY) == 2


def test_days_since_stool_counts_from_today():
    logs = [visit(n, 8) for n in range(7)]
    logs.append(visit(2, 9, kind="stool", solidity="solid"))
    assert health.days_since_stool(logs, TODAY) == 2

    logs.append(visit(0, 9, kind="stool", solidity="solid"))
    assert health.days_since_stool(logs, TODAY) == 0


# -- food --------------------------------------------------------------------


def test_a_day_with_one_meal_is_not_a_logged_day():
    """The opposite convention from `daily_load`'s rest days, on purpose: a day
    with no session is a day you did not train, and a day with no food on it is
    a day you did not write down what you ate."""
    # Six full days behind us, and one lonely breakfast today.
    meals = [meal(0), *(m for m in days_of_meals(7) if m.date != TODAY)]
    intake = health.daily_intake(meals, TODAY)
    assert TODAY not in intake
    assert len(intake) == 6


def test_intake_is_the_buckets_added_up():
    meals = days_of_meals(7, per_day=3, size="medium")
    assert health.intake_mean(meals, TODAY) == pytest.approx(3 * health.MEAL_KCAL["medium"])


def test_intake_is_silent_under_five_logged_days():
    assert health.intake_mean(days_of_meals(4), TODAY) is None
    assert health.intake_mean(days_of_meals(5), TODAY) is not None


def test_diet_quality_is_a_mean_over_meals_not_over_days():
    """Two good dinners do not undo five days of bad lunches."""
    meals = [
        *days_of_meals(5, per_day=2, quality=1),
        *days_of_meals(2, per_day=1, quality=5),
    ]
    # 10 meals at 1 and 2 at 5, not a mean of daily means.
    assert health.diet_quality(meals, TODAY) == pytest.approx(20 / 12)


def test_implied_rate_is_the_balance_as_pounds():
    assert health.implied_lb_per_week(-500.0) == pytest.approx(-1.0)


# -- overnight scalars -------------------------------------------------------


def scalars(values: list[float]) -> list[tuple[dt.date, float]]:
    """Oldest-first, one a day, ending today."""
    return [
        (TODAY - dt.timedelta(days=len(values) - 1 - n), v) for n, v in enumerate(values)
    ]


def test_a_scalar_baseline_needs_a_fortnight():
    assert health.scalar_baseline(scalars([50.0] * 13), TODAY) is None
    # …and a flat series has no spread to measure a departure in.
    assert health.scalar_baseline(scalars([50.0] * 20), TODAY) is None


def test_hrv_dip_is_a_run_below_your_own_baseline():
    values = [50.0, 55.0, 45.0, 52.0, 48.0] * 4  # twenty days of ordinary noise
    values[-2:] = [20.0, 20.0]
    assert health.scalar_run(scalars(values), TODAY, above=False) == 2


def test_resting_hr_runs_the_other_way():
    values = [48.0, 52.0, 46.0, 50.0, 49.0] * 4
    values[-3:] = [70.0, 70.0, 70.0]
    assert health.scalar_run(scalars(values), TODAY, above=True) == 3
    # The same series read as a dip is not a run at all.
    assert health.scalar_run(scalars(values), TODAY, above=False) == 0


def test_a_missing_morning_breaks_a_scalar_run():
    values = [48.0, 52.0, 46.0, 50.0, 49.0] * 4
    values[-3:] = [70.0, 70.0, 70.0]
    points = [p for p in scalars(values) if p[0] != TODAY - dt.timedelta(days=1)]
    assert health.scalar_run(points, TODAY, above=True) == 1
