"""`config.toml` -> validated settings.

Every constant that used to require editing Python lives in config.toml now.
The models here are the same `Base` as everything else, so `extra="forbid"`
applies: a misspelled key in the TOML is a startup ValidationError naming the
field, not a silently-ignored line.

Deliberately *not* moved out of source: the elo constants in `scoring.py`.
Changing those rewrites the meaning of every historical rating_event, which
makes them a philosophy rather than a knob.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from .models.common import Base, DayMode, Rating, Url
from .models.domain import Link
from .todo import RESERVED as _RESERVED_ALIASES

DEFAULT_CONFIG_NAME = "config.toml"
SUNDAY = 6  # datetime.date.weekday(), where Monday is 0


def project_root() -> Path:
    """The repo root -- the parent of this package."""
    return Path(__file__).resolve().parent.parent


# --- sections --------------------------------------------------------------


class ServerSettings(Base):
    host: str = "127.0.0.1"
    port: int = Field(default=8502, ge=1, le=65535)
    open_browser: bool = True
    log_level: str = "INFO"
    log_file: str | None = "dashboard.log"


class PathSettings(Base):
    db: str = "dashboard.db"
    problems: str = "problems.json"
    metrics: str = "metrics.jsonl"

    static: str = "web"
    """The directory mounted as a catch-all at `/`.

    Was `"."`, which means *the project root* -- and the mount serves whatever
    is beneath it, so that default published `dashboard.db`, `.env`,
    `config.toml` and `metrics.jsonl` to anything that could reach the port.
    Nothing was actually running on it (config.toml has said `web` since the
    rewrite), but a default is what a config gets when it omits the line, and
    this one was the wrong shape of mistake to leave lying around next to a
    file called positions.csv.
    """

    batches: str = "batches"
    """Where the overnight scheduled task drops `<date>.json`. Serving one is
    free; generating one costs an API call. See `dashboard/batch.py`."""

    investigations: str = "investigations"
    """Where the write-ups live, one markdown file per topic.

    The one directory in this config that is a *source* rather than an export.
    These are opened in a real editor, they are what you typed, and the
    database holds a snapshot of one taken when it was submitted rather than
    the other way round. Nothing here is ever regenerated, so nothing here may
    be overwritten by the server -- see `investigate.ensure_file`.
    """

    scratch: str = "scratch"
    """Where the recall boxes' buffer goes while it is being typed in.

    A working directory, not a record: what a graded recall is, is the row the
    grading wrote, and these files are only ever the two textareas in a form an
    editor can open. Gitignored for that reason -- a day's file stops mattering
    the moment the answer is submitted, and the answer is in the database.
    """

    spelling: str = "spelling.json"
    """The hand-curated spelling pool. Read-only to the server, and tracked in
    git -- unlike `problems.json`, nothing ever appends to this, so it stays
    hand-editable without churning."""

    interests: str = "interests.md"
    """What you want to read, in your own words. Optional -- absent means the
    arXiv candidate stays in arXiv's own order. See `dashboard/interests.py`."""

    training: str = "training.toml"
    """The training plan -- what a day asks of you, as opposed to what you did.

    Read-only to the server and hand-edited, like `spelling.json`. Optional: an
    absent file means no camp is on, and every panel renders that as silence
    rather than as an error. See `dashboard/trainplan.py`.
    """

    usage: str = "usage.json"
    """How often each phone screen is opened, per day -- `{date: {screen: n}}`.

    Written by `POST /api/usage` and by nothing else, and read by whoever is
    deciding what earns a place in the phone's bar. A file rather than a table
    because it is a statistic about the app, not a fact about a day: nothing
    in the dashboard reads it back. See `dashboard/usage.py`."""

    positions: str = "positions.csv"
    """The brokerage positions export. Gitignored, and the one file here whose
    absence is the normal case -- a fresh clone has none and the panel says so.

    Validated below as *not* living under `static`, which would publish it."""

    proofbook: str = "proofbook.json"
    """The proof textbook's contents, parsed once by `dashboard.proofbook`.

    Tracked in git; the PDF it describes is not. Optional in practice -- a
    missing index means the proof days cannot name a page range, which the
    batch reports at 3am rather than the dashboard failing at 8."""


class ModelPricing(Base):
    input: float = Field(gt=0)
    output: float = Field(gt=0)


