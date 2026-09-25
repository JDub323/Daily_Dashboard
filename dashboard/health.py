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

from .models import api
from .models import domain as d
from .models.common import InputState

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

DRINK_ML = 355.0
"""What one drink is worth in millilitres -- twelve US fluid ounces.

The counterpart to `MEAL_KCAL` and meant to be read the same way: the drink
log records a count, and this is the one line that turns counts into a volume.
It applies to four of the five kinds. Alcohol is measured in standard drinks
rather than in twelve-ounce servings -- a shot and a beer are both 1 -- so
there is no volume to convert, which is why `NOT_FLUID` exists rather than
this constant simply being multiplied through."""

NOT_FLUID = frozenset({"alcohol"})
"""The tags that take a drink out of the day's fluid -- there is one.

Stated as what is excluded rather than as what counts, which is the way round
it was written when a drink had a single kind. The reason is that everything
in this log is water with something in it: a plain glass carries no tags at
all, so a list of the tags that *do* hydrate would have nothing to say about
the commonest row in the table.

Caffeine counts, which surprises people. The diuretic effect is real and it is
smaller than the water it arrives in; net, a mug hydrates.
Alcohol is out for the opposite reason and also for a duller one: its unit is
not a volume at all, so there is no honest number to add. It excludes the whole
row rather than its share of it -- a rum and coke is alcohol and sugar, and the
mixer is not worth pretending we can measure."""

MIN_DRINK_DAYS = 5
"""Out of seven, and the same gate the gut read uses. A week with two days of
drinks logged has a mean, and the mean is a statement about which two days you
remembered rather than about how much you drank."""

# Deliberately absent: how much caffeine is a lot, and how much alcohol is worth
# mentioning. Both are editorial -- what is worth a sentence -- rather than
# measurement, and those live in `panels/health.js` beside `SLEEP_FLOOR` and
# `QUALITY_FLAG`, which are the same kind of number. This file computes; that
# one decides what to say about it.

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


# -- what you drank ----------------------------------------------------------
#
# The other half of the hydration reading, and the half that is yours to
# change. `dark_urine_run` says what your body did with the water; this says
# how much of it there was to work with. Neither is worth much alone -- a dark
# week with nothing logged is a week you did not write down, and 3000ml a day
# with the urine still reading dark is a salt problem rather than a water one.
#
# Every function here carries the same gate the gut read does, and for the
# identical reason: the interesting readings are all statements about an
# absence -- little water, no alcohol -- and an absence only means something if
# you were writing things down.


def drink_logged_days(logs: Sequence[d.DrinkLog], today: dt.date, days: int = ACUTE_DAYS) -> int:
    """How many of the last `days` days have any drink row at all."""
    return sum(1 for entries in _by_day(logs, today, days).values() if entries)


def day_fluid_ml(logs: Sequence[d.DrinkLog]) -> float:
    """One day's fluid, in ml. Alcohol contributes nothing -- see `NOT_FLUID`."""
    return sum(
        entry.amount * DRINK_ML
        for entry in logs
        if not NOT_FLUID.intersection(str(tag) for tag in entry.tags)
    )


def fluid_mean(logs: Sequence[d.DrinkLog], today: dt.date) -> float | None:
    """Mean ml a day over the week, or None if the log was not being kept.

    Days with rows but no *hydrating* rows count as zero rather than being
    skipped, which is the opposite of what `daily_intake` does with meals. The
    difference is what the silence means: a day with two beers logged and
    nothing else is a day you drank no water, and it is precisely the day this
    reading exists to catch. A day with nothing at all on it is a day you
    forgot, and that one is dropped by the gate above rather than counted low.
    """
    if drink_logged_days(logs, today) < MIN_DRINK_DAYS:
        return None
    days = [
        day_fluid_ml(entries) for entries in _by_day(logs, today, ACUTE_DAYS).values() if entries
    ]
    return statistics.fmean(days) if days else None


def caffeine_mean(logs: Sequence[d.DrinkLog], today: dt.date) -> float | None:
    """Mean caffeinated drinks a day this week.

    Over logged days rather than over all seven, so a weekend away does not
    read as two days of abstinence you did not actually manage.
    """
    if drink_logged_days(logs, today) < MIN_DRINK_DAYS:
        return None
    days = [
        sum(entry.amount for entry in entries if "caffeine" in entry.tags)
        for entries in _by_day(logs, today, ACUTE_DAYS).values()
        if entries
    ]
    return statistics.fmean(days) if days else None


