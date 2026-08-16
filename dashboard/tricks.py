"""The trick of the day: what counts as the right answer, and which old trick
comes back.

Pure over its inputs -- no LLM, no database, no clock. The generation lives in
`content`, the storage in `Repository`; everything here is a function of the
arguments it is handed, which is what makes the two decisions in this file
testable without either.

Those two decisions are worth stating up front, because they are where this
feature is easy to get wrong.

**Marking is a string comparison, and it is deliberately not a solver.** The
alternative -- grading eight answers with a model every day -- is the thing
this design exists to avoid, and it would not be more correct so much as more
expensive and less predictable. What makes a comparison good enough is that
the generator states the wanted form in the prompt and lists the other obvious
spellings in `accept`. `normalise` below closes the gap that neither of those
can: whitespace, case, LaTeX packaging, and the difference between `0.5` and
`.50`, which are not different answers by any reading.

**The callback prefers the trick you have gone longest without.** Not the
newest, and not random. A trick met once and never again is the one the deck
exists to catch, and recency is the only input here for the reason
`scoring.draw_weight` gives: it cannot be gamed, because you do not choose it.
"""

from __future__ import annotations

import datetime as dt
import random
import re
import unicodedata

from . import logs, scoring
from .models import domain as d

log = logs.get(__name__)


CHALLENGE = "challenge"
"""The band whose problem is optional and graded by a model rather than
compared. Named once here because three modules test for it."""

# --- what counts as the same answer ----------------------------------------

_MATH_DELIMITERS = re.compile(r"^\$+|\$+$")
_FORMATTING = re.compile(r"\\(?:left|right|,|;|!|quad|qquad|displaystyle|text(?=\{))")
_WHITESPACE = re.compile(r"\s+")

_DASHES = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2014": "-"})
"""Unicode minus and the en/em dashes. A negative answer copied out of a
rendered formula carries U+2212, and one typed on a keyboard carries U+002D;
marking the first wrong would be marking the renderer, not the answer."""


def normalise(text: str) -> str:
    """What two answers have to share to count as the same one.

    Case, surrounding whitespace and *internal* whitespace all go -- unlike
    `spelling.normalise`, which keeps internal spaces because `hors d'oeuvre`
    is spelled with one. Nothing here is spelled with a space: `n(n+1)/2` and
    `n(n + 1)/2` are the same expression written by two people, and the only
    thing distinguishing them is how much of a hurry each was in.

    Then the packaging: `$…$`, the LaTeX spacing macros, and a trailing full
    stop. All of those are how an answer was *presented*, and the generator is
    asked for a bare value precisely because presentation is not the thing
    being tested.

    Deliberately absent: any attempt to normalise the algebra. `\\frac{n}{2}`
    and `n/2` stay different strings here, and are reconciled by the
    generator listing both in `accept` -- because the moment this function
    starts rewriting expressions it is a computer algebra system with no tests,
    and the failure mode is marking a wrong answer right.
    """
    out = unicodedata.normalize("NFC", text).strip().translate(_DASHES)
    out = _MATH_DELIMITERS.sub("", out).strip()
    out = _FORMATTING.sub("", out)
    out = _WHITESPACE.sub("", out)
    return out.rstrip(".").casefold()


_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
TOLERANCE = 1e-9


def _as_number(text: str) -> float | None:
    """The value of a plain decimal, or None if it is not one.

    Restricted to a literal by the regex rather than handed to `float()` and
    guarded with a try: `float` accepts `inf`, `nan` and `1_0`, none of which
    is an answer anybody typed on purpose, and all of which would compare
    equal to something.
    """
    return float(text) if _NUMBER.match(text) else None


def is_correct(answer: str, problem: d.TrickProblem) -> bool:
    """Does `answer` match the canonical answer or one of its alternatives?

    Numerically when both sides are plain decimals, so `0.5`, `.5` and `0.50`
    are one answer rather than three. Textually otherwise, on the normalised
    forms above.

    A blank is always wrong, and is not an error: the panel submits the whole
    set at once, so an unfilled box is an answer of "I don't know".
    """
    typed = normalise(answer)
    if not typed:
        return False
    given = _as_number(typed)
    for candidate in (problem.answer, *problem.accept):
        want = normalise(candidate)
        if typed == want:
            return True
        wanted = _as_number(want)
        if given is not None and wanted is not None and abs(given - wanted) <= TOLERANCE:
            return True
    return False


