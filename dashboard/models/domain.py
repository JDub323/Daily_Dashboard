"""Persisted models. Each of these becomes a SQLite table.

Column vs. JSON rule: real columns for anything filtered, sorted, or
aggregated; JSON for validated leaf blobs that are only ever rendered whole
(`grid`, `per_sentence`, `themes`, `hints`).
"""

from __future__ import annotations

import datetime as dt

from pydantic import Field, computed_field

from .common import (
    Base,
    ChessStatus,
    HealthMetric,
    MathStatus,
    PaperStatus,
    Provenance,
    Rating,
    Score,
    Track,
    Url,
)

# --- ratings ---------------------------------------------------------------


class RatingEvent(Base):
    """One elo-affecting result, append-only, both tracks.

    There is deliberately no `rating` column anywhere else in the schema. The
    current rating is derived -- the last measured `rating_after`, or the seed
    from config if there is none. In state.json, `math.rating` and
    `math.history[-1].rating` were two independent sources of truth that could
    (and did) disagree.

    Storing `rating_before`, `rating_after` and the `k` actually applied makes
    every row self-auditing: the arithmetic can be re-checked years later even
    if the config that produced it has changed.
    """

    id: int | None = None
    track: Track
    date: dt.date
    """Local calendar day, supplied by the browser -- see api.DateBody."""

    ref: str
    """math: the problem source string. chess: the lichess puzzle id."""

    opponent: Rating
    """math: the tier anchor. chess: the puzzle rating. Frozen at write time
    so a later config edit cannot retroactively rewrite history."""

    score: Score
    k: int = Field(ge=1)

    rating_before: Rating | None = None
    rating_after: Rating | None = None
    """NULL exactly when provenance is `reconstructed`."""

    provenance: Provenance = Provenance.measured

    area: str | None = None
    """NULL is the pooled rating -- the one the panel shows and the only kind
    of row that existed before per-area ratings. A non-NULL area is the same
    result rated again inside its bucket, so one solved problem writes two
    rows that move two independent ratings."""

    @computed_field
    @property
    def delta(self) -> int | None:
        if self.rating_before is None or self.rating_after is None:
            return None
        return self.rating_after - self.rating_before


# --- math ------------------------------------------------------------------


class MathProblem(Base):
    """Immutable generated content. Separate from the attempt at it."""

    id: int | None = None

    source: str = Field(min_length=3)
    """UNIQUE. Replaces the unbounded `math.seen` list -- "already served"
    is now `SELECT source FROM math_problem`."""

    tier: str
    """Left as a plain str rather than an enum precisely because the tier
    anchors are moving to config.toml; an enum would freeze in code the one
    thing being moved out of it. Validated against loaded config at the
    boundary."""

    anchor: Rating
    statement: str
    answer: str

    hints: list[str] = Field(min_length=3, max_length=3)
    """Was hint1/hint2/hint3. The client immediately built an array anyway.
    These are the three real hints; the topic/textbook links are not hints and
    are not counted here."""

    verified_url: Url | None = None
    model: str
    generated_at: dt.datetime

    topic: str | None = None
    """The rotation topic this was generated for, recorded at generation time.

    NULL on problems that predate the column. Not inferred from the date:
    `topic_for` reads the rotation out of config, and config is editable, so a
    reconstruction would silently change what a past problem "was about" the
    next time the rotation is reordered."""

    area: str | None = None
    """The rating bucket, resolved from `topic` at generation time and frozen
    here for the same reason `opponent` is frozen on a rating event: config
    can be edited, and a result has to stay in the bucket it was earned in.
    NULL rates pooled-only."""


class MathAttempt(Base):
    date: dt.date
    """PK -- one problem per day."""

    problem_id: int
    hints_revealed: int = Field(ge=0, le=3, default=0)
    status: MathStatus = MathStatus.open
    score: Score | None = None
    """NULL while open. Derived server-side from status + hints_revealed."""

    minutes: int | None = Field(default=None, ge=1, le=1440)
    """How long it took, self-reported and rounded to the minute.

    Optional, and NULL on every attempt that predates the column. Timing
    happens on a phone or a kitchen clock rather than in the dashboard -- there
    is no start button anywhere, deliberately, because a timer running in a tab
    is a timer that is wrong whenever you walk away from it.

    Kept here rather than only in its effect: the effect is folded into the
    rating_event's `k`, and reading a K of 36 back later tells you a bonus
    applied but not what it was measured from. This is the input.
    """


# --- chess -----------------------------------------------------------------


class ChessPuzzle(Base):
    """What survives deleting the inline board: enough to link out, plus the
    rating, which is still the elo opponent."""

    id: str = Field(pattern=r"^[A-Za-z0-9]{4,8}$")
    rating: Rating
    themes: list[str] = Field(default_factory=list)
    url: Url


