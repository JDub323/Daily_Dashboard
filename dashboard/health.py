"""What the training log adds up to.

Pure functions over lists: no database, no clock, no config. Everything here
takes `today` as an argument for the same reason the rest of the server does --
the browser owns "today" ([Invariant 1]) -- and every one of them is a
straightforward thing to test, which is the point of keeping them out of
`state.py`.

The load metric is **session-RPE**: rpe x minutes, summed over the day. It is
what combat sports use, and it is used here for a specific reason rather than a
traditional one -- the alternatives are all heart-rate derived, and wrist
optical heart rate under hand wraps and a glove, with the wrist flexing on
every punch, is the least trustworthy number the watch produces. A load model
built on it would be confidently wrong about exactly the sessions that matter
most.

The organising rule of this module is that **every function returns None until
it has enough history to mean something**. That is not defensiveness. A ratio
computed off four days is not a weak signal, it is a number that will move
violently for reasons that are purely arithmetic, and the cost of showing it is
that you train differently because of it. `None` renders as silence, and
silence is correct until roughly a month in.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .models import domain as d

ACUTE_DAYS = 7
"""The "recent" window: what you did this week."""

CHRONIC_DAYS = 28
"""The "normal" window: what you have been doing. Four weeks is the usual
choice and it is roughly the timescale on which aerobic adaptation shows up."""

MIN_HISTORY_FOR_RATIO = 14
"""Below two weeks the acute window is most of the chronic one and the ratio is
pinned near 1 by construction."""

MIN_HISTORY_FOR_MONOTONY = ACUTE_DAYS
"""Monotony reads a full week of days including rest days. A window that runs
off the start of your history fills with zeros that are not rest -- they are
absence -- and those zeros inflate the spread, which makes monotony look
*better*. A warning metric that fails toward "everything is fine" is worse than
no warning metric."""

MIN_POINTS_FOR_WEIGHT = 10
"""Daily weight swings a couple of pounds on water alone; a trend drawn through
fewer than ten readings is drawing the water."""

MIN_ENTRIES_FOR_BASELINE = 14
BASELINE_DAYS = 30
"""What "normal for you" means. Deliberately personal: readiness has no
meaningful absolute scale, and a 17/25 is only informative next to your own
recent 17s."""

DIP_SDS = 1.0
"""How far below baseline counts as a dip."""


# --- load ------------------------------------------------------------------


def srpe(rpe: int, minutes: int) -> int:
    """Session load. The whole model rests on this one multiplication."""
    return rpe * minutes


def daily_load(
    sessions: Sequence[d.TrainingSession], start: dt.date, end: dt.date
) -> dict[dt.date, float]:
    """Load per day across the closed interval, **rest days included as 0**.

    The zeros are not padding. Monotony is a spread measurement, and a week of
    six hard days plus one rest day is a completely different week from six
    hard days alone -- but only if the rest day is present as a zero. Dropping
    empty days would silently make every week look varied.
    """
    loads = {
        start + dt.timedelta(days=offset): 0.0 for offset in range((end - start).days + 1)
    }
    for session in sessions:
        if start <= session.date <= end:
            loads[session.date] += float(srpe(session.rpe, session.minutes))
    return loads


def window_load(loads: Mapping[dt.date, float], end: dt.date, days: int) -> float:
    """Total load over the `days` days ending at `end`, inclusive."""
    return sum(loads.get(end - dt.timedelta(days=offset), 0.0) for offset in range(days))


def _history_days(sessions: Sequence[d.TrainingSession], today: dt.date) -> int:
    """Days from the first logged session to `today`, inclusive."""
    if not sessions:
        return 0
    return (today - min(session.date for session in sessions)).days + 1


def acwr(sessions: Sequence[d.TrainingSession], today: dt.date) -> float | None:
    """Acute:chronic workload ratio -- this week against your normal week.

    Roughly 0.8-1.3 is the band the injury literature likes; above ~1.5 is the
    spike that tends to precede getting hurt, and well below 0.8 is a deload
    whether or not you meant to take one.

    Both sides are expressed as a **mean daily load**, and the chronic window is
    clipped to however much history actually exists. That is the trap this
    function exists to avoid: with three weeks of data, a fixed 28-day
    denominator quietly includes seven days of zeros from before you started
    logging, which deflates "normal" and reports a spike that is really just the
    calendar. Clipping keeps the ratio honest between weeks two and four, and
    changes nothing after that.
    """
    history = _history_days(sessions, today)
    if history < MIN_HISTORY_FOR_RATIO:
        return None

    span = min(CHRONIC_DAYS, history)
    loads = daily_load(sessions, today - dt.timedelta(days=span - 1), today)

    chronic = window_load(loads, today, span) / span
    if chronic <= 0:
        return None
    acute = window_load(loads, today, ACUTE_DAYS) / ACUTE_DAYS
    return acute / chronic


def monotony(sessions: Sequence[d.TrainingSession], today: dt.date) -> float | None:
    """Foster's monotony: how alike the last seven days were.

    Mean daily load over its standard deviation. Above ~2.0 means every day
    looked the same, which is the pattern that actually precedes going stale --
    and it is invisible to total volume, because a flat week and a
    hard-easy-hard week can add to precisely the same number. This is the one
    number here that catches a training error you cannot feel yet.
    """
    if _history_days(sessions, today) < MIN_HISTORY_FOR_MONOTONY:
        return None

    loads = daily_load(sessions, today - dt.timedelta(days=ACUTE_DAYS - 1), today)
    values = list(loads.values())
    mean = statistics.fmean(values)
    if mean <= 0:
        return None
    spread = statistics.pstdev(values)
    if spread == 0:
        # Seven identical days, rest week included. Mathematically infinite
        # monotony; reported as absent, because the honest reading of a week of
        # seven zeros is "no week to describe" rather than "maximum danger".
        return None
    return mean / spread


def strain(sessions: Sequence[d.TrainingSession], today: dt.date) -> float | None:
    """Weekly load x monotony. Volume weighted by how unrelenting it was."""
    mono = monotony(sessions, today)
    if mono is None:
        return None
    loads = daily_load(sessions, today - dt.timedelta(days=ACUTE_DAYS - 1), today)
    return window_load(loads, today, ACUTE_DAYS) * mono


# --- weight ----------------------------------------------------------------


def weight_trend(
    points: Sequence[tuple[dt.date, float]], span: int = ACUTE_DAYS
) -> tuple[float, float] | None:
    """`(ema, lb_per_week)`, or None below `MIN_POINTS_FOR_WEIGHT` readings.

    The EMA is the number worth looking at: morning weight moves two pounds on
    hydration and yesterday's salt, so the raw reading mostly reports noise
    while the smoothed one reports the direction.

    The rate is a least-squares slope through the raw readings of the last four
    weeks, not a difference of two EMA values. Differencing a smoothed series
    inherits the smoother's lag and then amplifies whatever noise survived it;
    a regression uses every reading once and is far steadier week to week.
    """
    if len(points) < MIN_POINTS_FOR_WEIGHT:
        return None

    ordered = sorted(points)
    alpha = 2 / (span + 1)
    ema = ordered[0][1]
    for _, value in ordered[1:]:
        ema += alpha * (value - ema)

    recent = ordered[-CHRONIC_DAYS:]
    origin = recent[0][0]
    days = [float((date - origin).days) for date, _ in recent]
    values = [value for _, value in recent]
    if len(set(days)) < 2:
        return None
    slope = statistics.linear_regression(days, values).slope
    return ema, slope * 7


# --- readiness -------------------------------------------------------------


def readiness_mean(entries: Sequence[d.Readiness], today: dt.date, days: int) -> float | None:
    """Mean score over the `days` days ending today. None if nothing was
    answered in the window -- a mean over zero entries is not a low score."""
    start = today - dt.timedelta(days=days - 1)
    scores = [entry.score for entry in entries if start <= entry.date <= today]
    return statistics.fmean(scores) if scores else None


def readiness_baseline(
    entries: Sequence[d.Readiness], today: dt.date
) -> tuple[float, float] | None:
    """`(mean, sd)` over the trailing 30 days -- what normal looks like for you.

    None until `MIN_ENTRIES_FOR_BASELINE` answers exist, because the standard
    deviation is the load-bearing half and a spread estimated from six numbers
    is mostly a description of those six numbers.
    """
    start = today - dt.timedelta(days=BASELINE_DAYS - 1)
    scores = [entry.score for entry in entries if start <= entry.date <= today]
    if len(scores) < MIN_ENTRIES_FOR_BASELINE:
        return None
    spread = statistics.pstdev(scores)
    if spread == 0:
        return None
    return statistics.fmean(scores), spread


def readiness_dip(entries: Sequence[d.Readiness], today: dt.date) -> int:
    """Consecutive days ending today spent a full SD below baseline.

    The run length rather than a boolean, because one bad morning is a bad
    morning and three in a row is a trend. The panel stays quiet under three.
    Returns 0 when there is no baseline yet, when today is unanswered, or when
    a day inside the run was skipped -- a gap breaks the run rather than
    extending it across a day nobody measured.
    """
    baseline = readiness_baseline(entries, today)
    if baseline is None:
        return 0
    mean, spread = baseline
    floor = mean - DIP_SDS * spread

    by_date = {entry.date: entry.score for entry in entries}
    run = 0
    while True:
        score = by_date.get(today - dt.timedelta(days=run))
        if score is None or score >= floor:
            return run
        run += 1


# --- what goes in and what comes out ---------------------------------------
#
# Same rule as everything above: none of these say anything until the log
# behind them is dense enough to mean something. That matters more here than
# it does for load, because these are the metrics where *not logging* looks
# exactly like the thing being measured. A day with no stool row is a day you
# did not go or a day you forgot, and only one of those is worth a sentence.
# So every function in this section first asks whether the log was being kept
# at all, and returns None when it was not.

KCAL_PER_LB = 3500.0
"""The old rule of thumb. Wrong in the third decimal place and fine here --
it is converting a bucketed guess into a rate, not doing physiology."""

MEAL_KCAL = {"small": 400.0, "medium": 750.0, "large": 1200.0}
"""What the three sizes are worth, and **these are meant to be argued with.**

