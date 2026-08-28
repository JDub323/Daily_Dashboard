"""The overnight batch file: what the 3am scheduled task hands the dashboard.

Its own module rather than a shape inside `batch.py`, because two unrelated
modules need it and neither should import the other: `content` reads a batch
when serving the day's problem, and `batch` writes and validates one from the
CLI. `batch` imports `content`; `content` must not import `batch`.

`extra="forbid"` comes from `Base` and is doing real work here. This file is
produced by a language model at 3am with nobody watching, and an invented
field is the cheapest possible signal that the prompt and the schema have
drifted apart. Better a loud failure into the log at 3am than a half-populated
panel at 8am.
"""

from __future__ import annotations

import datetime as dt

from pydantic import Field

from .common import Base, Url


class BatchProblem(Base):
    """One rung of the ladder.

    Mirrors `domain.MathProblem` minus everything the dashboard fills in
    itself -- `anchor` is looked up from the tier, `model` and `generated_at`
    describe the generation rather than the problem, and `id` is the
    database's to assign.
    """

    source: str = Field(min_length=3)
    """The competition and problem number, e.g. "Putnam 1998 B3". UNIQUE in
    the database, so this is also the duplicate check."""

    tier: str
    """Validated against the configured anchors on import, not here -- the
    tier table lives in config.toml and this model must not freeze a copy."""

    statement: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    hints: list[str] = Field(min_length=3, max_length=3)

    verified_url: Url | None = None
    """Where the generator confirmed the problem. Optional in the type so a
    batch is still usable without it, required in the prompt so its absence
    is visible."""


class DailyBatch(Base):
    """One day's generated problems, keyed by the day they are for.

    Holds a *ladder* rather than a single problem. Batch generation is billed
    to a subscription rather than per token, so over-generating is close to
    free -- and it buys back the two things the 3am task cannot know:

    1. The current elo, which decides the tier. The task proposes a spread;
       the dashboard picks the rung nearest the live rating at serve time.
    2. Whether a problem has been seen. `source` is UNIQUE, so a repeat is
       caught either way -- but with a ladder it falls through to the next
       rung instead of falling back to a paid generation.
    """

    date: dt.date
    topic: str | None = None
    generated_at: dt.datetime | None = None
    problems: list[BatchProblem] = Field(min_length=1, max_length=5)


# --- the trick -------------------------------------------------------------
#
# The other file the 3am task writes, here rather than in `tricks.py` for the
# reason above: `tricks` reads it when serving and `batch` writes and validates
# it, and neither may import the other.


class BatchTrickProblem(Base):
    """One problem of a generated trick set.

    Mirrors `domain.TrickProblem` exactly, and exists separately for the reason
    `BatchProblem` does: this is the wire format a model writes at 3am with
    nobody watching, and `extra="forbid"` on it is the cheapest signal that the
    prompt and the schema have drifted apart.
    """

    band: str = Field(pattern=r"^(easy|medium|callback|challenge)$")
    prompt: str = Field(min_length=3)
    answer: str = Field(min_length=1)
    accept: list[str] = Field(default_factory=list, max_length=6)
    solution: str = Field(default="", max_length=2000)


class TrickSet(Base):
    """One day's trick and its problems, from the overnight task.

    Unlike `DailyBatch` this is not a ladder to choose a rung from -- every
    problem in it is served. The batch buys the same thing it buys for the math
    problem and nothing more: the generation happens on a subscription instead
    of on the API, so the day is free.
    """

    date: dt.date
    topic: str = Field(min_length=1)
    """Echoed back so `--validate-trick` can check the generator answered the
    week it was asked about rather than a different one."""

    name: str = Field(min_length=2, max_length=120)
    statement: str = Field(min_length=3)
    idea: str = Field(default="", max_length=1200)
    kind: str = Field(default="trick", pattern=r"^(trick|technique)$")

    callback: str | None = None
    """The name of the older trick the `callback` problem is on, echoed back so
    the import can find the card it belongs to. None when the context named no
    trick to reach back to."""

    problems: list[BatchTrickProblem] = Field(min_length=2, max_length=20)
    generated_at: dt.datetime | None = None