def alcohol_week(logs: Sequence[d.DrinkLog], today: dt.date) -> float | None:
    """Standard drinks in the last seven days, or None if the log was sparse.

    A total rather than a mean, because that is the unit the number is thought
    about in -- nobody counts alcohol per day -- and because a mean of 1.1
    hides the Saturday that is the whole of it.
    """
    if drink_logged_days(logs, today) < MIN_DRINK_DAYS:
        return None
    start = today - dt.timedelta(days=ACUTE_DAYS - 1)
    return sum(
        entry.amount for entry in logs if "alcohol" in entry.tags and start <= entry.date <= today
    )


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

    Snacks included, unlike in `_meal_rows`. The two ask different questions:
    that one asks whether the log was kept, and a snack is not a meal; this one
    asks how whole the food was, and an apple is an honest 5. Excluding them
    here would score a week of fruit as though it had not been eaten.
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


# --- the rail: four scores, and what each of them is standing on -------------
#
# The health tab used to answer its three questions in sentences and say
# nothing else, and the two things it refused hardest to produce were a
# readiness percentage and any single "are you ready" number. Both refusals
# were right for the panel that made them, and both are repealed here. What
# changed is not the argument -- it is that the laptop stopped being a logging
# surface, so a screen that spoke only when it had a fortnight of history
# became a screen that was blank most mornings while being the only place the
# day's state is shown at all.
#
# So there are four composites now, and the whole of the old objection is
# answered by three rules rather than by not building them:
#
#   **A score is the mean of the inputs that scored, and nothing else.** No
#   weights, no filling a missing input in with a neutral value, no carrying
#   yesterday's reading forward. Equal weights are a judgement and they are
#   meant to be argued with; what they buy is that nobody can quietly steer the
#   number, which is the failure mode of every wellness score ever shipped.
#
#   **Every part is one keystroke away.** `f` and a letter opens the zoom, and
#   the zoom draws each input as its own bar against its own baseline with the
#   raw reading printed beside it. A composite you cannot take apart is a
#   composite you end up believing; this one is checkable in one press.
#
#   **An input that cannot be scored honestly says which kind of absence it
#   is.** See `InputState`. This is the rule the old panel enforced by staying
#   silent, and it is the one that matters most now that a hole is a hole
#   rather than a box you have not got to yet -- a night the watch spent on the
#   charger and a rest day must never render alike.
#
# What has NOT changed: nothing here invents history it does not have. Every
# gate above this line still holds, and an input behind its gate reports
# `no_baseline` rather than a number computed off four days.

BAND_LOW = 0.8
BAND_HIGH = 1.3
"""The acute:chronic band the injury literature likes. `panels/health.js`
carries a wider high of 1.5 for the *sentence* it prints, and the difference is
deliberate: 1.5 is where a spike is worth interrupting you about, and 1.3 is
where it stops being an ordinary week."""

ACWR_FLOOR = 0.4
ACWR_CEILING = 1.8
"""Where the ratio scores zero on either side. Not limits -- nothing here is
forbidden -- but the points past which "how far outside the band" has stopped
being a useful distinction."""

MONOTONY_GOOD = 1.0
MONOTONY_BAD = 2.5
"""Foster's monotony, scored downward. Above 2.0 is the published flag; 2.5 is
where this stops discriminating, because everything past it is the same
answer -- every day this week was the same size."""

DEFICIT_FLOOR = -750.0
DEFICIT_CEILING = 0.0
"""The daily energy balance this scores as correct, in kcal.

A judgement, exactly like `MEAL_KCAL`, and meant to be argued with in the same
way. It is a cut toward a February weigh-in, so a surplus is wrong and so is a
hole big enough to cost training: 750 kcal a day is about a pound and a half a
week, which is the fastest anyone should be taking weight off while sparring."""

FUEL_SLACK = 750.0
"""How far outside the deficit band the balance falls to zero. Wide, because
the balance is an estimate over three meal buckets and a narrow ramp would be
scoring the precision of `MEAL_KCAL` rather than the eating."""

BASE_FLUID_ML = 2600.0
"""What a day needs before training, in ml.

There is a fixed term here at all only because there is a moving one beside it.
The drink log argued for years against inventing a hydration target, on the
grounds that a target you hit by logging teaches you to log -- and that
objection is answered by the *other* term: the watch now says how much was
sweated out, so what a day asks for moves with the week rather than being a
number somebody chose."""

MIN_POINTS_FOR_JUMP = 10
"""Testing days before the vertical is worth a line.

It used to say "ten of them at six-week intervals is a year", and that was
never true of this camp: `LFC` and `LC` both take a vertical, so the plan asks
for one every Monday and every Thursday and ten of them is five weeks.

The correction matters more than a stale comment usually would, because the
cadence is what decided how the number is *scored*. A twice-weekly measurement
was being fed to `_against_baseline`, which wants fourteen readings inside a
trailing thirty days -- a bar two a week cannot clear, at any point, ever. The
input was not merely toothless; it was unreachable, and would have read
`no_baseline` after a year of testing. See `jump_score`."""

