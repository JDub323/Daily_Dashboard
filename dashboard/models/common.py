"""Shared scalars, enums, and the model base.

Everything persisted or accepted over HTTP derives from `Base`. The strictness
here is deliberate: `extra="forbid"` is what turns a malformed LLM generation
into a loud ValidationError at the boundary, instead of a half-populated dict
that only fails three layers later in the browser.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- scalars ---------------------------------------------------------------

Score = Annotated[float, Field(ge=0.0, le=1.0)]
Rating = Annotated[int, Field(ge=100, le=4000)]

# HttpUrl admits only http/https. That is the whole fix for the paper-queue
# hole -- a pasted `javascript:` URL can no longer reach the DOM, because it
# cannot get past the request model. Escaping stops being something the
# renderer has to remember.
Url = HttpUrl


# --- enums -----------------------------------------------------------------


class Track(StrEnum):
    """The two elo-rated activities. One rating_event table serves both."""

    math = "math"
    chess = "chess"


class Provenance(StrEnum):
    measured = "measured"
    """Produced by a real attempt; the rating trajectory is trustworthy."""

    reconstructed = "reconstructed"
    """Imported from state.json, where the rating was interpolated by the old
    _backfill_ratings rather than measured. The date/score/ref on these rows
    are genuine; rating_before/rating_after are not, and are stored NULL."""

    lapsed = "lapsed"
    """Auto-scored: the problem was drawn and the day ended with no mark.

    The rating change is real and the numbers are trustworthy, so unlike
    `reconstructed` these rows carry ratings. What they are not is *evidence
    about skill* -- nobody attempted anything -- so they are excluded from the
    K-factor's `n` and from `success_rate`. A day you ghosted should cost you
    rating without also making the estimate more confident."""

    late = "late"
    """A lapsed day submitted afterwards, scored at `late_math_score`.

    Evidence about skill -- you did the problem -- so unlike `lapsed` it counts
    toward the K-factor's `n`. It is excluded from `success_rate`, which
    thresholds at score > 0.5: a late score is deflated by design and tops out
    at 0.40, so every late solve would read as a failure and catching up would
    look like getting worse."""

    unlapsed = "unlapsed"
    """Bookkeeping: hands back exactly what a lapse took, before its `late`
    result is applied.

    Submitting late replaces the lapse rather than stacking on it, and the log
    is append-only -- so the lapse row stays and this row cancels it. Excluded
    from `n` and from `success_rate` alike: nothing was attempted here, and
    `score`/`k` on the row are the lapse's own values carried over only because
    the columns are NOT NULL. Nothing reads them."""


class MathStatus(StrEnum):
    open = "open"
    solved = "solved"
    failed = "failed"

    lapsed = "lapsed"
    """Drawn but never marked, and the day is over. Scored as a failure --
    only reachable through `Repository.lapse_math`, never from the client."""


class ChessStatus(StrEnum):
    open = "open"
    done = "done"


class MathOutcome(StrEnum):
    """What the client reports. The score is derived server-side."""

    solved = "solved"
    failed = "failed"


class ChessOutcome(StrEnum):
    """The three self-report buttons that replaced the inline board."""

    clean = "clean"
    messy = "messy"
    failed = "failed"


class PaperStatus(StrEnum):
    surfaced = "surfaced"
    """Today's triage candidate, not yet acted on."""

    queued = "queued"
    read = "read"
    skipped = "skipped"

    cleared = "cleared"
    """Was in the queue, since read out of it."""


class HealthMetric(StrEnum):
    weight = "weight"
    sleep = "sleep"


class Game(StrEnum):
    wordle = "wordle"
    pokedoku = "pokedoku"


# Deliberately absent: `ReviewQuality`, the four SM-2 grades. The theorem
# self-check is one bit -- you remembered it or you did not -- because nothing
# reads the grade any more. A four-way scale whose only consumer is a
# histogram is a scale you will stop answering honestly.
