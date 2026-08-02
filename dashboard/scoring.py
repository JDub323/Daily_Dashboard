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
import random
from collections.abc import Iterable, Sequence

from .models.common import ChessOutcome, MathOutcome

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


LATE_MULTIPLIER = 0.4
"""What a late submission keeps of what it would have been worth on the day.

0.4 is not a free parameter: it is `math_score(solved, 2)`, so being a day late
costs exactly what two hints cost. That is the whole rule, and stating it as
the two-hint score rather than as "0.4" is what keeps the two in step if the
hint penalty ever changes.
"""

LATE_WINDOW_DAYS = 14
"""How far back a lapse can still be submitted.

After this a lapse is final. The archive still shows the problem and its
answer -- reading it back is the point of an archive -- but the rating stops
being re-litigable, so a month-old figure means what it said it meant.
"""


def late_math_score(outcome: MathOutcome, hints_revealed: int) -> float:
    """Score for a lapsed day submitted late.

    The whole on-time ladder, scaled: 0.40 / 0.28 / 0.16 / 0.04 for zero
    through three hints. Multiplying rather than subtracting is what the
    "hints as a percentage of the late score" rule amounts to -- each hint
    costs 30% of what the attempt is still worth, so it comes to 0.12 off 0.40
    rather than the full 0.30 off 1.0.

    Subtracting in full would have run a three-hint late solve to
    `0.4 - 0.9 = -0.5`, and `math_score` deliberately has no clamp. Scaling
    keeps the ladder inside the score column's 0..1 CHECK, keeps the ordering,
    and keeps the shape: a fully-hinted late solve is worth barely more than
    nothing, for the same reason it is on the day.

    A late `failed` scores 0.0, exactly what the lapse already charged -- so
    honestly reporting that you tried and could not do it costs nothing beyond
    what ghosting it already did, and still converts the day into evidence.
    """
    return round(LATE_MULTIPLIER * math_score(outcome, hints_revealed), 4)


LAPSE_SCORE = 0.0
"""What a drawn-but-never-marked day scores.

Zero, against the anchor of the problem that was actually drawn -- so ghosting
a day costs exactly what admitting you failed it costs, never less. Any gentler
figure would make silence cheaper than an honest "couldn't solve", which is the
one incentive this must not create.

The lapse only fires for a day that was *opened*: no problem drawn, no
penalty. A week away during finals therefore costs nothing, while abandoning a
problem you asked for costs a full result. That asymmetry is the point, and it
is why this is a constant here rather than a decay curve -- there is nothing to
tune once "you started it" is the trigger.
"""


_CHESS_SCORES = {
    ChessOutcome.clean: 1.0,
    ChessOutcome.messy: 0.4,
    ChessOutcome.failed: 0.0,
}


def chess_score(outcome: ChessOutcome) -> float:
    """Self-reported, matching the three buttons that replaced the board."""
    return _CHESS_SCORES[ChessOutcome(outcome)]


# --- the theorem draw ------------------------------------------------------
"""Which theorem comes up today.

This replaced SM-2. SM-2 asked "when is this card next due?" and answered per
card, which meant the day's workload was whatever happened to land on it: zero
cards on a Tuesday, six on a Wednesday. The deck here is hand-curated -- you
add a theorem the moment you meet it in a problem -- and the question is the
other one: given that exactly one theorem comes up today, which?

The answer is a weighted sample over the deck where the only input is how long
it has been since the theorem last appeared. Nothing about how well you did
enters the weight. That is a decision, not an omission: the self-check is
there so you know whether you knew it, and the moment a miss shortens the
interval you have an incentive to grade generously to keep your deck quiet.
Recency alone cannot be gamed, because you do not choose it.

Unlike the elo constants above these two are overridable from `[theorems]` in
config.toml. The rule this module's header states -- constants live here
because changing them rewrites history -- does not bind: a draw is sampled
fresh every day and reinterprets nothing that already happened. The values
below are the defaults, not the only permitted answer.
"""

COOLDOWN_DAYS = 1
RECENCY_EXPONENT = 2.0


def draw_weight(
    days_since: int, *, cooldown: int = COOLDOWN_DAYS, exponent: float = RECENCY_EXPONENT
) -> float:
    """Relative likelihood of a card that last appeared `days_since` days ago.

    Zero through the cooldown -- a theorem you saw yesterday is one you still
    remember, and drawing it tests nothing. Past that the weight grows as
    `(days_since - cooldown) ** exponent`, so the gap is measured from the
    first day the card is eligible at all rather than from the day it
    appeared. Without that subtraction the first eligible day already carries
    a weight of `2 ** exponent` and the cliff at the cooldown edge is a jump
    rather than a start.

    At the default exponent of 2 a theorem unseen for a month is roughly 840x
    likelier than one unseen for two days. That is intentionally steep: with a
    deck of any size a linear weight leaves the long tail effectively never
    drawn.
    """
    if days_since <= cooldown:
        return 0.0
    return float(days_since - cooldown) ** exponent


def weighted_draw(weights: Sequence[float], rng: random.Random | None = None) -> int:
    """Index sampled proportional to `weights`, or -1 for an empty sequence.

    Falls back to a uniform draw when every weight is zero. That is reachable
    with a one-card deck drawn yesterday, and the honest behaviour is to show
    the card again rather than to show nothing -- a deck of one has no other
    answer, and refusing to draw would read as the feature being broken.
    """
    if not weights:
        return -1
    rng = rng or random.Random()
    if sum(weights) <= 0.0:
        return rng.randrange(len(weights))
    return rng.choices(range(len(weights)), weights=weights, k=1)[0]


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