JUMP_RECENT = 4
"""Tests that make up "where you are now" -- a fortnight at two a week.

Four rather than one because a single vertical is mostly noise: shoes, how the
warm-up went, which rep you decided was the one. The question this input exists
to answer is whether a block of training moved anything, and that question is
asked of a fortnight, not of a Thursday."""

JUMP_STALE_DAYS = 14
"""How old the last test may be before the input stops answering.

Without this the score would be the vertical you had in November, printed in
February beside four fresh bars, with nothing on screen to say it was four
months old. A sparse metric has to be allowed to go quiet -- and it reports
`not_logged` rather than `no_baseline`, because a testing day nobody tested on
is a fact about the week and not about the length of the series."""

VO2MAX_WINDOW = 90
"""Days over which the VO2max change is measured. A quarter, because the
estimate moves by fractions and only updates after an outdoor run -- a
month-on-month difference would mostly report which weeks it rained."""

MEALS_A_DAY = 3
"""How many meals a day the log expects. A claim about the eater rather than a
target -- he eats three times a day, so a day with two rows in it is a day with
a row missing. `chips.js` carries the same number for the same reason."""

CAFFEINE_CEILING = 6.0
"""Caffeinated drinks a day at which the caffeine input scores zero. Double the
sentence's flag of three, because this is a slope rather than a warning: the
flag is where the number becomes worth *saying*, not where it becomes bad."""

ALCOHOL_CEILING = 10.0
"""Standard drinks in seven days at which the alcohol input scores zero. Same
argument -- `panels/health.js` speaks at four, and four is where the number is
likely to be explaining something else on the panel rather than where it has
become the whole story."""


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _ramp(value: float, zero: float, hundred: float) -> float:
    """A straight line from `zero` to `hundred`, clamped at both ends.

    `zero > hundred` reverses it, which is how the inputs where less is better
    -- stress, urine darkness, monotony, caffeine -- are scored without a
    second function and without a sign to get backwards.
    """
    if zero == hundred:
        return 100.0
    return _clamp((value - zero) / (hundred - zero) * 100.0)


def _band_score(value: float, low: float, high: float, floor: float, ceiling: float) -> float:
    """100 anywhere inside `[low, high]`, sloping to 0 at `floor` and `ceiling`.

    For the two inputs with a correct *range* rather than a correct direction:
    the workload ratio, where both a spike and a deload are departures, and the
    energy balance, where eating too little during a camp is its own mistake
    rather than extra progress.
    """
    if low <= value <= high:
        return 100.0
    return _ramp(value, floor, low) if value < low else _ramp(value, ceiling, high)


def _today(points: Sequence[tuple[dt.date, float]], today: dt.date) -> float | None:
    """Today's reading and only today's -- never "the most recent on file".

    The same rule `state.latest` follows and for the same reason: a three-day-
    old resting heart rate read as this morning's is a panel you have to
    date-check, which is a panel you stop trusting.
    """
    return dict(points).get(today)


def _against_baseline(
    points: Sequence[tuple[dt.date, float]],
    today: dt.date,
    *,
    higher_is_better: bool,
    sds: float = 2.0,
) -> float | None:
    """Today's reading as 0-100 against its own trailing month, or None.

    50 is exactly at baseline and the ends are `sds` standard deviations out.
    Two rather than one: a single SD is where `scalar_run` starts calling
    something a dip, so a scale that bottomed out there would show every
    ordinary bad morning as zero.

    None when there is no baseline yet *or* no reading today, and the caller
    tells those two apart -- they are `no_baseline` and `no_watch_data`, and
    the difference between them is the whole point of `InputState`.
    """
    baseline = scalar_baseline(points, today)
    if baseline is None:
        return None
    value = _today(points, today)
    if value is None:
        return None
    mean, spread = baseline
    z = (value - mean) / spread
    return _clamp(50.0 + (z if higher_is_better else -z) * 50.0 / sds)