class LlmSettings(Base):
    model: str
    max_tokens: int = Field(default=16000, gt=0)
    timeout_seconds: int = Field(default=300, gt=0)
    """Silence budget for a call that can run web searches.

    Not a budget for the whole call: it is handed to `urlopen`, which makes it
    the socket timeout, which applies per blocking read. The metrics log proves
    the difference -- a math generation with four searches took 847s wall and
    completed fine under this "300 second timeout", because it never went 300s
    without sending anything.

    Left generous deliberately. A search-backed generation is the expensive
    call, the gaps while the server runs a search are the longest in the app,
    and tripping this one early throws away real money.
    """

    quick_timeout_seconds: int = Field(default=60, gt=0)
    """The same budget for a call with no tools, which cannot be slow.

    Word grading, the word itself, theorem cards and topic links have never
    taken more than 12.2s across every line in metrics.jsonl. Sharing the
    search-sized 300s meant a grading that stalled sat there for five minutes
    before admitting it -- which is the failure this exists to stop. 60s is
    roughly five times the slowest one ever observed.
    """

    max_retries: int = Field(default=2, ge=0)
    """Extra attempts after a transport failure that cost nothing. See
    `llm._with_retries` for why "cost nothing" is the condition."""

    retry_backoff_seconds: float = Field(default=1.0, gt=0)
    """First backoff; doubles per attempt."""

    max_pause_continuations: int = Field(default=6, ge=1)
    web_search_max_uses: int = Field(default=4, ge=0)
    web_search_cost_usd: float = Field(default=0.01, ge=0)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)

    models: dict[str, str] = Field(default_factory=dict)
    """Per-kind model overrides, `kind -> model`. See `model_for`."""

    # Fallback when a model is missing from [llm.pricing]: the standard
    # non-intro rate. Over- rather than under-reporting cost is the safe
    # direction for a log whose whole purpose is noticing spend.
    FALLBACK_PRICING: ClassVar[ModelPricing] = ModelPricing(input=3e-6, output=15e-6)

    def price(self, model: str) -> ModelPricing:
        return self.pricing.get(model, self.FALLBACK_PRICING)

    def model_for(self, kind: str) -> str:
        """The model for one kind of generation, defaulting to `model`.

        Not every call needs the same model. Deciding whether an explanation of
        Cauchy-Schwarz says the right thing is a short judgement against a
        statement already in hand, which is a much smaller job than writing a
        verified Putnam problem -- and it happens every day, so the difference
        compounds. Keyed by the same `kind` string that labels the row in
        metrics.jsonl, so the cost log attributes it correctly with no second
        vocabulary to keep in step.
        """
        return self.models.get(kind, self.model)