They are the one place the meal log stops being a record and starts being an
estimate, so they are named, in one place, rather than smuggled into a
calculation. Calibrated for a fighter in camp who logs snacks as small meals:
three mediums plus two smalls is about 3050, which is roughly a training day.

If the balance below persistently disagrees with the scale, these are what is
wrong -- not the scale. `weight_per_week` is a measurement; this is arithmetic
over three words."""

MIN_MEALS_FOR_DAY = 2
"""A day with one meal on it is a day you stopped logging after breakfast."""

MIN_DAYS_FOR_INTAKE = 5
"""Out of seven. Two missed days is a week you can still average; four is a
week you cannot."""

URINE_DARKNESS = {"clear": 1.0, "light": 2.0, "yellow": 3.0, "darker": 4.0}

DARK_URINE_MEAN = 3.5
"""Between `yellow` and `darker`, so a day only counts as dark when most of it
was actually dark rather than one reading pulling an average up."""

MIN_VOIDS_FOR_DAY = 2
"""After the first has been thrown away -- so three logged urinations. Fewer
than that is not a day with a hydration reading on it."""

DARK_RUN_DAYS = 3
"""One dark day is a Tuesday. Three in a row, while you are cutting weight, is
the thing that quietly costs you a sparring session."""

MIN_LOGGED_DAYS = 5
"""Out of the last seven, for the gut read. This is the "were you even
logging" gate -- see the section note."""