def jump_score(
    points: Sequence[tuple[dt.date, float]], today: dt.date
) -> float | None:
    """The vertical as 0-100: the last four tests against everything before
    them. None when there is not enough, or when testing has lapsed.

    A separate function rather than another `_against_baseline` caller, and the
    difference between them is the whole point. That one is built for a metric
    the watch produces every night: it windows by *days*, wants fourteen
    readings inside thirty of them, and requires one dated today. Every one of
    those three is wrong here. Two tests a week is nine readings in a month, so
    the count gate can never pass; and a vertical is taken on a Monday, so
    requiring one today would blank the input on five days out of seven even
    when the testing is going perfectly.

    So this windows by *tests*. `JUMP_RECENT` of them are the present, whatever
    span they cover, and the rest of the series is what the present is measured
    against -- which makes the reading "the last fortnight against the block
    behind it", the shape the question is actually asked in.

    What stays borrowed is the scale: a z against the older tests' own spread,
    2 SD to the ends, 50 at no change -- identical to `_against_baseline`, so
    this bar means the same thing as the four beside it. A vertical's honest
    unit is its own variance and nothing else; an inch is a lot for one man and
    noise for another.

    Population SD, like `scalar_baseline`, and zero spread is None for the same
    reason: a series that has not moved supplies no scale to measure a
    departure from.
    """
    series = sorted(points)
    if len(series) < MIN_POINTS_FOR_JUMP:
        return None
    # Testing has stopped, or the camp is paused, or the phone has not been
    # opened in a gym for a fortnight. The caller renders this as `not_logged`.
    if (today - series[-1][0]).days > JUMP_STALE_DAYS:
        return None

    recent = [value for _, value in series[-JUMP_RECENT:]]
    older = [value for _, value in series[:-JUMP_RECENT]]
    spread = statistics.pstdev(older)
    if spread == 0:
        return None
    z = (statistics.fmean(recent) - statistics.fmean(older)) / spread
    return _clamp(50.0 + z * 50.0 / 2.0)


def jump_absence(
    points: Sequence[tuple[dt.date, float]], today: dt.date
) -> InputState:
    """Which kind of nothing a vertical with no score is.

    The same distinction `_absence` draws for the watch's scalars, drawn
    against the other absence: too few tests ever is a young series, and a
    long-enough series that has gone quiet is a log that stopped being kept.
    One of those is fixed by waiting and the other by jumping, and a panel that
    printed the same word for both would be telling you to wait.
    """
    if len(points) < MIN_POINTS_FOR_JUMP:
        return InputState.no_baseline
    if (today - max(date for date, _ in points)).days > JUMP_STALE_DAYS:
        return InputState.not_logged
    return InputState.no_baseline


def _absence(points: Sequence[tuple[dt.date, float]], today: dt.date) -> InputState:
    """Which kind of nothing a watch metric with no score is.

    No baseline is a young series; a baseline with no reading today is a night
    on the charger. Getting this backwards would be the panel blaming the watch
    for the calendar, or the calendar for the watch.
    """
    if scalar_baseline(points, today) is None:
        return InputState.no_baseline
    return InputState.no_watch_data


def _input(
    key: str,
    label: str,
    value: float | None,
    *,
    absent: InputState,
    reading: str | None = None,
) -> api.ZoneInput:
    """One row of a zoom: a score when there is one, and a named absence when
    there is not."""
    if value is None:
        return api.ZoneInput(key=key, label=label, state=absent, reading=reading)
    return api.ZoneInput(
        key=key,
        label=label,
        state=InputState.scored,
        value=round(value, 1),
        reading=reading,
    )


ABSENT_WORDS = {
    InputState.no_watch_data: "no watch data",
    InputState.no_baseline: "not enough history yet",
    InputState.not_logged: "not logged",
    InputState.not_asked: "nothing asked today",
}
"""How each absence is said out loud. Here rather than in the panel because the
distinction is the server's claim about the data, and a client paraphrasing it
would be a second opinion about what a missing row means."""


def _zone(inputs: list[api.ZoneInput]) -> api.ZoneScore:
    """Fold a zone's inputs into its bar.

    The mean of whichever scored, and None when none of them did -- never zero.
    A zone with nothing behind it renders as a sentence rather than as an empty
    bar, because an empty bar and a bar at zero are the same picture and they
    are opposite facts.

    `note` names what is missing whenever anything is, grouped by *why*, and
    goes unsaid when everything scored. A bar with nothing wrong with it should
    say nothing at all -- the whole tab is built on that.
    """
    scored = [item.value for item in inputs if item.value is not None]

    missing: dict[InputState, list[str]] = {}
    for item in inputs:
        if item.state is not InputState.scored:
            missing.setdefault(item.state, []).append(item.label)

    note = " · ".join(
        f"{ABSENT_WORDS[state]}: {', '.join(labels)}"
        for state, labels in missing.items()
        if state in ABSENT_WORDS
    )

    return api.ZoneScore(
        value=round(statistics.fmean(scored), 1) if scored else None,
        inputs=inputs,
        note=note or None,
    )


def _readiness_on(entries: Sequence[d.Readiness], today: dt.date) -> d.Readiness | None:
    return next((entry for entry in entries if entry.date == today), None)