def week_index(date: dt.date) -> int:
    """Weeks since the proleptic epoch, incrementing on Sunday.

    `date(1, 1, 1)` is a Monday, so Sundays are exactly the ordinals divisible
    by seven and floor division steps on Sunday. Using the ordinal rather than
    `isocalendar().week` is what keeps the count continuous: ISO week numbers
    reset each January, so a rotation indexed by them would skip an entry at
    every new year.
    """
    return date.toordinal() // 7


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""`date.weekday()` order, and the keys `[math.week]` must use."""


class ProofSettings(Base):
    """Where the proof days get their problems, and which book that is."""

    enabled: bool = True

    pdf: str = "textbooks/Putnam_and_beyond.pdf"
    """Read by the overnight generator, never by the server. Relative to the
    project root, so one path works from both Windows and WSL."""

    tiers: dict[str, list[str]] = Field(default_factory=dict)
    """[math.week] mode -> the tiers that mode may ask for.

    A separate table from `[math.anchors]` and pointing into it: Wednesday's
    problem and Thursday's come from the same book and the same section, and
    the only thing that distinguishes them is how hard the rung is allowed to
    be. Validated against the anchors below, since a tier named here that does
    not exist there is a mode that can never be generated for.
    """

    sections: dict[str, str] = Field(default_factory=dict)
    """rotation topic -> the section of the book it is drawn from, as a number
    like `2.2.4`.

    Hand-written and hand-checked against the contents page, which is exactly
    what makes it worth having: the automatic fallback in `proofbook` matched
    "polynomial roots, Vieta, and symmetric functions" to *Equations with
    Functions as Unknowns*, a real section about something else. A topic absent
    from this table falls back to that matcher and then to its area's chapter,
    so the table may be partial and a new rotation entry is not an error.
    """


class ReadingSettings(Base):
    """Sunday: read, and do many easy problems. No rating, no timer."""

    enabled: bool = True

    segments: int = Field(default=3, ge=1, le=6)
    segment_minutes: int = Field(default=30, ge=5, le=120)
    """Three half-hours generated, one or two expected.

    The over-generation is the point and it is not waste: how much of a week's
    topic is already familiar varies enormously by topic, so the plan offers
    more than the day asks for and you take the segments that are new. Which
    ones you skipped, and why, go back into the next generation -- otherwise
    every month opens with the same introduction.
    """


class MathSettings(Base):
    topic_of_week: str = Field(min_length=1)
    """Used only when `rotation` is empty."""

    week: dict[str, DayMode] = Field(default_factory=dict)
    """weekday name -> what that day asks for. Empty means every day is
    `compute`, which is what this was before the week had a shape."""

    proof: ProofSettings = Field(default_factory=ProofSettings)
    reading: ReadingSettings = Field(default_factory=ReadingSettings)

    rotation: list[str] = Field(default_factory=list)
    rotation_start: dt.date | None = None
    anchors: dict[str, Rating]

    areas: dict[str, str] = Field(default_factory=dict)
    """topic -> the broad area it belongs to, the bucket a per-area rating uses.

    Lives beside the rotation because it is metadata about that same list, and
    is validated against it below: a rotation entry with no area would be a
    topic whose results land nowhere, which is the kind of silent hole this
    config's validators exist to catch at startup.

    The buckets are deliberately coarse. Rating each of the 25 topics
    separately sounds better and is worse -- a topic comes round for one week
    per ~6-month cycle, so its `n` never grows and `k_factor` never leaves 96.
    Six areas see roughly four times the traffic each.
    """

    par_minutes: dict[str, int] = Field(default_factory=dict)
    """tier -> the reference time a solve is measured against, in minutes.

    Keyed by tier for the same reason `anchors` is: difficulty is already
    expressed as a tier, and a second scale would be a second thing to keep in
    step. Empty turns the time bonus off entirely -- `time_multiplier` returns
    1.0 for a missing reference, so every solve scores exactly as it did
    before the feature existed.
    """

    @field_validator("anchors")
    @classmethod
    def _non_empty(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            raise ValueError("[math.anchors] must define at least one tier")
        return v

    @model_validator(mode="after")
    def _par_minutes_are_real_tiers(self) -> MathSettings:
        """A par for a tier that does not exist is silently dead config.

        Checked in this direction only. Every key must name a tier, but not
        every tier needs a key: a new tier added to `[math.anchors]` should
        start out untimed rather than take the whole config down at startup,
        and an untimed tier is a supported state, not a broken one.
        """
        unknown = [t for t in self.par_minutes if t not in self.anchors]
        if unknown:
            raise ValueError(
                f"[math.par_minutes] names {unknown} — not tier(s) in [math.anchors] "
                f"({sorted(self.anchors)})"
            )
        if any(m <= 0 for m in self.par_minutes.values()):
            raise ValueError("[math.par_minutes] values must be positive")
        return self

    def par_for(self, tier: str, mode: DayMode | None = None) -> int | None:
        """The reference time for a tier, or None if it has no configured one.

        None on every proof day, whatever the tier's par says. A reference time
        is the thing the speed bonus is measured against, and there is no
        honest reference time for a proof: the two-day problem is *designed* to
        be sat with, and pricing it by the clock would pay for hurrying through
        the one day of the week that exists for not hurrying.

        Enforced here rather than by leaving the proof tiers out of
        `[math.par_minutes]`, because those same tiers are reachable on a
        compute day -- an `olympiad-easy` rung on a Tuesday is timed, and the
        same tier read off a Thursday is not. The mode is what decides, so the
        mode has to be the argument.
        """
        if mode is not None and mode.is_proof:
            return None
        return self.par_minutes.get(tier)

    @model_validator(mode="after")
    def _week_names_real_days(self) -> MathSettings:
        if not self.week:
            return self
        unknown = sorted(set(self.week) - set(WEEKDAYS))
        if unknown:
            raise ValueError(f"[math.week] names {unknown}; expected {list(WEEKDAYS)}")
        missing = [d for d in WEEKDAYS if d not in self.week]
        if missing:
            raise ValueError(f"[math.week] is missing {missing}")
        return self

    @model_validator(mode="after")
    def _proof_tiers_are_real_tiers(self) -> MathSettings:
        """Every tier a mode may ask for has to have an anchor to rate against.

        Checked in both directions here, unlike `[math.par_minutes]`: a mode
        with no tiers at all cannot be generated for, and a mode that is in the
        week but not in this table would fail at 3am with nothing to say.
        """
        for mode, tiers in self.proof.tiers.items():
            if mode not in set(DayMode):
                raise ValueError(f"[math.proof.tiers] names mode {mode!r}, which is not a day mode")
            unknown = [t for t in tiers if t not in self.anchors]
            if unknown:
                raise ValueError(
                    f"[math.proof.tiers].{mode} names {unknown} — not tier(s) in "
                    f"[math.anchors] ({sorted(self.anchors)})"
                )
        wanted = {m for m in self.week.values() if m.is_proof}
        absent = sorted(m.value for m in wanted if not self.proof.tiers.get(m.value))
        if self.proof.enabled and absent:
            raise ValueError(
                f"[math.proof.tiers] has no tiers for {absent}, which [math.week] uses"
            )
        return self

    @model_validator(mode="after")
    def _proof_sections_are_real_topics(self) -> MathSettings:
        """A section override for a topic that is not in the rotation is dead
        config -- almost always a typo in a topic string, which is long enough
        to typo and never loud enough to notice."""
        if not self.rotation:
            return self
        unknown = [t for t in self.proof.sections if t not in self.rotation]
        if unknown:
            raise ValueError(
                f"[math.proof.sections] names {len(unknown)} topic(s) not in the rotation: "
                f"{unknown[0]!r} and others"
                if len(unknown) > 1
                else f"[math.proof.sections] names {unknown[0]!r}, which is not in the rotation"
            )
        return self

    def mode_for(self, date: dt.date) -> DayMode:
        """What this day asks for. `compute` when the week has no shape."""
        if not self.week:
            return DayMode.compute
        return self.week[WEEKDAYS[date.weekday()]]

    def section_for(self, date: dt.date) -> str | None:
        """The proof-textbook section this week is pinned to, if one is."""
        return self.proof.sections.get(self.topic_for(date))

    def is_second_day(self, date: dt.date) -> bool:
        """Is this the second day of a two-day problem?

        True when this day and the one before it are both `proof-long` -- which
        is what a two-day problem *is*, expressed as the pair of entries in
        `[math.week]` that make it one. Derived rather than configured, so
        moving the pair to Friday/Saturday is one table edit and nothing here
        needs to know it happened.
        """
        return (
            self.mode_for(date) is DayMode.proof_long
            and self.mode_for(date - dt.timedelta(days=1)) is DayMode.proof_long
        )

    def carries_over(self, date: dt.date) -> bool:
        """Is this day one of a two-day problem?

        The mirror of `is_second_day`, and what tells the lapse reconciler to
        carry an unmarked day rather than charge it a zero.
        """
        return (
            self.mode_for(date) is DayMode.proof_long
            and self.mode_for(date + dt.timedelta(days=1)) is DayMode.proof_long
        )

    def tiers_for(self, mode: DayMode) -> list[str]:
        """The tiers a mode may ask for, easiest first."""
        tiers = self.proof.tiers.get(mode.value, [])
        return sorted(tiers, key=lambda t: self.anchors[t])

    @model_validator(mode="after")
    def _rotation_is_anchored_to_a_sunday(self) -> MathSettings:
        if not self.rotation:
            return self
        if self.rotation_start is None:
            raise ValueError("[math].rotation needs a rotation_start date")
        if self.rotation_start.weekday() != SUNDAY:
            raise ValueError(
                f"[math].rotation_start must be a Sunday; "
                f"{self.rotation_start} is a {self.rotation_start:%A}"
            )
        return self

    @model_validator(mode="after")
    def _every_rotation_topic_has_an_area(self) -> MathSettings:
        if not self.areas:
            return self  # per-area ratings are off; the pooled one still works
        missing = [t for t in self.rotation if t not in self.areas]
        if missing:
            raise ValueError(f"[math.areas] is missing {len(missing)}: {missing[0]!r} and others")
        return self

    @property
    def area_names(self) -> list[str]:
        """The distinct areas, ordered by how many rotation slots each holds --
        which is how much of the Putnam it represents, and so the order the
        breakdown should read in."""
        counts: dict[str, int] = {}
        for topic in self.rotation:
            area = self.areas.get(topic)
            if area:
                counts[area] = counts.get(area, 0) + 1
        return sorted(counts, key=lambda a: (-counts[a], a))

    def area_for(self, date: dt.date) -> str | None:
        """The rating bucket for the week containing `date`."""
        return self.areas.get(self.topic_for(date))

    def area_for_topic(self, topic: str) -> str | None:
        """The rating bucket for a theorem card's free-text topic.

        A card's `topic` was written by whichever generation created it and is
        unconstrained, so it is matched against the known areas rather than
        trusted to be one. Equality is not enough: a real card in the deck
        reads "Combinatorics / Generating Functions (Complex Analysis
        applications in competition mathematics)", which is recognisably
        combinatorics and equal to nothing.

        So: exact match, then containment, then the rotation topics. The
        containment pass takes the longest area name first, because "number
        theory" contains no shorter area but "algebra" sits inside plenty of
        longer phrases, and the more specific name is the better answer.

        None when nothing matches at all -- the honest answer to "which bucket
        is this" when nothing knows. The caller prefers the grader's own
        classification and only falls back to this.
        """
        wanted = topic.strip().casefold()
        areas = self.area_names
        for area in areas:
            if area.casefold() == wanted:
                return area
        for area in sorted(areas, key=len, reverse=True):
            if area.casefold() in wanted:
                return area
        for rotation_topic, area in self.areas.items():
            if rotation_topic.casefold() in wanted:
                return area
        return None

    def topic_for(self, date: dt.date) -> str:
        """The topic for the week containing `date`.

        `date` comes from the browser, like every other date in the app, so
        the topic turns over at the user's midnight rather than the server's.
        """
        if not self.rotation:
            return self.topic_of_week
        assert self.rotation_start is not None  # guaranteed by the validator
        weeks = week_index(date) - week_index(self.rotation_start)
        return self.rotation[weeks % len(self.rotation)]

    @property
    def default_tier(self) -> str:
        """Fallback when the LLM returns a tier that is not in the table.

        The median anchor, not a hardcoded "aime-late": the tier names are
        config now, so a name baked into code would be a dangling reference
        the moment the table is edited.
        """
        return sorted(self.anchors, key=lambda t: self.anchors[t])[len(self.anchors) // 2]


class SpellingSettings(Base):
    """The spelling bee. Five words a day, spoken rather than shown.

    Costs nothing per day: the words come from the pool in `spelling.json`.
    Anchors are per tier and fixed here for the reason `[math.anchors]` is --
    difficulty is a tier name in the pool file, never a number, so editing the
    pool cannot drift the rating scale.
    """

    words_per_day: int = Field(default=5, ge=1, le=20)
    ladder: list[str] = Field(default_factory=list)
    """Tier per slot, easiest first. Validated against `anchors` at startup."""

    anchors: dict[str, Rating] = Field(default_factory=dict)

    relearn_days: int = Field(default=7, ge=0, le=365)
    """How long after missing a word before it can come back -- and once it
    can, it is *preferred*. Not stopping misspelling the words you misspell is
    the one way this feature could be busywork."""

    cooldown_days: int = Field(default=30, ge=0, le=3650)
    """How long before a word you got right may come round again."""

    max_repeats: int = Field(default=1, ge=0, le=20)
    """How many rungs of one round may ask a word you have had before.

    A cap on the relearn rule above, not a replacement for it. Missed words are
    still preferred and still come back -- one of them a day, rather than as
    many as have come due, which is how a round ended up being mostly words you
    had already seen. Best effort by construction: a rung whose tier has no
    unseen word left repeats regardless, because a short day is worse than a
    repeated one."""

    @property
    def enabled(self) -> bool:
        return bool(self.ladder and self.anchors)

    @model_validator(mode="after")
    def _ladder_names_real_tiers(self) -> SpellingSettings:
        unknown = sorted(set(self.ladder) - set(self.anchors))
        if unknown:
            raise ValueError(
                f"[spelling].ladder names tier(s) {unknown} with no entry in "
                f"[spelling.anchors] ({sorted(self.anchors)})"
            )
        if self.ladder and len(self.ladder) != self.words_per_day:
            # Two ways to say how many words a day, and they must agree --
            # otherwise the count silently becomes whichever the draw reads.
            raise ValueError(
                f"[spelling].ladder has {len(self.ladder)} entries but "
                f"words_per_day is {self.words_per_day}"
            )
        return self

    def anchor_for(self, tier: str) -> int:
        """The elo opponent for a word of this tier, or the middle of the
        table for a tier the pool names and the config does not."""
        if tier in self.anchors:
            return self.anchors[tier]
        by_value = sorted(self.anchors.values())
        return by_value[len(by_value) // 2] if by_value else 1400


class TrickSettings(Base):
    """The trick of the day: one identity or technique from the week's topic,
    taught by a set of problems.

    The only feature here that pays for a generation *and* has no free source
    to fall back on -- there is no curated pool of tricks the way there is for
    math problems and spelling words. So the overnight batch matters more here
    than anywhere else: `./batch.sh --trick-context` produces the same set on a
    subscription, and the live path exists for the mornings the task did not
    run. See `dashboard/batch.py`.
    """

    easy: int = Field(default=4, ge=0, le=10)
    medium: int = Field(default=4, ge=0, le=10)
    """The two graded rungs. The optional challenge is always exactly one --
    it is the problem that asks for more than applying the trick, and there is
    no such thing as two of those in one morning."""

    anchors: dict[str, Rating] = Field(default_factory=dict)
    """band -> elo opponent. `easy` and `medium` are averaged over the set the
    day actually asked, so a day with a callback in it is rated against what it
    contained; `challenge` is its own event and its own anchor.

    Fixed here rather than derived from `[math.anchors]` because these are a
    different scale entirely: an `easy` trick problem is a one-line application
    of an identity, which is not the same claim about you as an AMC problem.
    """

    callback_cooldown_days: int = Field(default=3, ge=0, le=60)
    """How recent a trick has to be before it stops being worth calling back.
    Three, because a set already spends most of its problems on today's trick
    and reaching back to Tuesday is barely reaching back."""

    callback_exponent: float = Field(default=1.5, ge=0.0, le=6.0)
    """How sharply the callback favours a long-unvisited trick. Gentler than
    `[theorems].recency_exponent` on purpose -- see `tricks.pick_callback`."""

    @property
    def enabled(self) -> bool:
        """No anchors means the feature is off, and the panel says so rather
        than erroring. The courtesy `[spelling]` and `[tricks]` already get."""
        return bool(self.anchors) and (self.easy + self.medium) > 0

    @model_validator(mode="after")
    def _bands_have_anchors(self) -> TrickSettings:
        if not self.anchors:
            return self
        missing = [b for b in ("easy", "medium", "challenge") if b not in self.anchors]
        if missing:
            raise ValueError(
                f"[tricks.anchors] is missing {missing} — every band a set can "
                f"contain needs an anchor, or its results rate against nothing"
            )
        return self

    def anchor_for(self, band: str) -> int:
        """The elo opponent for one band. A `callback` rates as a medium: it is
        a medium-difficulty problem, and the only thing different about it is
        which trick it is on."""
        if band == "callback":
            band = "medium"
        if band in self.anchors:
            return self.anchors[band]
        by_value = sorted(self.anchors.values())
        return by_value[len(by_value) // 2] if by_value else 1400

    def set_anchor(self, bands: list[str]) -> int:
        """The elo opponent for a whole marked set: the mean of its bands.

        Computed from the bands the set *actually contained* rather than from
        the configured counts, so a generation that came back one medium short
        is rated as the easier day it was. Clamped to the `Rating` range the
        column enforces, and defaulting to the medium anchor for an empty set
        -- which cannot be marked anyway, but must not divide by zero on the
        way to finding that out.
        """
        if not bands:
            return self.anchor_for("medium")
        mean = sum(self.anchor_for(b) for b in bands) / len(bands)
        return max(100, min(4000, math.floor(mean + 0.5)))


class TheoremSettings(Base):
    """How the day's theorem is drawn. See `scoring.draw_weight`."""

    cooldown_days: int = Field(default=1, ge=0, le=30)
    """Days after an appearance during which a card cannot be drawn at all.
    1 means "not the same theorem two days running", which is the rule that
    makes the draw feel like recall rather than a slideshow."""

    recency_exponent: float = Field(default=2.0, ge=0.0, le=6.0)
    """How sharply the odds favour a long-unseen theorem. 0 is a uniform
    draw over everything off cooldown; 2 is the default steep curve."""


