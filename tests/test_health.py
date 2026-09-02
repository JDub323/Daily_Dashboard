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
from dashboard.models.common import InputState, SessionKind

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


# -- what you drank ----------------------------------------------------------


def drink(
    days_ago: int, hour: int, tags: list[str] | None = None, amount: float = 1.0
) -> d.DrinkLog:
    """One drink. No tags is the default because no tags is a glass of water,
    which is what most of these fixtures want to be."""
    return d.DrinkLog(
        id=days_ago * 100 + hour,
        date=TODAY - dt.timedelta(days=days_ago),
        hour=hour,
        tags=tags or [],
        amount=amount,
    )


def days_of_drinks(
    days: int, per_day: int = 6, tags: list[str] | None = None, amount: float = 1.0
):
    return [drink(n, 8 + i, tags, amount) for n in range(days) for i in range(per_day)]


def test_a_drink_is_twelve_ounces_of_it():
    assert health.day_fluid_ml([drink(0, 9, amount=2.0)]) == pytest.approx(710.0)


def test_alcohol_is_not_fluid():
    """The one tag whose `amount` is not a volume at all -- a shot and a beer
    are both 1 -- so there is no honest number to add to the day's total."""
    day = [drink(0, 20, ["alcohol"], 3.0), drink(0, 9, amount=1.0)]
    assert health.day_fluid_ml(day) == pytest.approx(health.DRINK_ML)


def test_alcohol_takes_the_whole_row_out_and_not_its_share():
    """A rum and coke is `alcohol` and `sugar`, and the mixer is not a volume
    this log can separate out -- so the row counts for nothing rather than for
    the part of it that was coke."""
    assert health.day_fluid_ml([drink(0, 20, ["alcohol", "sugar"], 2.0)]) == 0


def test_coffee_counts_as_fluid_however_it_was_taken():
    plain = health.day_fluid_ml([drink(0, 7, ["coffee"], 1.0)])
    sweet = health.day_fluid_ml([drink(0, 7, ["sugar", "coffee"], 1.0)])
    assert plain == sweet == pytest.approx(health.DRINK_ML)


def test_milk_counts_as_fluid():
    assert health.day_fluid_ml([drink(0, 7, ["milk"], 1.0)]) == pytest.approx(health.DRINK_ML)


def test_fluid_is_silent_until_the_log_was_being_kept():
    """Four logged days out of seven is not a week's hydration, it is four
    days you remembered. The same gate the gut read runs on."""
    assert health.fluid_mean(days_of_drinks(4), TODAY) is None
    assert health.fluid_mean(days_of_drinks(5), TODAY) is not None


def test_a_day_of_nothing_but_beer_counts_as_a_dry_day():
    """The opposite of what `daily_intake` does with meals, and deliberately.

    A day with rows on it but no water is a day you drank no water -- which is
    the day this reading exists to catch. Skipping it as "no data" would have
    the mean quietly report the days you did drink.
    """
    # Six ordinary days, and a seventh with two beers on it and nothing else.
    logs = [*days_of_drinks(6, per_day=6), drink(6, 21, ["alcohol"], 2.0)]
    assert health.drink_logged_days(logs, TODAY) == 7
    assert health.fluid_mean(logs, TODAY) == pytest.approx(6 * health.DRINK_ML * 6 / 7)


def test_caffeine_is_a_mean_over_the_days_you_logged():
    logs = [
        *days_of_drinks(7, per_day=1),
        *days_of_drinks(7, per_day=2, tags=["coffee"]),
    ]
    assert health.caffeine_mean(logs, TODAY) == pytest.approx(2.0)


def test_alcohol_is_a_week_total_and_not_a_mean():
    """Nobody counts alcohol per day, and a mean of 1.1 hides the Saturday
    that is the whole of it."""
    logs = [*days_of_drinks(7, per_day=3), drink(2, 21, ["alcohol"], 4.0)]
    assert health.alcohol_week(logs, TODAY) == pytest.approx(4.0)


def test_a_dry_week_reads_zero_rather_than_silence():
    """Only once the log was kept. Zero is a real answer and it is the answer
    most weeks; None is "nobody was writing it down", and the panel prints
    nothing for that."""
    assert health.alcohol_week(days_of_drinks(7), TODAY) == 0.0
    assert health.alcohol_week(days_of_drinks(3), TODAY) is None


