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

SPELLING_SEED = 1300
"""Between the `easy` and `medium` anchors.

The premise of the feature is a competent speller who has been letting
autocorrect do it -- so starting level with the easy tier would assert the
problem is worse than it is, and starting at the hard one would make the first
week a run of losses that says nothing. Between them, the first few days find
the level quickly at K=96 and then settle.
"""

INVESTIGATE_SEED = 1400
"""Below `[investigate].anchor`, deliberately.

Starting level with the anchor would make the first month read as "about as
good as a competent write-up", which is a claim nothing has evidence for yet.
A hundred below means the early ballots have somewhere to move from, and K=96
finds the real level inside a fortnight either way.
"""

THEOREM_K = 6
"""K for a graded theorem recall, chosen so the delta is exactly +/-3.

Recall is rated against an opponent equal to the current rating, which makes
the expected score exactly 0.5, which makes the delta `k * (score - 0.5)`.
Both halves of the answer land -> score 1.0 -> +3; one -> 0.5 -> 0; neither ->
0.0 -> -3.

Worth stating why it is done this way rather than by writing +/-3 into the row
directly. `rating_after - rating_before == k * (score - expected)` is the
property that lets any rating_event be re-checked by hand years later, and a
fixed delta beside a fabricated k and score would be the one row in the log
where the arithmetic did not hold. Here the numbers are real: the opponent
really is your own rating, the score really is the fraction you got right, and
the +/-3 falls out.
"""


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
    return elo_update_with_k(rating, opponent, score, k_factor(n))


def theorem_score(explained: bool, applied: bool) -> float:
    """Half for the statement, half for a problem you needed it on.

    Two halves rather than a single verdict because they fail independently
    and for different reasons: knowing what Cauchy-Schwarz says and remembering
    the polynomial problem you reached for it on are separate acts of recall,
    and a card where the first lands and the second does not is genuinely a
    draw rather than a loss.
    """
    return (float(explained) + float(applied)) / 2


def elo_update_with_k(rating: int, opponent: int, score: float, k: int) -> int:
    """The same update with K supplied rather than derived from `n`.

    Exists because the time bonus scales K, and the scaled value is what has
    to be written to the row: `delta == k * (score - expected)` is the property
    that makes a rating_event re-checkable years later, and it stops being true
    the moment the K in the column is not the K that was applied.
    """
    return _round_half_up(rating + k * (score - expected_score(rating, opponent)))


# --- how long it took ------------------------------------------------------

TIME_NEUTRAL = 1.5
"""Ratio of time-taken to reference-time that neither adds nor subtracts.

Not 1.0. The reference times in `[math.par_minutes]` are what the tier is
nominally worth, and taking half again as long as that is a perfectly ordinary
solve -- so that is where the multiplier is pinned at exactly 1x, and the
numbers below are all measured from it.
"""

TIME_FLOOR = 0.66
TIME_FLOOR_AT = 3.0
"""The decline bottoms out at 0.66x, and not until three times the reference
time. Between neutral and there it falls off smoothly rather than in steps, so
there is no cliff to notice and no threshold to sit just inside of."""

TIME_CAP = 1.5
"""Roughly the reciprocal of the floor: the fast half of the curve can give
back about as much as the slow half can take. Reached at ~0.76x the reference
time, past which shaving minutes stops paying -- there is a difference between
solving something quickly and having seen it before, and this is where the
curve declines to guess which one happened."""

TIME_EXPONENT = math.log(TIME_FLOOR) / math.log(TIME_NEUTRAL / TIME_FLOOR_AT)
"""Derived, not chosen (~0.5995).

The three numbers above are the ones with opinions in them; this is whatever
exponent makes the curve pass through both of the points they name. Writing it
as a literal would let someone edit `TIME_FLOOR` and silently move where the
floor is actually reached.
"""


def time_multiplier(minutes: int | None, par_minutes: int | None) -> float:
    """How much a solve is worth for having taken `minutes`.

    `(TIME_NEUTRAL / ratio) ** TIME_EXPONENT`, clamped to [floor, cap]. A
    power law rather than a line because the meaningful quantity is the *ratio*
    -- twenty minutes over on a ten-minute problem is a different event from
    twenty minutes over on an hour-long one, and only a ratio says so.

    1.0 when either number is missing. An untimed solve is worth exactly what
    it was worth before this existed, which is what makes the input optional in
    practice rather than merely in the schema: forgetting to start a timer
    costs nothing, and there is never a reason to invent a number.
    """
    if not minutes or not par_minutes:
        return 1.0
    ratio = minutes / par_minutes
    return round(min(TIME_CAP, max(TIME_FLOOR, (TIME_NEUTRAL / ratio) ** TIME_EXPONENT)), 4)