def _vs_baseline_words(
    points: Sequence[tuple[dt.date, float]], today: dt.date, unit: str
) -> str | None:
    """`58 ms against a 62 baseline` -- the fact the score is a rescaling of.

    Printed beside every normalised bar, and this is the half that makes the
    other half checkable. A 34 with no number beside it is a number you either
    believe or ignore; a 34 next to "58 against 62" is one you can argue with.
    """
    value = _today(points, today)
    if value is None:
        return None
    baseline = scalar_baseline(points, today)
    if baseline is None:
        return f"{value:g} {unit}, no baseline yet"
    return f"{value:g} {unit} against a {baseline[0]:.0f} baseline"


def _jump_words(points: Sequence[tuple[dt.date, float]]) -> str | None:
    """`24.5 in, last four 24.1 against 23.4` -- what the bar is a rescaling of.

    Same job as `_vs_baseline_words` and a different sentence, because the
    comparison underneath it is a different one: four tests against the tests
    before them, not tonight against a trailing month. Printing the month's
    words over this arithmetic would be a caption describing a calculation
    nobody ran.

    Takes no `today`. Everything here is about the series' own last readings,
    and the staleness that today decides is already said by the score going
    absent -- saying it twice, in two places that could disagree, is how a
    panel ends up printing a number it has just called missing.
    """
    if not points:
        return None
    series = sorted(points)
    last = series[-1][1]
    if len(series) < MIN_POINTS_FOR_JUMP:
        return f"{last:g} in, best of three — {len(series)} of {MIN_POINTS_FOR_JUMP} tests"
    recent = statistics.fmean([value for _, value in series[-JUMP_RECENT:]])
    older = statistics.fmean([value for _, value in series[:-JUMP_RECENT]])
    return f"{last:g} in — last {JUMP_RECENT} average {recent:.1f} against {older:.1f}"


def _plain_words(
    points: Sequence[tuple[dt.date, float]], today: dt.date, suffix: str
) -> str | None:
    value = _today(points, today)
    return None if value is None else f"{value:g} {suffix}"


def feel_row(entry: d.FeelLog) -> float:
    """One feel as 0-100: energy and mood, each 1-5, together. Symptoms are not
    in it and never will be -- a sore throat is not a number between 0 and
    100, and they go to the flag line instead."""
    return (entry.energy + entry.mood - 2) / 8 * 100


def feel_score(feels: Sequence[d.FeelLog], today: dt.date) -> float | None:
    """Today's feel as 0-100, or None when nothing was logged today.

    As answered for the first fortnight of the log, then against your own
    normal the way the watch's scalars are -- 50 at your usual day, 2 SD to
    the ends. The switch is the same `MIN_ENTRIES_FOR_BASELINE` the watch uses,
    counted in days with a feel on them, because a 3-and-3 is only average if
    that is what your days are.
    """
    todays = [feel_row(entry) for entry in feels if entry.date == today]
    if not todays:
        return None
    now = statistics.fmean(todays)
    start = today - dt.timedelta(days=BASELINE_DAYS - 1)
    days: dict[dt.date, list[float]] = {}
    for entry in feels:
        if start <= entry.date < today:
            days.setdefault(entry.date, []).append(feel_row(entry))
    if len(days) < MIN_ENTRIES_FOR_BASELINE:
        return now
    means = [statistics.fmean(values) for values in days.values()]
    spread = statistics.pstdev(means)
    if spread == 0:
        return now
    return _clamp(50.0 + (now - statistics.fmean(means)) / spread * 25.0)


def _feel_words(feels: Sequence[d.FeelLog], today: dt.date) -> str | None:
    todays = [entry for entry in feels if entry.date == today]
    if not todays:
        return None
    if len(todays) == 1:
        one = todays[0]
        return f"energy {one.energy} · mood {one.mood} at {one.at}"
    energy = statistics.fmean(entry.energy for entry in todays)
    mood = statistics.fmean(entry.mood for entry in todays)
    return f"energy {energy:.1f} · mood {mood:.1f}, {len(todays)} today"