def test_last_months_drinking_is_outside_the_week():
    logs = [*days_of_drinks(7, per_day=3), drink(20, 21, ["alcohol"], 10.0)]
    assert health.alcohol_week(logs, TODAY) == 0.0


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


# --- the rail --------------------------------------------------------------
#
# Four composites, on a panel whose first two passes refused to build any. The
# tests here are almost all about the refusal rather than the arithmetic,
# because the arithmetic is a mean and the refusal is the whole design:
#
# 1. **A score is the mean of what scored, and never fills a gap in.** An
#    absent input must not be a zero and must not be a neutral 50 -- both of
#    those are a number about the watch dressed up as a number about him.
# 2. **A hole says which kind of hole it is.** `no_watch_data` and `not_asked`
#    are the same absence on screen unless something insists otherwise, and
#    they are opposite facts: one is a charger night, one is a rest day.
# 3. **Nothing scores before its gate.** HRV without a fortnight behind it
#    reports `no_baseline`, not a number computed off four mornings.


def zone_input(score, key):
    return next(item for item in score.inputs if item.key == key)


def states(score):
    return {item.key: item.state for item in score.inputs}


# -- how a bar is folded together -------------------------------------------


def test_a_bar_is_the_mean_of_whatever_scored():
    """Equal weights, over the inputs that could speak. Two inputs at 80 and 40
    is 60 whether or not the other four had anything to say."""
    score = health.hydration_score(
        fluid=2600.0,  # exactly the base target, nothing sweated: 100
        sweat=None,
        visits=[],
        caffeine=0.0,  # nothing drunk: 100
        alcohol=None,
        today=TODAY,
    )
    assert score.value == 100.0
    assert states(score)["urine"] is InputState.not_logged
    assert states(score)["alcohol"] is InputState.not_logged


def test_a_bar_with_nothing_behind_it_is_none_and_never_zero():
    """The distinction the whole rail rests on. An empty bar and a bar at zero
    are the same picture, and they are opposite facts -- one is a watch that
    said nothing and one is a week that went badly."""
    score = health.hydration_score(None, None, [], None, None, TODAY)
    assert score.value is None
    assert all(item.value is None for item in score.inputs)


def test_a_finished_bar_says_nothing_about_itself():
    """`note` names what is missing, and goes unsaid when nothing is. A panel
    that annotates a complete answer is a panel you learn to skim."""
    complete = health.fuel_score(days_of_meals(7), balance=-400.0, today=TODAY)
    assert complete.note is None

    partial = health.fuel_score(days_of_meals(7), balance=None, today=TODAY)
    assert partial.note is not None
    assert "energy balance" in partial.note


# -- recovery ---------------------------------------------------------------


def test_recovery_scores_off_the_check_in_alone_in_week_one():
    """The state this panel will actually be in for a month.

    HRV and resting heart rate need a fortnight of their own before they mean
    anything, and until the watch has been worn that long they must report
    `no_baseline` rather than a number. The morning check-in has been kept for
    months, so it is what the bar stands on meanwhile -- which is the whole
    reason it is an input at all.
    """
    score = health.recovery_score(
        hrv=scalars([50.0] * 4),
        rhr=scalars([48.0] * 4),
        sleep_score=[],
        battery=[],
        stress=[],
        readiness=[readiness(0, 4)],
        today=TODAY,
    )
    assert states(score)["hrv"] is InputState.no_baseline
    assert states(score)["rhr"] is InputState.no_baseline
    # 20 of 25, x4.
    assert score.value == 80.0
    assert "not enough history yet" in score.note


def test_a_night_on_the_charger_is_not_the_same_absence_as_a_young_series():
    """[PART 5] The one distinction the deletions made load-bearing. With the
    laptop's boxes gone a missing reading is a hole rather than a hand entry
    that may still arrive, and blaming the watch for the calendar -- or the
    calendar for the watch -- is how a panel stops being read."""
    mature = scalars([50.0, 55.0, 45.0, 52.0, 48.0] * 4)
    charger = [point for point in mature if point[0] != TODAY]

    score = health.recovery_score(charger, charger, [], [], [], [], TODAY)
    assert states(score)["hrv"] is InputState.no_watch_data

    young = health.recovery_score(scalars([50.0] * 4), [], [], [], [], [], TODAY)
    assert states(young)["hrv"] is InputState.no_baseline


