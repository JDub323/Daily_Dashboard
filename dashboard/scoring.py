"""Elo, scheduling and streaks. Pure functions -- no DB, no network, no clock.

This module is the port of app.js:87-91 to the server, so that a rating
update can happen inside the same transaction as the write. Two browser tabs
can no longer read a rating, both compute from it, and both write back.

The constants stay here rather than in config.toml on purpose: changing them
silently rewrites the meaning of every historical rating_event. They are a
deliberate philosophy, not a knob.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable

from .models.common import ChessOutcome, MathOutcome, ReviewQuality

# --- elo -------------------------------------------------------------------

K_FLOOR = 24
K_SCALE = 96
K_HALFLIFE = 8

MATH_SEED = 1500
CHESS_SEED = 1400


def _round_half_up(x: float) -> int:
    """JS `Math.round` semantics.

    Python's builtin `round` is banker's rounding: `round(0.5)` is 0, while
    `Math.round(0.5)` is 1. Porting the elo update to the server without this
    would shift ratings by a point at every half-integer, which is exactly
    often enough to be confusing and rare enough to look like a real drift.
    Matches JS for negatives too: floor(-1.5 + 0.5) == -1 == Math.round(-1.5).
    """
    return math.floor(x + 0.5)


def k_factor(n: int) -> int:
    """High variance early, stabilising as evidence accumulates.

    K = max(24, 96 / (1 + n/8)), preserved exactly.

    `n` is the count of prior attempts on the track, *including* reconstructed
    ones. Provenance says the recorded rating numbers are untrustworthy; it
    does not say the attempt never happened. K measures how much evidence
    exists, and an attempt count is evidence even when the trajectory that
    was written down for it was interpolated.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return max(K_FLOOR, _round_half_up(K_SCALE / (1 + n / K_HALFLIFE)))


def expected_score(rating: int, opponent: int) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent - rating) / 400))


def elo_update(rating: int, opponent: int, score: float, n: int) -> int:
    """New rating after one result. `n` is the count of prior measured events."""
    return _round_half_up(rating + k_factor(n) * (score - expected_score(rating, opponent)))


# --- outcome -> score ------------------------------------------------------

HINT_PENALTY = 0.30
MAX_HINTS = 3


def math_score(outcome: MathOutcome, hints_revealed: int) -> float:
    """Score for a math attempt.

    Only real hints are counted. The topic/textbook links are free -- they
    used to occupy hints[0], so opening reference material cost the same elo
    as being told the answer.

    No clamp: the penalty runs its full course, giving 1.0 / 0.7 / 0.4 / 0.1
    for zero through three hints. A fully-hinted solve is worth barely more
    than a failure, which is the intent -- hints are expensive.
    """
    if not 0 <= hints_revealed <= MAX_HINTS:
        raise ValueError(f"hints_revealed out of range: {hints_revealed}")
    # Coerce rather than compare with `is`: a StrEnum member is not the same
    # object as an equal plain str, so `"failed" is MathOutcome.failed` is
    # False and a raw string would silently take the solved branch.
    if MathOutcome(outcome) is MathOutcome.failed:
        return 0.0
    return round(1.0 - HINT_PENALTY * hints_revealed, 4)


_CHESS_SCORES = {
    ChessOutcome.clean: 1.0,
    ChessOutcome.messy: 0.4,
    ChessOutcome.failed: 0.0,
}


def chess_score(outcome: ChessOutcome) -> float:
    """Self-reported, matching the three buttons that replaced the board."""
    return _CHESS_SCORES[ChessOutcome(outcome)]


# --- spaced repetition -----------------------------------------------------

EASE_FLOOR = 1.3
EASE_CEILING = 3.0
EASE_STEP = 0.15
HARD_MULTIPLIER = 1.2


def sm2_review(quality: ReviewQuality, interval: int, ease: float) -> tuple[int, float]:
    """The SM-2-lite scheduler, ported from app.js:834-841.

    Returns the next `(interval_days, ease)`. The ease is updated first and
    the new value drives the interval, matching the original.

    The ceiling is new. The old client only clamped the floor, so a card
    answered `easy` eight times drifted past 3.0 -- which the `theorem_card`
    CHECK constraint now rejects outright. Clamping is the conservative
    reading: the intent was a bounded ease, and only one bound was written.
    """
    q = int(ReviewQuality(quality))
    ease = min(EASE_CEILING, max(EASE_FLOOR, ease + (q - 3) * EASE_STEP))
    if q == ReviewQuality.blank:
        return 1, ease
    multiplier = HARD_MULTIPLIER if q == ReviewQuality.hard else ease
    return max(1, _round_half_up(max(1, interval) * multiplier)), ease


# --- streaks ---------------------------------------------------------------


def streak(dates: Iterable[dt.date], today: dt.date) -> int:
    """Consecutive days ending today, or yesterday if today is not done yet.

    `today` is passed in rather than read from the clock: the browser is the
    authority on what day it is, and this function is the reason that matters
    -- computing it from the server's timezone is how a streak silently broke
    at midnight-adjacent hours.
    """
    days = set(dates)
    cursor = today
    if cursor not in days:
        cursor -= dt.timedelta(days=1)  # today not done yet is not a break
    n = 0
    while cursor in days:
        n += 1
        cursor -= dt.timedelta(days=1)
    return n