def timed_elo(
    rating: int, opponent: int, score: float, n: int, multiplier: float = 1.0
) -> tuple[int, int]:
    """`(K applied, new rating)` for one result, with the speed bonus folded in.

    The multiplier scales K, so it scales the rating change directly: solving
    at half the reference time moves you 1.5x as far as the same solve
    untimed, which is what "1x bonus at 1.5x time" means once you follow it
    through to the elo.

    It is applied *only when the result gains rating*, and that asymmetry is
    load-bearing. K is symmetric -- it scales the move, not the direction -- so
    an unguarded multiplier would make a fast three-hint solve of an easy
    problem, which is already a net loss because 0.1 is below what you were
    expected to score, lose you half again as much for being fast. Speed can
    amplify what you earned; it cannot deepen a hole.

    A slow solve that still gains is shrunk, which is the other half of the
    same rule and the one the floor exists for.
    """
    k = k_factor(n)
    if multiplier != 1.0 and score > expected_score(rating, opponent):
        k = max(1, _round_half_up(k * multiplier))
    return k, elo_update_with_k(rating, opponent, score, k)


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


PROOF_HINT_COSTS = (0.30, 0.60)
"""What each of a proof problem's two hints costs, cumulatively.

Two hints rather than three, and unequal rather than flat, because they are
different objects. The first is a nudge -- the observation you would have made
in another twenty minutes -- and costs what any hint costs. The second is
written to unlock the argument, so taking it means the mathematics was handed
to you and the writing-up is what is left.

The ladder is therefore 1.0 / 0.70 / 0.10, landing a fully-hinted proof exactly
where a fully-hinted compute problem lands. That is the intended symmetry: 0.1
is "you did the writing", and it is worth more than nothing and not much more.
"""

PARTIAL_SCORE = 0.5
"""A proof with the idea in it and a gap in it, before hints.

Exactly half, matching `theorem_score`'s two halves and `chess_score`'s middle
button -- the same self-report shape used everywhere a result is genuinely not
binary. What keeps it from being a mood is the rubric: the batch writes three
or four checkpoints from the book's own printed solution, and they are on
screen when the button is pressed.
"""


def proof_score(outcome: MathOutcome, hints_revealed: int) -> float:
    """Score for a proof attempt: the three-way self-report, minus hints.

    Clamped at zero, unlike `math_score`, and the difference is not an
    inconsistency. The compute ladder runs its penalty to 0.1 from a base of
    1.0 and can never go under; here the base can be 0.5, and 0.5 minus a
    0.9 giveaway is negative -- a number the `score` column's CHECK forbids and
    which would mean "worse than not doing it", which is not what a partial
    proof with both hints is.
    """
    outcome = MathOutcome(outcome)
    if outcome is MathOutcome.failed:
        return 0.0
    if not 0 <= hints_revealed <= len(PROOF_HINT_COSTS):
        raise ValueError(f"hints_revealed out of range for a proof: {hints_revealed}")
    base = PARTIAL_SCORE if outcome is MathOutcome.partial else 1.0
    return round(max(0.0, base - sum(PROOF_HINT_COSTS[:hints_revealed])), 4)


EARLY_SOLVE_BONUS = 3.0
"""K multiplier for solving a two-day problem on day one, unhinted.

The one place in this app where a result is worth a multiple of itself for
*when* it happened rather than for what it was. Thursday's problem is pitched
at a tier you are not expected to beat and comes with no hints at all, so
finishing it inside the first day is a genuinely rare event, and rating it like
an ordinary solve would say it was not.

Chosen to land near +60. Against a `putnam-early` anchor 250 above the proofs
rating the expected score is 0.19, so a mature K of 24 gives 24 x 3 x 0.81 =
+58; early on the track `K_BONUS_CEILING` caps it and it is +78. That is the
whole justification for the number, and it is a number rather than a fixed
delta because `rating_after - rating_before == k * (score - expected)` has to
keep holding for every row in the log.

Worth being honest about the asymmetry it creates. Elo is symmetric in K but
not in outcome: failing that same problem over two days costs about -5, because
you were expected to lose. So the two-day problem is cheap to fail and
lucrative to solve -- deliberately, since the point is to make hard problems
worth attempting, but it does mean the proofs rating drifts up over time and is
not a skill estimate.
"""

K_BONUS_CEILING = K_SCALE
"""The highest K the early-solve bonus may produce.

The bonus scales K, and K is *already* at its largest when a track is new, so
an uncapped 3.0 would make the first Thursday on the proofs track worth +230 --
"high variance early" turned into a number nobody could read afterwards.

`K_SCALE` because that is the most any result has ever been worth: the bonus
can lift a mature result to what a first-ever result gets, and no further.

Deliberately *not* applied inside `timed_elo` to every multiplier. That was the
first attempt and it was wrong: an area bucket with no history is a fresh track
at K=96, so a global ceiling would have silently dropped the speed bonus from
the area row while the pooled row kept it, and one result would have moved its
two ratings by different amounts. The ceiling belongs to the bonus that needs
it, not to the mechanism.
"""


def early_solve_multiplier(n: int) -> float:
    """`EARLY_SOLVE_BONUS`, capped so the applied K stays under the ceiling."""
    return min(EARLY_SOLVE_BONUS, K_BONUS_CEILING / k_factor(n))


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