def marked_problems(problems: list[d.TrickProblem]) -> list[d.TrickProblem]:
    """The set that is string-marked -- everything but the challenge.

    The one place that split is computed. `answers` is positional against this
    list, so a second definition of it somewhere else is a silent off-by-one
    waiting for the day a set arrives with the challenge in the middle.
    """
    return [p for p in problems if p.band != CHALLENGE]


def challenge_of(problems: list[d.TrickProblem]) -> d.TrickProblem | None:
    return next((p for p in problems if p.band == CHALLENGE), None)


def mark(problems: list[d.TrickProblem], answers: list[str]) -> list[bool]:
    """One verdict per markable problem. A short answer list scores the rest
    wrong, and extra answers are ignored -- `spelling.mark`'s rule, for its
    reason: the panel submits the whole set, so a missing box is a blank
    answer rather than a malformed request."""
    return [
        is_correct(answers[i], problem) if i < len(answers) else False
        for i, problem in enumerate(marked_problems(problems))
    ]


# --- which old trick comes back --------------------------------------------


def day_rng(date: dt.date) -> random.Random:
    """A generator seeded by the day.

    So "which trick does today call back to" has one answer however many times
    it is asked. It is asked more than once: the panel builds a prompt naming
    the callback, and the set is stored later against whatever the paste echoes
    back -- and a fresh sample per request would name a different trick on every
    reload, which reads as the page changing its mind.

    Not a substitute for the echo. The paste still resolves the callback by
    name, because a prompt copied yesterday and pasted today has to mean what it
    said when it was copied.
    """
    return random.Random(date.toordinal())


def pick_callback(
    tricks: list[d.Trick],
    last_used: dict[int, dt.date],
    today: dt.date,
    *,
    cooldown: int = 3,
    exponent: float = 1.5,
    rng: random.Random | None = None,
) -> d.Trick | None:
    """The trick one problem in today's set reaches back to, or None.

    `scoring.draw_weight` over how long it has been since each trick was last
    called back -- or since it was added, for one that never has been. Same
    curve as the theorem deck, and it is shared rather than copied because
    "which of these have I gone longest without" is literally the same
    question; only the thing being scheduled differs.

    The two defaults differ from the deck's, and both differences are
    deliberate. The cooldown is three days rather than one, because a set
    already spends most of its problems on today's trick and a callback to
    something from Tuesday is barely a callback. The exponent is gentler,
    because the tail here is not a long-neglected card sitting in a deck of
    fifty -- it is a list that grows by one every day, so an exponent of 2
    would make anything from the last fortnight effectively unreachable and
    spend every callback on week one.

    None when there is nothing eligible: the first day, or a day when every
    trick is inside its cooldown. The set is then one problem shorter, which is
    the honest outcome -- inventing a callback to yesterday would teach the
    reinforcement nothing.
    """
    eligible = [t for t in tricks if t.id is not None]
    if not eligible:
        return None

    def days_since(trick: d.Trick) -> int:
        return (today - last_used.get(trick.id, trick.added)).days

    weights = [
        scoring.draw_weight(days_since(t), cooldown=cooldown, exponent=exponent) for t in eligible
    ]
    if sum(weights) <= 0.0:
        # Every trick is inside its cooldown. `weighted_draw` would fall back to
        # a uniform pick, which is right for a one-card theorem deck (showing
        # *something* beats showing nothing) and wrong here: the callback is a
        # bonus, and the set stands on its own without one.
        return None
    return eligible[scoring.weighted_draw(weights, rng or day_rng(today))]


# --- the shape of a set ----------------------------------------------------


def wanted_bands(easy: int, medium: int, *, callback: bool) -> dict[str, int]:
    """How many problems of each band today's generation should produce.

    The callback replaces a medium problem rather than being added to the set,
    so the day is the same size whether or not there was an old trick to reach
    for. A set that grew by one on every day after the first would be a task
    whose length depended on how long you had been doing it.
    """
    return {
        "easy": max(0, easy),
        "medium": max(0, medium - (1 if callback and medium > 0 else 0)),
        "callback": 1 if callback and medium > 0 else 0,
        CHALLENGE: 1,
    }


def check_bands(problems: list[d.TrickProblem], wanted: dict[str, int]) -> list[str]:
    """What a generated set got wrong about its own shape, as sentences.

    Returned rather than raised so the batch validator can print all of them at
    once -- and so the live path can decide that a set one easy problem short
    is still a usable morning, which is `content`'s call to make and not this
    module's.
    """
    got: dict[str, int] = {}
    for problem in problems:
        got[problem.band] = got.get(problem.band, 0) + 1
    return [
        f"{n} {band} problem(s) wanted, {got.get(band, 0)} given"
        for band, n in wanted.items()
        if got.get(band, 0) != n
    ]