class ArgumentPattern(Base):
    """One hand-written card for the argument deck.

    Hand-written for the reason `[topic_library]` is: a card that costs nothing
    and is wrong only in ways you can see beats one regenerated at a fraction
    of a cent a time. `statement` is prose and deliberately not LaTeX -- this
    deck shares a table with the theorem cards and nothing else about them.
    """

    name: str = Field(min_length=2, max_length=120)
    topic: str = Field(min_length=2, max_length=60)
    statement: str = Field(min_length=20, max_length=1000)


class EditorSettings(Base):
    """The embedded editor: which one, and how it is told to behave.

    Its own section rather than `[investigate]`'s, which is where these three
    started. Two panels fork an editor now -- the write-up on the politics tab
    and the theorem recall boxes on the math one -- and a setting that governs
    the math tab out of the politics feature's config is a setting nobody finds
    when they go looking for it.
    """

    editor: list[str] = Field(default_factory=lambda: ["nvim", "vim"])
    """Editors to try, in order. The first one on PATH wins.

    A list rather than a string because the fallback is the whole point: this
    spawns a real terminal editor and a machine without `nvim` should get
    `vim` and a working panel, not a stack trace about a missing binary.
    """

    commands: list[str] = Field(default_factory=lambda: ["nnoremap U <C-r>"])
    """Ex commands passed as `-c`, so they run *after* your own config.

    The default one is here because of a browser, not a preference. Chrome
    reserves Ctrl-R and will not let a page have it, so vim's redo is
    unreachable inside the panel however the terminal is wired -- `U` is the
    nearest free key (its own "undo line" is the least-missed command in vim).
    Ctrl-W is reserved the same way and window commands cannot be recovered at
    all; that is the cost of an editor in a browser tab, and the reason the
    file stays openable from a real terminal.

    Emptying this list turns the whole thing off and gives you vim untouched.
    """

    scrollback: int = Field(default=1000, ge=0, le=100_000)
    """Lines the terminal keeps. Only reached with `:!` output -- a buffer
    scrolls inside the editor, not into the terminal's own history."""


