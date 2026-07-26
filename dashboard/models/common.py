"""Shared scalars, enums, and the model base.

Everything persisted or accepted over HTTP derives from `Base`. The strictness
here is deliberate: `extra="forbid"` is what turns a malformed LLM generation
into a loud ValidationError at the boundary, instead of a half-populated dict
that only fails three layers later in the browser.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
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


class MathStatus(StrEnum):
    open = "open"
    solved = "solved"
    failed = "failed"


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


class ReviewQuality(IntEnum):
    """SM-2 recall grades, matching the four buttons."""

    blank = 1
    hard = 2
    good = 3
    easy = 4