def recovery_score(
    hrv: Sequence[tuple[dt.date, float]],
    rhr: Sequence[tuple[dt.date, float]],
    sleep_score: Sequence[tuple[dt.date, float]],
    battery: Sequence[tuple[dt.date, float]],
    stress: Sequence[tuple[dt.date, float]],
    readiness: Sequence[d.Readiness],
    today: dt.date,
    feels: Sequence[d.FeelLog] = (),
) -> api.ZoneScore:
    """Am I recovering -- the first of the tab's three questions.

    Seven inputs, and the first five fall into two groups that behave differently in the
    first weeks of a series, which is what this function is really built
    around.

    Sleep score, Body Battery and stress are already 0-100 on scales Garmin
    publishes, so they score from the first morning the watch is worn. HRV and
    resting heart rate are not: their absolute values are genetic, the
    published ranges are wide enough to hold both your best week and your
    worst, and the only comparison carrying information is against a fortnight
    of your own. Until that exists they report `no_baseline`, and the bar is a
    mean over the ones that could speak.

    The sixth is the check-in, and it is here for two reasons. It is the only
    input that exists on day one -- the questionnaire has been kept for months
    and the watch is new -- so it is what stops the bar being silent through
    the weeks the baselines are filling. And it is *subjective*, which makes it
    the one input that can disagree with the rest: a sleep score of 88 against
    a sleep quality of 2 is a diagnostic and not noise, and the two sitting in
    one zoom is the only way that disagreement is ever seen.

    The seventh is the feel log, which is the check-in's argument carried
    through the day: self-report tracks the training response more closely
    than heart rate or HRV do (Saw, Main & Gastin 2016), and unlike the
    check-in it is asked after the day has happened rather than before.
    """
    row = _readiness_on(readiness, today)
    stress_today = _today(stress, today)

    return _zone([
        _input(
            "hrv",
            "hrv vs baseline",
            _against_baseline(hrv, today, higher_is_better=True),
            absent=_absence(hrv, today),
            reading=_vs_baseline_words(hrv, today, "ms"),
        ),
        _input(
            "rhr",
            "resting hr vs baseline",
            _against_baseline(rhr, today, higher_is_better=False),
            absent=_absence(rhr, today),
            reading=_vs_baseline_words(rhr, today, "bpm"),
        ),
        _input(
            "sleep_score",
            "sleep score",
            _today(sleep_score, today),
            absent=InputState.no_watch_data,
            reading=_plain_words(sleep_score, today, "of 100"),
        ),
        _input(
            "body_battery",
            "body battery",
            _today(battery, today),
            absent=InputState.no_watch_data,
            reading=_plain_words(battery, today, "at wake"),
        ),
        _input(
            "stress",
            "stress",
            # Inverted: Garmin's scale counts up in the thing you do not want,
            # and every bar on this rail counts up in the thing you do.
            None if stress_today is None else 100.0 - stress_today,
            absent=InputState.no_watch_data,
            reading=_plain_words(stress, today, "all-day average"),
        ),
        _input(
            "checkin",
            "check-in",
            # The questionnaire sums to 25 at its best, so x4 is the scale.
            # Not a ramp from zero: 5 of 25 is a real answer, the morning where
            # every one of the five was as bad as it goes.
            None if row is None else row.score * 4.0,
            absent=InputState.not_logged,
            reading=None if row is None else f"{row.score} of 25 this morning",
        ),
        _input(
            "feel",
            "feel",
            feel_score(feels, today),
            absent=InputState.not_logged,
            reading=_feel_words(feels, today),
        ),
    ])


def _strain_series(
    sessions: Sequence[d.TrainingSession], today: dt.date
) -> list[tuple[dt.date, float]]:
    """Strain computed for each of the last `BASELINE_DAYS`, so it can be
    scored against itself.

    Strain has no published range and no target -- it is a weekly load times a
    unitless ratio -- so the only thing it can honestly be compared with is
    what yours has been. Which is the conclusion HRV reached, and it gets the
    same machinery: `scalar_baseline` over this series, and silence until a
    fortnight of it exists.
    """
    out = []
    for offset in range(BASELINE_DAYS):
        day = today - dt.timedelta(days=offset)
        value = strain(sessions, day)
        if value is not None:
            out.append((day, value))
    return sorted(out)