class ChessAttempt(Base):
    date: dt.date
    puzzle_id: str
    status: ChessStatus = ChessStatus.open
    score: Score | None = None
    """Self-reported via the three buttons; `mistakes` and `hints` were
    derived from board interaction and left the schema with it."""


# --- word ------------------------------------------------------------------


class SentenceGrade(Base):
    ok: bool
    note: str


SENTENCES = 2
"""How many practice sentences the word of the day asks for.

Was five. Five distinct senses is a research task for most words and a
fabrication exercise for the rest, and the fifth box is where the exercise
stopped being about the word.

The bound is *not* enforced on the stored models below, only on the request
that writes them. Rows written when this was five are still in the table, and a
model that insisted on exactly two would make every one of them unreadable --
turning a copy edit into a migration, for a number that is allowed to change
again.
"""

MAX_SENTENCES = 5
"""The widest this has ever been, and so what a stored row may still contain."""


class WordGrade(Base):
    distinct: bool
    verdict: str
    per_sentence: list[SentenceGrade] = Field(min_length=1, max_length=MAX_SENTENCES)


class WordOfDay(Base):
    date: dt.date
    word: str
    pos: str
    definition: str

    pronunciation: str | None = None
    """IPA, in slashes -- `/səˈsʌrəs/`.

    NULL on every word that predates the column, and on any generation where
    the model declined to give one. The point of an uncommon-but-usable word
    is the *usable* half, and a word you cannot say out loud is not one you
    will use.
    """

    respelling: str | None = None
    """The same sound written for someone who does not read IPA --
    `suh-SUR-us`, stressed syllable capitalised.

    Both, rather than one. IPA is exact and unreadable without training;
    a respelling is readable and imprecise about vowels. Dictionaries print
    the pair for this reason, and the pair costs nothing here -- it is two
    fields on a generation that was already being paid for.
    """

    sentences: list[str] = Field(default_factory=list, max_length=MAX_SENTENCES)
    grade: WordGrade | None = None


# --- games -----------------------------------------------------------------


class WordleResult(Base):
    date: dt.date

    puzzle_no: int | None = Field(default=None, ge=1)
    """Normalised to an int on parse. state.json stored "1,861" as a string."""

    guesses: int | None = Field(default=None, ge=1, le=6)
    """NULL means lost (the X/6 case)."""

    won: bool
    grid: list[str] = Field(default_factory=list, max_length=6)


class PokedokuResult(Base):
    date: dt.date
    correct: int = Field(ge=0, le=9)
    score: int | None = Field(default=None, ge=0)
    grid: list[str] = Field(default_factory=list, max_length=3)


# --- theorems --------------------------------------------------------------


class TheoremCard(Base):
    """One theorem in the deck. Carries no schedule.

    A card knows what it is and when you added it; *when it next appears* is
    not a property of the card at all, it is a draw over the whole deck. That
    is the entire difference from the SM-2 shape this replaced, where every
    card walked its own `due` forward and the deck was whatever happened to
    land on today.
    """

    id: int | None = None
    name: str
    statement: str
    topic: str
    added: dt.date
    archived: bool = False


class TheoremDraw(Base):
    """The day's theorem, and whether you say you remembered it.

    Append-only and keyed by date, so it is both the record and the schedule:
    `days since last appeared` is a query over this table, not a stored
    countdown that can drift out of step with what you were actually shown.
    """

    date: dt.date
    card_id: int

    recalled: bool | None = None
    """NULL until the self-check. Deliberately not defaulted to False -- "you
    have not answered yet" and "you missed it" are different days, and only
    the second one is a data point."""


# --- papers ----------------------------------------------------------------


class Paper(Base):
    """One table replacing current/queue/archive -- three states of one
    entity, previously moved between three lists by hand-rolled splicing."""

    id: int | None = None
    title: str
    link: Url
    summary: str | None = None
    published: dt.date | None = None

    status: PaperStatus
    surfaced_on: dt.date | None = None
    """Set when arXiv served this as the day's triage candidate."""

    added: dt.date
    queue_pos: int | None = Field(default=None, ge=0)
    """Ordering within the queue; NULL when not queued."""


# --- misc daily ------------------------------------------------------------


class HealthEntry(Base):
    metric: HealthMetric
    date: dt.date
    value: float = Field(gt=0)
    """UNIQUE(metric, date) -- the find-then-mutate-else-append dance becomes
    a single UPSERT."""


class DayLog(Base):
    date: dt.date
    text: str = Field(max_length=2000)


class DayCompletion(Base):
    date: dt.date
    done: int = Field(ge=0)
    total: int = Field(gt=0)

    @computed_field
    @property
    def pct(self) -> float:
        """Computed, not stored. state.json persisted `pct` alongside
        done/total, so the three could disagree."""
        return self.done / self.total


# --- topic cache -----------------------------------------------------------


class Link(Base):
    title: str
    url: Url


class TopicInfo(Base):
    topic: str
    textbook: Link
    theorems: list[Link] = Field(min_length=1, max_length=8)
