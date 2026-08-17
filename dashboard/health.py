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