class InvestigateSettings(Base):
    """The write-up: what it is rated against, and where it is filed."""

    anchor: int = Field(default=1500, ge=100, le=4000)
    """The opponent rating every investigation is scored against.

    One number, not a table per format, because an investigation has no
    format and no assigned side -- the topics differ in subject, not in
    difficulty, so there is nothing for a second anchor to mean. Fixed, so the
    scale cannot drift when the grading model changes.
    """

    folders: list[str] = Field(default_factory=list)
    """The subject areas write-ups are filed under, seeded.

    A starting list rather than an empty directory, because the cold start is
    exactly when a classifier forks near-duplicates: asked to invent a folder
    with nothing to choose from, it produces "US-China trade" in September and
    "China trade policy" in October, and now there are two. It may still add
    beyond this list -- the seed is a floor, not a whitelist.
    """

    patterns: list[ArgumentPattern] = Field(default_factory=list)
    """The starter argument deck, seeded at startup. Empty is fine -- the deck
    then starts bare and fills from the ballot and the add box.

    Here rather than in a table of its own because the deck rates into this
    track's `argument-patterns` bucket: the panel is its own card, but the
    number it moves is this one."""

    split_at: int = Field(default=25, ge=2, le=1000)
    """How many write-ups a folder may hold before new ones are filed into a
    sub-folder of it.

    Only ever affects the file being placed now. Nothing already written is
    moved -- a bulk re-file of a directory of your own prose, decided by a
    model, is not something this should do without being asked each time.
    """