def load_score(
    sessions: Sequence[d.TrainingSession],
    plan_open: int,
    plan_total: int,
    jump: Sequence[tuple[dt.date, float]],
    today: dt.date,
) -> api.ZoneScore:
    """Am I training enough -- the second question, and the only zone whose
    inputs are all things you did rather than things measured about you.

    `plan_open` and `plan_total` are the *week's* prescribed blocks: how many
    are still unanswered, and how many there were. A week rather than a day,
    because the other three inputs here are seven- and twenty-eight-day
    windows and a daily figure beside them swings from 0 to 100 across a
    Tuesday -- at nine in the morning nothing prescribed has been logged yet,
    which is not a fact about anybody's training.

    A `plan_total` of zero is a week the plan asked nothing of -- eleven of the
    twenty-eight are unwritten -- and it reports `not_asked` rather than a zero
    score. A week that prescribed nothing is a week you cannot fail, and
    scoring it at zero would have the unwritten stretch of the camp read as
    three months of not training.

    Strain is included because it was asked for, and it is worth saying that it
    partly double-counts: strain is weekly load times monotony, so a flat week
    pulls this bar down twice. That is defensible -- sameness at volume is what
    the number exists to catch, and catching it twice is not wrong -- but it is
    an argument rather than an accident, and the zoom shows both so the
    doubling is visible instead of baked in.

    The vertical jump is here and stays ungated: below `MIN_POINTS_FOR_JUMP`
    tests, or after a fortnight of not testing, it contributes nothing and the
    other four inputs carry the bar between them. What it is no longer is
    *unreachable* -- it was scored through `_against_baseline`, which cannot be
    satisfied by a twice-weekly measurement at all, so the input had never once
    produced a number and could not have. `jump_score` is the comparison this
    one actually admits of.

    It is still the slowest thing on the panel, and it should be. Four tests
    move over a fortnight, so this bar answers a fortnight's question while the
    three beside it answer a week's -- which is the correct division of labour
    and not a defect: whether the training is working and whether this week was
    sensible are different questions, and only one of them can be asked on a
    Tuesday morning.
    """
    ratio = acwr(sessions, today)
    mono = monotony(sessions, today)
    week_strain = strain(sessions, today)
    strain_points = _strain_series(sessions, today)
    logged = plan_total - plan_open

    return _zone([
        _input(
            "acwr",
            "workload ratio",
            None
            if ratio is None
            else _band_score(ratio, BAND_LOW, BAND_HIGH, ACWR_FLOOR, ACWR_CEILING),
            absent=InputState.no_baseline,
            reading=None if ratio is None else f"{ratio:.2f} against your four-week normal",
        ),
        _input(
            "monotony",
            "monotony",
            None if mono is None else _ramp(mono, MONOTONY_BAD, MONOTONY_GOOD),
            absent=InputState.no_baseline,
            reading=None if mono is None else f"{mono:.1f} — how alike the week's days were",
        ),
        _input(
            "strain",
            "strain vs baseline",
            _against_baseline(strain_points, today, higher_is_better=False),
            absent=InputState.no_baseline,
            reading=None if week_strain is None else f"{week_strain:,.0f} this week",
        ),
        _input(
            "prescribed",
            "plan logged",
            None if plan_total <= 0 else logged / plan_total * 100.0,
            absent=InputState.not_asked,
            reading=None if plan_total <= 0 else f"{logged} of {plan_total} blocks logged",
        ),
        _input(
            "vert_jump",
            "vertical jump",
            jump_score(jump, today),
            absent=jump_absence(jump, today),
            reading=_jump_words(jump),
        ),
    ])


def _meal_rows(meals: Sequence[d.MealLog], today: dt.date) -> int:
    """This week's meals, not counting snacks.

    Snacks are excluded because this number is scored against
    `ACUTE_DAYS * MEALS_A_DAY` -- twenty-one *meals* -- and it is the input
    that says whether the log was kept at all. An apple counted here would
    read as a meal that was eaten, and a week of logged snacks with the
    dinners missing from it would score as a complete log, which is precisely
    the failure `fuel_score` exists to make visible.

    They are still food, and still counted everywhere the question is about
    intake rather than about completeness: `MEAL_KCAL` sums them into the
    energy balance and `diet_quality` averages their quality.
    """
    start = today - dt.timedelta(days=ACUTE_DAYS - 1)
    return sum(1 for entry in meals if start <= entry.date <= today and not entry.is_snack)


def fuel_score(
    meals: Sequence[d.MealLog],
    balance: float | None,
    today: dt.date,
) -> api.ZoneScore:
    """Am I eating like somebody in a camp.

    Three inputs, and the first is the only number on this rail with a *target*
    rather than a baseline. That target is the deficit band, it is a judgement,
    and it is a judgement about this camp specifically -- a cut toward a
    February weigh-in, where a surplus is wrong and so is a hole big enough to
    cost training.

    Meals logged is here for a reason worth restating now that the laptop takes
    no logs at all: a balance computed off a week with meals missing from it
    does not read as incomplete, it reads as a light week, and
    `implied_lb_per_week` will quietly believe it. Scoring the completeness of
    the log beside the conclusion drawn from it is what makes that visible.
    """
    quality = diet_quality(meals, today)
    rows = _meal_rows(meals, today)
    wanted = ACUTE_DAYS * MEALS_A_DAY

    return _zone([
        _input(
            "balance",
            "energy balance",
            None
            if balance is None
            else _band_score(
                balance,
                DEFICIT_FLOOR,
                DEFICIT_CEILING,
                DEFICIT_FLOOR - FUEL_SLACK,
                DEFICIT_CEILING + FUEL_SLACK,
            ),
            absent=InputState.not_logged,
            reading=None
            if balance is None
            else f"{abs(balance):,.0f} kcal a day {'under' if balance < 0 else 'over'} burn",
        ),
        _input(
            "quality",
            "diet quality",
            # 1-5, and 1 is a real answer rather than the bottom of a ramp from
            # nothing -- a week of burgers is a week of burgers.
            None if quality is None else _ramp(quality, 1.0, 5.0),
            absent=InputState.not_logged,
            reading=None if quality is None else f"{quality:.1f} of 5, how whole the food was",
        ),
        _input(
            "meals",
            "meals logged",
            min(rows / wanted * 100.0, 100.0),
            absent=InputState.not_logged,
            reading=f"{rows} of {wanted} this week",
        ),
    ])