def test_stress_is_scored_upside_down_from_how_garmin_reports_it():
    """Garmin's scale counts up in the thing you do not want; every bar on this
    rail counts up in the thing you do. A stress of 20 is a calm day."""
    score = health.recovery_score([], [], [], [], scalars([20.0]), [], TODAY)
    assert zone_input(score, "stress").value == 80.0


def test_the_check_in_and_the_watch_are_allowed_to_disagree():
    """Both are inputs and neither corrects the other. A sleep score of 88
    beside a wretched check-in is the diagnostic -- see the KEEP note in the
    check-in's own questions -- and averaging them into one number without
    showing both would be the panel hiding its most interesting morning."""
    score = health.recovery_score(
        hrv=[],
        rhr=[],
        sleep_score=scalars([88.0]),
        battery=[],
        stress=[],
        readiness=[readiness(0, 1)],  # 5 of 25: the worst morning available
        today=TODAY,
    )
    assert zone_input(score, "sleep_score").value == 88.0
    assert zone_input(score, "checkin").value == 20.0
    assert score.value == pytest.approx(54.0)


def test_a_reading_is_printed_beside_every_normalised_bar():
    """The half a 0-100 throws away. A 34 you can't check is a 34 you either
    believe or ignore."""
    mature = scalars([50.0, 55.0, 45.0, 52.0, 48.0] * 4)
    score = health.recovery_score(mature, [], [], [], [], [], TODAY)
    assert "against a" in zone_input(score, "hrv").reading


# -- load -------------------------------------------------------------------


def test_an_unwritten_week_is_not_a_failure_to_train():
    """`not_asked`, not zero.

    Eleven of the camp's twenty-eight weeks have no block written for them, and
    scoring those at zero would have a quarter of the year read as three months
    of not training. A week that prescribed nothing is a week you cannot fail.
    """
    score = health.load_score(daily(30), plan_open=0, plan_total=0, jump=[], today=TODAY)
    assert states(score)["prescribed"] is InputState.not_asked
    assert zone_input(score, "prescribed").value is None


def test_the_prescription_scores_as_the_weeks_fraction_logged():
    """A week's blocks, not a day's — `state.health_panel` sums the last seven
    prescriptions before handing them over.

    Daily was the first version and it was wrong in a way the panel showed
    immediately: with the other three inputs still behind their history gates,
    the bar *was* this number, and at nine on a Tuesday it read 0 next to a
    week's load of 1356.
    """
    score = health.load_score(daily(30), plan_open=3, plan_total=12, jump=[], today=TODAY)
    assert zone_input(score, "prescribed").value == 75.0
    assert "9 of 12" in zone_input(score, "prescribed").reading


def test_the_vertical_jump_never_gates_the_load_bar():
    """It is measured every six weeks and the bar is read every morning. Below
    ten points it reports `no_baseline` and contributes nothing -- and the bar
    is a number regardless, which is the property being pinned."""
    thin = [(TODAY - dt.timedelta(days=42 * n), 28.0) for n in range(3)]
    score = health.load_score(daily(30), plan_open=0, plan_total=9, jump=thin, today=TODAY)
    assert states(score)["vert_jump"] is InputState.no_baseline
    assert score.value is not None


def test_a_workload_ratio_inside_the_band_is_a_hundred():
    """The band is a *range*, so both a spike and a deload are departures from
    it and an ordinary week is not 'only 80% right'."""
    assert health._band_score(1.0, 0.8, 1.3, 0.4, 1.8) == 100.0
    assert health._band_score(1.3, 0.8, 1.3, 0.4, 1.8) == 100.0
    assert health._band_score(1.8, 0.8, 1.3, 0.4, 1.8) == 0.0
    assert health._band_score(0.4, 0.8, 1.3, 0.4, 1.8) == 0.0


def test_load_says_nothing_at_all_in_week_one():
    """Every one of its inputs is behind a history gate except the plan, so a
    fortnight in the bar is the prescription and nothing else."""
    score = health.load_score(daily(3), plan_open=0, plan_total=9, jump=[], today=TODAY)
    assert states(score)["acwr"] is InputState.no_baseline
    assert states(score)["monotony"] is InputState.no_baseline
    assert score.value == 100.0


