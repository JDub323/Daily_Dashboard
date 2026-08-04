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
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from .models.common import Base, Rating, Url
from .models.domain import Link

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
    static: str = "."

    batches: str = "batches"
    """Where the overnight scheduled task drops `<date>.json`. Serving one is
    free; generating one costs an API call. See `dashboard/batch.py`."""

    dossiers: str = "dossiers"
    """Rendered issue dossiers, one markdown file per debate issue.

    An *export*, not storage. `debate_fact` rows are the record; these are
    rewritten from them on every write so `rg` works. Deliberately not
    hand-edited -- problems.json was both tracked and appended to, which is how
    a file ends up churning on every commit while claiming to be editable.
    """

    interests: str = "interests.md"
    """What you want to read, in your own words. Optional -- absent means the
    arXiv candidate stays in arXiv's own order. See `dashboard/interests.py`."""


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


class MathSettings(Base):
    topic_of_week: str = Field(min_length=1)
    """Used only when `rotation` is empty."""

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

    def par_for(self, tier: str) -> int | None:
        """The reference time for a tier, or None if it has no configured one."""
        return self.par_minutes.get(tier)

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


class TheoremSettings(Base):
    """How the day's theorem is drawn. See `scoring.draw_weight`."""

    cooldown_days: int = Field(default=1, ge=0, le=30)
    """Days after an appearance during which a card cannot be drawn at all.
    1 means "not the same theorem two days running", which is the rule that
    makes the draw feel like recall rather than a slideshow."""

    recency_exponent: float = Field(default=2.0, ge=0.0, le=6.0)
    """How sharply the odds favour a long-unseen theorem. 0 is a uniform
    draw over everything off cooldown; 2 is the default steep curve."""


class DebateIssue(Base):
    """One issue: a rating bucket, and the weeks that rate into it.

    The direct counterpart of an entry in `[math.areas]` plus its share of
    `[math].rotation`, with the difference that both live in one table here.
    Math's split exists because its rotation predates its areas; there is no
    reason to repeat it, and keeping them together is what makes adding an
    issue a single append rather than an edit in two places that must agree.
    """

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    """The rating bucket's name, frozen onto every row it produces. Constrained
    because it is also a filename (`dossiers/<slug>.md`) and a rating `area`."""

    name: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    """Substrings that identify a headline as being about this issue. Ranked
    through `interests.Interests`, so they match casefolded anywhere in the
    title -- prefer a phrase to a short word, as `interests.md` says."""

    subareas: list[str] = Field(min_length=1)
    """One week each. Order is the order they come round in."""

    feeds: list[Url] = Field(default_factory=list)
    """Optional RSS specific to this issue. With none, the readings are ranked
    out of the `[news]` pool that is fetched anyway -- which costs nothing and
    is why this can be left empty."""


class DebateAngle(Base):
    """What is being contested today, as opposed to what the week is about."""

    key: str = Field(pattern=r"^[a-z][a-z-]{1,30}$")
    label: str = Field(min_length=1)
    format: str = Field(min_length=1)
    """Names an entry in `[debate.anchors]`. Validated at startup."""

    brief: str = Field(min_length=10)
    """Goes into the prompt verbatim. The one piece of the prompt with an
    opinion in it, which is why it is config rather than code."""


DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
"""Indexed by `date.weekday()`. Config names days; Python numbers them."""