LIQUID_DAYS_FLAG = 3
LONG_GAP_DAYS = 3
"""Days without a stool row before the panel will mention it -- and only when
`MIN_LOGGED_DAYS` says the log was being kept through the gap."""


def _by_day(logs: Sequence[Any], today: dt.date, days: int) -> dict[dt.date, list[Any]]:
    """Group into the `days` days ending today, empty days included.

    The empty days are load-bearing for the same reason `daily_load`'s zeros
    are: a run is broken by a day with nothing in it, and a dict that simply
    omitted those days would splice two separate runs into one long one.
    """
    start = today - dt.timedelta(days=days - 1)
    out: dict[dt.date, list[Any]] = {
        start + dt.timedelta(days=offset): [] for offset in range(days)
    }
    for entry in logs:
        if start <= entry.date <= today:
            out[entry.date].append(entry)
    return out


# -- hydration ---------------------------------------------------------------


def day_darkness(logs: Sequence[d.BathroomLog]) -> float | None:
    """Mean urine darkness for one day, **first void discarded**.

    Overnight urine is concentrated in everybody -- it is the product of eight
    hours without a drink, not of a hydration problem -- so the morning
    reading is dark on a perfectly hydrated day, and including it would have
    this panel warn about dehydration every morning of the camp. Discarding it
    costs a reading and buys the rest of them their meaning.

    None when what is left is under `MIN_VOIDS_FOR_DAY`: two readings with the
    darkest of them removed is not a day's worth of evidence.
    """
    voids = sorted(
        (entry for entry in logs if entry.kind == "urine" and entry.color is not None),
        key=lambda entry: entry.hour,
    )
    rest = voids[1:]
    if len(rest) < MIN_VOIDS_FOR_DAY:
        return None
    return statistics.fmean(URINE_DARKNESS[str(entry.color)] for entry in rest)