class MarketSettings(Base):
    tickers: list[str] = Field(default_factory=list)
    cache_seconds: int = Field(default=600, ge=0)

    include_holdings: bool = True
    """Append the symbols from the positions file to the movements list.

    On by decision, not by default-because-it-is-easier. It is the one part of
    this feature that *reduces* privacy: with it on, the tickers you hold are
    on screen whether or not the portfolio is unlocked, so the Hello prompt is
    protecting share counts and dollar values rather than the fact that you own
    NVDA. Turning it off leaves the lock covering the whole thing.
    """


class PortfolioSettings(Base):
    """The fingerprint-locked holdings panel.

    `unlock_seconds` is enforced server-side -- `webauthn.Unlocks` stops
    serving the data when it expires, and the panel's countdown is a display of
    that rather than the thing enforcing it.
    """

    enabled: bool = True

    unlock_seconds: int = Field(default=300, ge=30, le=3600)
    """How long an unlock lasts. Five minutes by request."""

    rp_id: str | None = None
    """WebAuthn relying-party ID. Derived from `[server].host` when unset, and
    that derivation is the useful one -- see `webauthn.for_settings`, and note
    that an IP address cannot be an rp_id at all."""


class GarminSettings(Base):
    """The Forerunner sync. Off until the watch is on the wrist.

    Everything here is a switch rather than a credential: `GARMIN_EMAIL` and
    `GARMIN_PASSWORD` live in `.env`, which is not in the repository, and the
    password is read exactly once by `dashboard.garmin login` to mint the token
    store. Nothing in `config.toml` should ever need to be redacted.
    """

    enabled: bool = False
    """Turn on once `dashboard.garmin login` has written a token store. Off by
    default so a fresh clone -- or the tests -- never reach for the network."""

    auto: bool = True
    """Sync in the background when the dashboard is opened.

    This is the whole answer to "I don't want to record any of those": there is
    no scheduled job because the machine is asleep at 4am, and the first boot
    of the morning is a trigger that is actually there when you need it.
    """

    days: int = Field(default=3, ge=1, le=28)
    """How far back each run re-pulls. Garmin revises last night's sleep for
    hours after you get up, so a one-day window would freeze the first draft.
    Re-pulling costs nothing -- the write is an upsert that declines to touch
    a hand correction."""

    min_interval_minutes: int = Field(default=60, ge=1)
    """Floor between runs nobody asked for -- the pulse's own thirty-second
    beat, on a dashboard sitting open on a second screen. An hour, because
    these numbers change once a night and an unattended tab has no reason to
    go and look more often than that."""

    focus_interval_minutes: int = Field(default=5, ge=1)
    """Floor between runs somebody did ask for: opening the dashboard, or
    turning back to its window.

    A separate floor because it answers a different question. The hour above is
    "how often should a tab nobody is facing go and look"; this is "you have
    just walked up to the laptop wanting this morning's numbers", and applying
    an hour to that is what made the watch take two reloads and a wait to
    appear. Still floored, so a morning of alt-tabbing is at most twelve trips
    rather than one per glance."""

    token_dir: str = "~/.garminconnect"
    """Where the OAuth tokens the login command writes are kept. Outside the
    repository on purpose."""


