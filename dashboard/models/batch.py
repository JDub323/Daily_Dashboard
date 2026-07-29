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