class DebateSettings(Base):
    """The daily debate. See the header comment in config.toml.

    Nothing here costs an API call, which is the whole reason the feature can
    be the size of the math problem without being the size of its bill: the
    argument itself runs in the Claude app on a subscription, and the dashboard
    only assembles a prompt and parses a scorecard.
    """

    issues: list[DebateIssue] = Field(default_factory=list)
    angles: list[DebateAngle] = Field(default_factory=list)
    anchors: dict[str, Rating] = Field(default_factory=dict)

    rotation_start: dt.date | None = None
    order: str = Field(default="interleave", pattern=r"^(interleave|sequential)$")

    reading_day: str = "sunday"
    contra_days: list[str] = Field(default_factory=list)
    contra_bonus: int = Field(default=75, ge=0, le=400)

    min_facts: int = Field(default=2, ge=0, le=20)
    grace_days: int = Field(default=1, ge=0, le=14)

    @property
    def enabled(self) -> bool:
        """No issues means the feature is off, and the panel says so rather
        than erroring. Same courtesy `[math].rotation` gets."""
        return bool(self.issues and self.angles and self.anchors)

    @model_validator(mode="after")
    def _days_are_days(self) -> DebateSettings:
        unknown = [d for d in [self.reading_day, *self.contra_days] if d.lower() not in DAYS]
        if unknown:
            raise ValueError(f"[debate] names {unknown} — not day name(s) in {DAYS}")
        if self.reading_day.lower() in [d.lower() for d in self.contra_days]:
            raise ValueError(f"[debate].reading_day {self.reading_day!r} is also a contra day")
        return self

    @model_validator(mode="after")
    def _angles_name_real_formats(self) -> DebateSettings:
        unknown = sorted({a.format for a in self.angles} - set(self.anchors))
        if unknown:
            raise ValueError(
                f"[[debate.angles]] names format(s) {unknown} with no entry in "
                f"[debate.anchors] ({sorted(self.anchors)})"
            )
        return self

    @model_validator(mode="after")
    def _slugs_are_unique(self) -> DebateSettings:
        slugs = [i.slug for i in self.issues]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            # A duplicate slug would silently merge two issues' ratings and
            # have them overwrite each other's dossier.
            raise ValueError(f"[[debate.issues]] reuses slug(s) {dupes}")
        keys = [a.key for a in self.angles]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"[[debate.angles]] reuses key(s) {dupes}")
        return self

    @model_validator(mode="after")
    def _rotation_is_anchored_to_a_sunday(self) -> DebateSettings:
        if not self.issues:
            return self
        if self.rotation_start is None:
            raise ValueError("[debate] needs a rotation_start date")
        if self.rotation_start.weekday() != SUNDAY:
            raise ValueError(
                f"[debate].rotation_start must be a Sunday; "
                f"{self.rotation_start} is a {self.rotation_start:%A}"
            )
        return self

    @property
    def rotation(self) -> list[tuple[str, str]]:
        """`(issue slug, sub-area)` in the order the weeks visit them.

        Both orderings are in-order traversals of `issues`; neither samples.
        That is the difference from the theorem deck, and it is deliberate --
        a recency-weighted draw is right for recall, where the question is
        "what have I not seen lately", and wrong for a curriculum, where the
        question is "what comes next".

        Interleaved, an issue with four sub-areas simply appears in four of
        the rounds and one with three appears in three, so weight follows the
        list rather than needing a number beside it.
        """
        if self.order == "sequential":
            return [(i.slug, s) for i in self.issues for s in i.subareas]
        widest = max((len(i.subareas) for i in self.issues), default=0)
        return [
            (issue.slug, issue.subareas[round_])
            for round_ in range(widest)
            for issue in self.issues
            if round_ < len(issue.subareas)
        ]

    def week_for(self, date: dt.date) -> tuple[str, str] | None:
        """This week's `(issue slug, sub-area)`, or None when off."""
        rotation = self.rotation
        if not rotation:
            return None
        assert self.rotation_start is not None  # guaranteed by the validator
        weeks = week_index(date) - week_index(self.rotation_start)
        return rotation[weeks % len(rotation)]

    def issue(self, slug: str) -> DebateIssue | None:
        """By slug, or None for an issue that has since left the config.

        None is a state the readers handle rather than an error: results earned
        under a deleted issue stay in `rating_event` -- deleting an issue must
        not be able to corrupt a rating history, only to stop adding to it.
        """
        return next((i for i in self.issues if i.slug == slug), None)

    @property
    def issue_slugs(self) -> list[str]:
        return [i.slug for i in self.issues]

    def is_reading_day(self, date: dt.date) -> bool:
        return DAYS[date.weekday()] == self.reading_day.lower()

    def is_contra_day(self, date: dt.date) -> bool:
        return DAYS[date.weekday()] in [d.lower() for d in self.contra_days]

    def angle_for(self, date: dt.date) -> DebateAngle | None:
        """The angle for a debate day: the nth debate day of the week takes the
        nth angle.

        Counted by walking the week rather than by indexing on `weekday()`,
        because which days are debate days is config -- moving the reading day
        to Wednesday should shuffle the angles with it rather than leave a hole
        where Sunday's angle used to be.
        """
        if not self.angles or self.is_reading_day(date):
            return None
        # Monday-first, matching `weekday()`, so the ladder runs through the
        # week in the order the week is lived rather than from an arbitrary
        # start.
        position = sum(
            0 if DAYS[d] == self.reading_day.lower() else 1 for d in range(date.weekday())
        )
        return self.angles[position % len(self.angles)]

    def anchor_for(self, angle: DebateAngle, *, contra: bool) -> int:
        """The elo opponent: the format's anchor, plus the contra bonus.

        Clamped to the `Rating` range the column enforces, so a large
        `contra_bonus` on the hardest format cannot write a row that fails its
        CHECK at the last moment.
        """
        base = self.anchors.get(angle.format, 1500)
        return max(100, min(4000, base + (self.contra_bonus if contra else 0)))


class MarketSettings(Base):
    tickers: list[str] = Field(default_factory=list)
    cache_seconds: int = Field(default=600, ge=0)


class CalendarSettings(Base):
    command: list[str] = Field(default_factory=lambda: ["gcalcli", "--nocolor"])
    cache_seconds: int = Field(default=300, ge=0)
    timeout_seconds: int = Field(default=60, gt=0)

    @field_validator("command")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("[calendar].command must name an executable")
        return v


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
    debate: DebateSettings = Field(default_factory=DebateSettings)
    theorems: TheoremSettings = Field(default_factory=TheoremSettings)
    markets: MarketSettings = Field(default_factory=MarketSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
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