class CalendarSettings(Base):
    command: list[str] = Field(default_factory=lambda: ["gcalcli", "--nocolor"])
    cache_seconds: int = Field(default=300, ge=0)
    timeout_seconds: int = Field(default=60, gt=0)

    list_cache_seconds: int = Field(default=300, ge=0)
    """How long the account's calendar list is trusted.

    Was a day, on the reasoning that calendars are made every few months. That
    was the wrong quantity to optimise: `gcalcli list` measures 0.27s, and this
    number is not a performance budget but *the delay before a calendar you
    just created can be typed into*. Five minutes because it also bounds how
    long a deleted calendar stays on offer.
    """

    warm_on_start: bool = True
    """Fit the quick-add classifier in a background thread at boot.

    Off under test, where it would run the real `gcalcli` against the real
    account on every app the suite constructs -- see the `settings` fixture.
    """

    history_days: int = Field(default=1095, ge=0)
    history_days_ahead: int = Field(default=180, ge=0)
    history_cache_seconds: int = Field(default=86_400, ge=0)
    """The classifier's training window. Three years back, half a year
    forward: next semester's timetable is already filed and is the most
    representative data on the account."""

    write_timeout_seconds: int = Field(default=20, gt=0)
    """Shorter than the read timeout, on purpose.

    A read happens on a timer and nobody is waiting for it. A write happens
    because someone pressed Enter and is watching -- 60s of a spinner is
    indistinguishable from the app being broken, which is what the stdin bug
    looked like from the outside for as long as it existed.
    """

    default_calendar: str = "Special Events"
    """Where an event goes when the classifier has nothing useful to say.

    Named rather than inferred: "the first writable one" is alphabetical
    accident, and this is the calendar whose contents are already the most
    varied, so a wrong guess landing here is the least surprising.
    """

    week_starts_on: int = Field(default=0, ge=0, le=6)
    """0 = Monday, matching `date.weekday()`. 6 is Sunday, as Google draws it."""

    @field_validator("command")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("[calendar].command must name an executable")
        return v


class Subject(Base):
    """One alias, and what it stands for."""

    name: str = Field(min_length=1)
    calendar: str | None = None
    """Where this subject's work is filed, overriding everything else.

    Left unset for most subjects: the default target is already Homework, and
    naming it on every entry would be repeating the default forty times. Set it
    where a subject genuinely lives somewhere else.
    """


