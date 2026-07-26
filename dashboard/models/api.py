"""Request and response bodies.

Two things happen at this layer that could not happen before:

1. POST handlers stop indexing raw dicts. `body["word"]`, `body["sentences"]`
   and `body["text"]` each turned a malformed request into a KeyError and a
   500 with no field information; they become 422s naming the field.

2. Every body that concerns a day carries a `date` from the browser. The
   server used to stamp `dt.date.today()` in WSL's timezone while the client
   compared against its own local date, so any skew silently broke every
   "done today" check. The browser is now the sole authority on "today".

`POST /api/state` and `POST /api/state/merge` have no successor here. With
one endpoint per write and per-column UPDATE, whole-subtree merging has
nothing left to do.
"""

from __future__ import annotations

import datetime as dt

from pydantic import Field, model_validator

from .common import (
    Base,
    ChessOutcome,
    HealthMetric,
    MathOutcome,
    Rating,
    ReviewQuality,
    Score,
    Url,
)
from .domain import (
    DayCompletion,
    MathProblem,
    Paper,
    RatingEvent,
    TheoremCard,
    TopicInfo,
    WordGrade,
    WordOfDay,
)

# --- requests --------------------------------------------------------------


class DateBody(Base):
    """Base for anything that happens on a particular local day."""

    date: dt.date


class MathNewRequest(DateBody):
    pass


class MathHintRequest(DateBody):
    pass


class MathFinishRequest(DateBody):
    outcome: MathOutcome
    """The client reports what happened; the server derives the score from
    hints_revealed and applies the elo update in one transaction."""


class ChessNewRequest(DateBody):
    pass


class ChessFinishRequest(DateBody):
    outcome: ChessOutcome


class WordTodayRequest(DateBody):
    pass


class WordSentencesRequest(DateBody):
    """Autosave of the five boxes, without grading."""

    sentences: list[str] = Field(max_length=5)


class WordCheckRequest(DateBody):
    word: str = Field(min_length=1)
    sentences: list[str] = Field(min_length=5, max_length=5)
    """The "need all five" rule moves out of a client-side `if` and into the
    contract. Previously a short list was a 500."""


class CalendarAddRequest(Base):
    text: str = Field(min_length=1, max_length=500)


class PaperTriageRequest(DateBody):
    mark: str = Field(pattern=r"^(read|queued|skipped)$")


class PaperQueueRequest(DateBody):
    url: Url
    """Was a free-form string interpolated into an href. `javascript:` in the
    paste box executed; it now fails validation."""

    title: str | None = None


class PaperQueueActionRequest(DateBody):
    paper_id: int
    action: str = Field(pattern=r"^(up|read|drop)$")


class TheoremNewRequest(DateBody):
    pass


class TheoremReviewRequest(DateBody):
    card_id: int
    quality: ReviewQuality


class WordleLogRequest(DateBody):
    """Parsed client-side from the share text, posted as structured data."""

    puzzle_no: int | None = Field(default=None, ge=1)
    guesses: int | None = Field(default=None, ge=1, le=6)
    won: bool
    grid: list[str] = Field(default_factory=list, max_length=6)


class PokedokuLogRequest(DateBody):
    correct: int = Field(ge=0, le=9)
    score: int | None = Field(default=None, ge=0)
    grid: list[str] = Field(default_factory=list, max_length=3)


class HealthLogRequest(DateBody):
    metric: HealthMetric
    value: float = Field(gt=0)


class DayLogRequest(DateBody):
    text: str = Field(max_length=2000)


class CompletionRequest(DateBody):
    done: int = Field(ge=0)
    total: int = Field(gt=0)

    @model_validator(mode="after")
    def _done_within_total(self) -> CompletionRequest:
        """Expressed here as well as in the table's CHECK.

        Without it a client that miscounts its own chips gets a 500 and a
        traceback from a constraint violation, when what happened is a bad
        request that should name the field.
        """
        if self.done > self.total:
            raise ValueError(f"done ({self.done}) cannot exceed total ({self.total})")
        return self


# --- responses -------------------------------------------------------------


class TrackSummary(Base):
    rating: Rating
    """Derived: the last measured rating_after, else the seed."""

    k_next: int
    """K that the next result will use -- surfaced because the UI shows it."""

    measured_count: int
    success_rate: int | None = None
    history: list[RatingEvent] = Field(default_factory=list)


class MathPanel(Base):
    summary: TrackSummary
    problem: MathProblem | None = None
    hints_revealed: int = 0
    status: str = "open"
    score: Score | None = None
    hints_visible: list[str] = Field(default_factory=list)
    """Only the hints actually revealed. The unrevealed ones are not sent --
    previously the whole hint list shipped to the client and was merely not
    rendered."""


class ChessPanel(Base):
    summary: TrackSummary
    puzzle_id: str | None = None
    puzzle_rating: Rating | None = None
    puzzle_url: Url | None = None
    themes: list[str] = Field(default_factory=list)
    status: str = "open"
    score: Score | None = None


class GamesPanel(Base):
    wordle_today: WordleLogRequest | None = None
    pokedoku_today: PokedokuLogRequest | None = None
    wordle_streak: int = 0
    pokedoku_streak: int = 0
    wordle_average: float | None = None


class PapersPanel(Base):
    surfaced: Paper | None = None
    queue: list[Paper] = Field(default_factory=list)
    cleared_count: int = 0


class TheoremsPanel(Base):
    due: list[TheoremCard] = Field(default_factory=list)
    deck_size: int = 0


class HealthSeries(Base):
    metric: HealthMetric
    points: list[tuple[dt.date, float]] = Field(default_factory=list)


class DashboardState(Base):
    """The boot payload. A response projection assembled from many tables --
    no longer the storage format, which is what let the old one be
    full-replaced by a stale tab."""

    date: dt.date
    math: MathPanel
    chess: ChessPanel
    word: WordOfDay | None = None
    games: GamesPanel
    theorems: TheoremsPanel
    papers: PapersPanel
    health: list[HealthSeries] = Field(default_factory=list)
    daylog: str | None = None
    completion: list[DayCompletion] = Field(default_factory=list)
    perfect_day_streak: int = 0


class ConfigResponse(Base):
    topic: str
    tickers: list[str]


class WordCheckResponse(Base):
    grade: WordGrade


class TopicResponse(TopicInfo):
    pass


# --- feeds -----------------------------------------------------------------


class NewsItem(Base):
    title: str
    link: Url
    cat: str


class MarketRow(Base):
    ticker: str
    price: float | None = None
    change_pct: float | None = None
    spark: list[float] = Field(default_factory=list)
    error: str | None = None


class CalendarEvent(Base):
    date: dt.date
    start: str | None = None
    end: str | None = None
    title: str


class CalendarResponse(Base):
    today: list[CalendarEvent] = Field(default_factory=list)
    week: list[CalendarEvent] = Field(default_factory=list)


# --- streaming -------------------------------------------------------------


class MathStatusEvent(Base):
    """One ndjson line from POST /api/math/new while generation runs."""

    status: str


class MathDoneEvent(Base):
    problem: MathProblem
    hints_revealed: int = 0
