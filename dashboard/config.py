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
    max_pause_continuations: int = Field(default=6, ge=1)
    web_search_max_uses: int = Field(default=4, ge=0)
    web_search_cost_usd: float = Field(default=0.01, ge=0)
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)

    # Fallback when a model is missing from [llm.pricing]: the standard
    # non-intro rate. Over- rather than under-reporting cost is the safe
    # direction for a log whose whole purpose is noticing spend.
    FALLBACK_PRICING: ClassVar[ModelPricing] = ModelPricing(input=3e-6, output=15e-6)

    def price(self, model: str) -> ModelPricing:
        return self.pricing.get(model, self.FALLBACK_PRICING)


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