# -- fuel -------------------------------------------------------------------


def test_a_cut_inside_the_band_scores_full_and_a_surplus_does_not():
    """The one number on the rail with a target rather than a baseline, and the
    target is a judgement about *this* camp: a surplus is wrong, and so is a
    hole big enough to cost training."""
    meals = days_of_meals(7)
    assert health.fuel_score(meals, -400.0, TODAY).inputs[0].value == 100.0
    assert health.fuel_score(meals, -750.0, TODAY).inputs[0].value == 100.0
    assert health.fuel_score(meals, 750.0, TODAY).inputs[0].value == 0.0
    assert health.fuel_score(meals, -1500.0, TODAY).inputs[0].value == 0.0


def test_a_week_with_meals_missing_scores_as_a_week_with_meals_missing():
    """The reason this input exists. A balance computed off a log with holes in
    it does not read as incomplete, it reads as a light week, and
    `implied_lb_per_week` believes it -- so the completeness of the log is
    scored beside the conclusion drawn from it."""
    full = health.fuel_score(days_of_meals(7), -400.0, TODAY)
    thin = health.fuel_score(days_of_meals(7, per_day=1), -400.0, TODAY)
    assert zone_input(full, "meals").value == 100.0
    assert zone_input(thin, "meals").value == pytest.approx(33.3, abs=0.1)
    assert thin.value < full.value


# -- hydration --------------------------------------------------------------


def test_the_fluid_target_moves_with_what_was_sweated_out():
    """The whole argument for `sweat_loss_ml` replacing the watch's glyph.

    The drink log spent a paragraph refusing to invent a hydration target, on
    the grounds that a number you hit by logging teaches you to log. What
    answers that is a target that is not invented: two litres on a rest day and
    the same two litres after a hard spar are different days, and now they
    score differently without anybody having decided that they should.
    """
    rest = health.hydration_score(2600.0, None, [], None, None, TODAY)
    sparred = health.hydration_score(2600.0, 1300.0, [], None, None, TODAY)
    assert zone_input(rest, "fluid").value == 100.0
    assert zone_input(sparred, "fluid").value == pytest.approx(66.7, abs=0.1)
    assert "sweated" in zone_input(sparred, "fluid").reading


def test_urine_colour_is_scored_off_days_dense_enough_to_ask():
    """`day_darkness` throws the first void of each day away -- it is
    concentrated on everybody -- so a day with one reading contributes nothing
    rather than contributing a bad one."""
    one_each = [visit(n, 7) for n in range(7)]
    assert health._week_darkness(one_each, TODAY) is None

    dark = [log for n in range(7) for log in day_of_urine(n, "light", "darker", "darker")]
    assert health._week_darkness(dark, TODAY) == 4.0
    score = health.hydration_score(None, None, dark, None, None, TODAY)
    assert zone_input(score, "urine").value == 0.0


# -- the two slow trends ----------------------------------------------------


def test_vo2max_is_the_last_reading_on_file_not_todays():
    """The one number on this tab deliberately exempt from "today or nothing".
    It only updates after an outdoor run with heart rate, so today-or-nothing
    would blank it about twenty-eight days in thirty -- and the date comes back
    with it so the panel can say how old it is rather than implying it is
    fresh."""
    points = [(TODAY - dt.timedelta(days=9), 52.4)]
    latest, when, change = health.vo2max_trend(points, TODAY)
    assert latest == 52.4
    assert when == TODAY - dt.timedelta(days=9)
    assert change is None


def test_a_vo2max_change_needs_two_readings_far_enough_apart():
    """This estimate moves by fractions. A week-on-week difference is noise
    with a sign on it, and printing it as a direction would have the panel
    announce a trend every time it rained."""
    near = [(TODAY - dt.timedelta(days=n), 52.0 + n * 0.1) for n in (5, 0)]
    assert health.vo2max_trend(near, TODAY)[2] is None

    apart = [(TODAY - dt.timedelta(days=80), 50.0), (TODAY, 52.5)]
    assert health.vo2max_trend(apart, TODAY)[2] == pytest.approx(2.5)


def test_no_readings_at_all_is_none_rather_than_a_zero_trend():
    assert health.vo2max_trend([], TODAY) is None