def dark_urine_run(logs: Sequence[d.BathroomLog], today: dt.date) -> int:
    """Consecutive days ending today that read dark. 0, or a run.

    A day with no reading breaks the run rather than carrying it across
    itself -- the same rule `readiness_dip` follows, and for the same reason:
    the alternative is a warning that grows while nobody is measuring.
    """
    by_day = _by_day(logs, today, CHRONIC_DAYS)
    run = 0
    while run < CHRONIC_DAYS:
        mean = day_darkness(by_day.get(today - dt.timedelta(days=run), []))
        if mean is None or mean < DARK_URINE_MEAN:
            return run
        run += 1
    return run


def hydration_mean(points: Sequence[tuple[dt.date, float]], today: dt.date) -> float | None:
    """Mean ml a day logged on the watch over the last week.

    Absent days are *skipped*, not zeroed. The hydration glyph is tapped by
    hand, so a day it was not tapped is a day with no reading, and averaging a
    zero into it would report thirst that was really forgetfulness.
    """
    start = today - dt.timedelta(days=ACUTE_DAYS - 1)
    values = [value for date, value in points if start <= date <= today]
    return statistics.fmean(values) if len(values) >= 3 else None


# -- the gut -----------------------------------------------------------------


def logged_days(logs: Sequence[d.BathroomLog], today: dt.date, days: int = ACUTE_DAYS) -> int:
    """How many of the last `days` days have any bathroom row at all.

    The gate everything below is built on. Each of those readings is a
    statement about an absence -- no stool for three days, no dark readings --
    and an absence only means something if you were writing things down.
    """
    return sum(1 for entries in _by_day(logs, today, days).values() if entries)


def liquid_days(logs: Sequence[d.BathroomLog], today: dt.date) -> int | None:
    """Days in the last week with a liquid stool on them, or None if the log
    was too sparse to ask."""
    if logged_days(logs, today) < MIN_LOGGED_DAYS:
        return None
    return sum(
        1
        for entries in _by_day(logs, today, ACUTE_DAYS).values()
        if any(entry.solidity == "liquid" for entry in entries)
    )


def days_since_stool(logs: Sequence[d.BathroomLog], today: dt.date) -> int | None:
    """Days since the last stool row, or None when nobody was logging.

    0 means today. The None is not a technicality: three days of silence in a
    log you stopped keeping on Friday says nothing at all, and saying it
    anyway is how a panel earns the habit of being ignored.
    """
    if logged_days(logs, today) < MIN_LOGGED_DAYS:
        return None
    for offset in range(CHRONIC_DAYS):
        date = today - dt.timedelta(days=offset)
        if any(entry.date == date and entry.kind == "stool" for entry in logs):
            return offset
    return None


# -- food --------------------------------------------------------------------


def meal_kcal(size: str) -> float:
    return MEAL_KCAL[str(size)]


def daily_intake(
    meals: Sequence[d.MealLog], today: dt.date, days: int = ACUTE_DAYS
) -> dict[dt.date, float]:
    """Estimated kcal per day, over the days that were actually logged.

    Days with fewer than `MIN_MEALS_FOR_DAY` rows are **left out entirely**
    rather than counted low. That is the opposite of what `daily_load` does
    with rest days, and the difference is real: a day with no session on it is
    a day you did not train, and a day with no food on it is a day you did not
    write down what you ate.
    """
    return {
        date: sum(meal_kcal(entry.size) for entry in entries)
        for date, entries in _by_day(meals, today, days).items()
        if len(entries) >= MIN_MEALS_FOR_DAY
    }