class TodoSettings(Base):
    default_calendar: str = "Homework"
    """Where a dated item goes when nothing more specific applies.

    Not `[calendar].default_calendar`, which answers a different question --
    that one is the classifier's shrug, for lines that are events. This is the
    prior, and the prior is that a thing with a due date is homework. It is
    right about nineteen lines in twenty, which is a better basis for a default
    than a model's fallback.
    """

    todo_calendars: list[str] = Field(default_factory=lambda: ["Homework", "Tests"])
    """Which calendars mean "this is work, and it can be finished".

    Writing to one of these creates a tracked item with a state; writing to any
    other calendar is an ordinary event and creates nothing. Deciding it by the
    *target* rather than by a mode switch is what lets one box do both -- the
    picker was already on screen and already says where the thing is going.
    """

    recur_weeks: int = Field(default=12, ge=1, le=52)
    """How far an unbounded "every tuesday" runs. A semester is about fifteen."""

    subjects: dict[str, Subject] = Field(default_factory=dict)

    @field_validator("subjects")
    @classmethod
    def _keys_are_typeable(cls, v: dict[str, Subject]) -> dict[str, Subject]:
        """Lower-cased, one word, and never a word the date parser wants.

        The last rule is the one worth having. An alias keyed `sat` or `may`
        expands inside a line that `calparse` is about to read for a date, and
        the failure is silent in both directions -- either the subject eats the
        day or the day eats the subject. Refusing at load is the only point
        where this is cheap to notice.
        """
        out: dict[str, Subject] = {}
        for key, subject in v.items():
            folded = key.strip().lower()
            if not folded or re.search(r"\s", folded):
                raise ValueError(f"[todo.subjects] key {key!r} must be a single word")
            if folded in _RESERVED_ALIASES:
                raise ValueError(
                    f"[todo.subjects] key {key!r} is a word the date parser reads;"
                    " pick another abbreviation"
                )
            out[folded] = subject
        return out


class Feed(Base):
    cat: str
    url: Url


class Quota(Base):
    cat: str
    n: int = Field(ge=0)


class NewsSettings(Base):
    cache_seconds: int = Field(default=900, ge=0)
    proxy_prefix: str = ""
    paywalled: list[str] = Field(default_factory=list)
    quotas: list[Quota] = Field(default_factory=list)
    feeds: list[Feed] = Field(default_factory=list)

    repeat_window_days: int = Field(default=30, ge=0, le=365)
    """How long a headline stays suppressed after it has been shown.

    30 rather than forever. A URL that surfaced during a broken fetch would
    otherwise be invisible permanently, and a story genuinely re-reported
    months later is news again. 0 disables the filter without removing the
    table.
    """


class ArxivSettings(Base):
    cache_seconds: int = Field(default=3600, ge=0)
    max_results: int = Field(default=20, ge=1)
    query: str


class TopicEntry(Base):
    """A hand-curated entry in [topic_library]. Same shape as `TopicInfo`
    minus the topic name, which is the table key."""

    textbook: Link
    theorems: list[Link] = Field(min_length=1, max_length=8)


# --- root ------------------------------------------------------------------


class Settings(Base):
    server: ServerSettings = Field(default_factory=ServerSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    llm: LlmSettings
    math: MathSettings
    editor: EditorSettings = Field(default_factory=EditorSettings)
    investigate: InvestigateSettings = Field(default_factory=InvestigateSettings)
    spelling: SpellingSettings = Field(default_factory=SpellingSettings)
    tricks: TrickSettings = Field(default_factory=TrickSettings)
    theorems: TheoremSettings = Field(default_factory=TheoremSettings)
    markets: MarketSettings = Field(default_factory=MarketSettings)
    portfolio: PortfolioSettings = Field(default_factory=PortfolioSettings)
    garmin: GarminSettings = Field(default_factory=GarminSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    todo: TodoSettings = Field(default_factory=TodoSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    arxiv: ArxivSettings
    topic_library: dict[str, TopicEntry] = Field(default_factory=dict)

    root: Path = Field(default_factory=project_root, exclude=True)
    """Directory the relative paths above resolve against.

    Always the project root, never the config file's directory. Pointing
    `DASHBOARD_CONFIG` at a scratch copy is meant to change settings, not to
    silently relocate `dashboard.db` next to that copy and open an empty one.
    A config that genuinely wants its data elsewhere says so with an absolute
    path in `[paths]`.
    """

    def path(self, name: str) -> Path:
        """Resolve one of the [paths] entries against `root`."""
        return (self.root / getattr(self.paths, name)).resolve()

    @model_validator(mode="after")
    def _positions_are_not_published(self) -> Settings:
        """The positions file must not sit inside the static root.

        `[paths].static` is mounted as a catch-all at `/`, so anything beneath
        it is served to anyone who can reach the port -- no token, no Hello, no
        panel involved. Every other guard in this feature would still be in
        place and completely bypassed by a URL.

        Caught at startup rather than by a check at read time, because the way
        this goes wrong is a `[paths]` edit made months from now for an
        unrelated reason, and the symptom is silence.
        """
        static, positions = self.path("static"), self.path("positions")
        if positions == static or static in positions.parents:
            raise ValueError(
                f"[paths].positions ({positions}) is inside [paths].static ({static}), "
                "where the web server would publish it unauthenticated. Move it outside."
            )
        return self


# --- loading ---------------------------------------------------------------


def load_settings(path: str | Path | None = None) -> Settings:
    """Read and validate config.toml. `DASHBOARD_CONFIG` overrides the path."""
    if path is None:
        path = os.environ.get("DASHBOARD_CONFIG") or project_root() / DEFAULT_CONFIG_NAME
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    return Settings(**data)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Process-wide settings. Cached because config.toml is read once at
    startup; `settings.cache_clear()` re-reads it (used by the tests)."""
    return load_settings()


# --- .env ------------------------------------------------------------------


def load_env(path: str | Path | None = None) -> None:
    """Minimal dotenv. Existing environment wins, so a real export overrides
    the file rather than the other way round."""
    p = Path(path or project_root() / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