def _week_darkness(visits: Sequence[d.BathroomLog], today: dt.date) -> float | None:
    """Mean urine darkness over the week, off days dense enough to ask.

    Built on `day_darkness`, which throws away each day's first void -- so a
    day with only one reading contributes nothing, which is why this is None on
    a week the log was kept sporadically. Same rule as everything above it: a
    measurement nobody took is not evidence of anything.
    """
    days = _by_day(visits, today, ACUTE_DAYS)
    scores = [value for logs in days.values() if (value := day_darkness(logs)) is not None]
    return statistics.fmean(scores) if scores else None


def hydration_score(
    fluid: float | None,
    sweat: float | None,
    visits: Sequence[d.BathroomLog],
    caffeine: float | None,
    alcohol: float | None,
    today: dt.date,
) -> api.ZoneScore:
    """Am I drinking enough for the week I am actually having.

    The fluid input is the one worth explaining, because the drink log spent a
    paragraph arguing that a hydration target should not exist: a number you
    hit by logging rather than by drinking is a number that teaches you to log.
    That objection was correct against a *fixed* target, and `sweat_loss_ml` is
    what answers it. The target here is `BASE_FLUID_ML` plus what the watch
    says was sweated out, so it is higher on a sparring day than on a rest day
    without anybody deciding it should be -- and a day you trained hard and
    drank a normal amount now scores as what it is.

    Urine colour sits beside it and is doing something different: it is the
    only input on this rail that measures what your body did with the water
    rather than what went in. On a cut that distinction is the whole point --
    walking around three pounds down on fluid comes off the scale as progress.

    Caffeine and alcohol are here because they are the cheapest explanations
    for the bar next door. Both land on HRV and on deep sleep, and a week where
    recovery is down and these two are why is a week whose useful sentence is
    about caffeine rather than about training.
    """
    target = BASE_FLUID_ML + (sweat or 0.0)
    darkness = _week_darkness(visits, today)

    return _zone([
        _input(
            "fluid",
            "fluid logged",
            None if fluid is None else min(fluid / target * 100.0, 100.0),
            absent=InputState.not_logged,
            reading=None
            if fluid is None
            else (
                f"{fluid:,.0f} ml a day against {target:,.0f} needed"
                + (f", {sweat:,.0f} sweated" if sweat else "")
            ),
        ),
        _input(
            "urine",
            "urine colour",
            # 1 clear, 4 darker. Scored down from 4 rather than from 5: the
            # scale has four rungs and the worst of them is the worst there is.
            None if darkness is None else _ramp(darkness, 4.0, 1.0),
            absent=InputState.not_logged,
            reading=None
            if darkness is None
            else f"{darkness:.1f} of 4, first of the day excluded",
        ),
        _input(
            "caffeine",
            "caffeine",
            None if caffeine is None else _ramp(caffeine, CAFFEINE_CEILING, 0.0),
            absent=InputState.not_logged,
            reading=None if caffeine is None else f"{caffeine:.1f} drinks a day",
        ),
        _input(
            "alcohol",
            "alcohol",
            None if alcohol is None else _ramp(alcohol, ALCOHOL_CEILING, 0.0),
            absent=InputState.not_logged,
            reading=None if alcohol is None else f"{alcohol:g} in the last seven days",
        ),
    ])


def vo2max_trend(
    points: Sequence[tuple[dt.date, float]], today: dt.date
) -> tuple[float, dt.date, float | None] | None:
    """`(latest, when, change over the quarter)`, or None with no readings.

    Deliberately not gated on the reading being today's. Everything else on
    this tab reads "today or nothing", because a stale sleep score pretending
    to be last night's is a panel you have to date-check -- but VO2max only
    updates after an outdoor run with heart rate, so today-or-nothing would
    blank it about twenty-eight days in thirty. `when` comes back with it so
    the panel can say how old the number is instead of implying it is fresh.

    The change is None until two readings sit far enough apart to subtract.
    This estimate moves by fractions, and a week-on-week difference is noise
    with a sign on it.
    """
    ordered = sorted(points)
    if not ordered:
        return None
    when, latest = ordered[-1]

    start = today - dt.timedelta(days=VO2MAX_WINDOW - 1)
    window = [(date, value) for date, value in ordered if date >= start]
    change = None
    if len(window) >= 2 and (window[-1][0] - window[0][0]).days >= VO2MAX_WINDOW // 3:
        change = latest - window[0][1]
    return latest, when, change