def intake_mean(meals: Sequence[d.MealLog], today: dt.date) -> float | None:
    """Mean estimated intake a day this week, or None under
    `MIN_DAYS_FOR_INTAKE` logged days."""
    days = daily_intake(meals, today)
    if len(days) < MIN_DAYS_FOR_INTAKE:
        return None
    return statistics.fmean(days.values())


def diet_quality(meals: Sequence[d.MealLog], today: dt.date) -> float | None:
    """Mean `quality` over the week's meals -- how whole the food was.

    Over *meals*, not over days, because that is the thing you can act on: two
    good dinners do not undo five days of bad lunches, and a mean of daily
    means would let them.
    """
    start = today - dt.timedelta(days=ACUTE_DAYS - 1)
    scores = [entry.quality for entry in meals if start <= entry.date <= today]
    return statistics.fmean(scores) if len(scores) >= ACUTE_DAYS else None


def expenditure_mean(points: Sequence[tuple[dt.date, float]], today: dt.date) -> float | None:
    """Mean kcal out a day this week, as the watch measured it."""
    start = today - dt.timedelta(days=ACUTE_DAYS - 1)
    values = [value for date, value in points if start <= date <= today]
    return statistics.fmean(values) if len(values) >= MIN_DAYS_FOR_INTAKE else None


def implied_lb_per_week(balance: float) -> float:
    """A daily surplus or deficit, expressed as the weight it predicts.

    This is the only reason the balance is worth computing. On its own it is a
    number in a unit you have no feel for; next to `weight_per_week` -- which
    is *measured* -- it becomes a check on itself, and when the two disagree
    the scale is right and `MEAL_KCAL` is wrong.
    """
    return balance * 7 / KCAL_PER_LB


# -- overnight scalars -------------------------------------------------------
#
# HRV, resting HR and sleep, once the watch is filling them in. Deliberately
# the same shape as readiness: a personal baseline, a run the wrong side of
# it, and silence under a fortnight of history. The published normal ranges
# for all three are wide enough to contain both your best week and your worst,
# so the only comparison that carries information is against yourself.


def scalar_mean(
    points: Sequence[tuple[dt.date, float]],
    today: dt.date,
    days: int = ACUTE_DAYS,
    minimum: int = 3,
) -> float | None:
    start = today - dt.timedelta(days=days - 1)
    values = [value for date, value in points if start <= date <= today]
    return statistics.fmean(values) if len(values) >= minimum else None


def scalar_baseline(
    points: Sequence[tuple[dt.date, float]], today: dt.date
) -> tuple[float, float] | None:
    """`(mean, sd)` over the trailing 30 days, or None under
    `MIN_ENTRIES_FOR_BASELINE` readings.

    Zero spread returns None as well: a series that has not moved has no scale
    in which to measure a departure from it.
    """
    start = today - dt.timedelta(days=BASELINE_DAYS - 1)
    values = [value for date, value in points if start <= date <= today]
    if len(values) < MIN_ENTRIES_FOR_BASELINE:
        return None
    spread = statistics.pstdev(values)
    if spread == 0:
        return None
    return statistics.fmean(values), spread


def scalar_run(
    points: Sequence[tuple[dt.date, float]],
    today: dt.date,
    *,
    above: bool,
    sds: float = DIP_SDS,
) -> int:
    """Consecutive days ending today spent `sds` standard deviations the wrong
    side of baseline -- `above` for resting heart rate, below it for HRV.

    A missing day breaks the run. Every gate in this module ends up saying the
    same thing: a measurement nobody took is not evidence of anything.
    """
    baseline = scalar_baseline(points, today)
    if baseline is None:
        return 0
    mean, spread = baseline
    limit = mean + sds * spread if above else mean - sds * spread

    by_date = dict(points)
    run = 0
    while run < BASELINE_DAYS:
        value = by_date.get(today - dt.timedelta(days=run))
        if value is None or (value <= limit if above else value >= limit):
            return run
        run += 1
    return run
