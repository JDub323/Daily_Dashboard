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


class MathLateRequest(DateBody):
    """`date` is today, as everywhere; `target` is the lapsed day being
    submitted. Both, because the window is measured between them and only the
    browser knows which day today is."""

    target: dt.date
    outcome: MathOutcome


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


class TheoremAddRequest(DateBody):
    """ "add Vandermonde's" -- a name, not a statement.

    The point of the deck is that adding to it costs one phrase typed while
    the problem you just met it in is still on screen. Writing the statement
    out is what stopped the old deck from growing, so the LLM does it.
    """

    prompt: str = Field(min_length=2, max_length=200)


class TheoremRecallRequest(DateBody):
    recalled: bool


class TheoremArchiveRequest(DateBody):
    card_id: int
    archived: bool = True


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


class AreaRating(Base):
    """One bucket of the math rating breakdown."""

    area: str
    rating: Rating
    events: int
    """Measured results in this area. Zero means `rating` is the pooled
    rating standing in, not something earned -- the panel dims those."""


class MathPanel(Base):
    summary: TrackSummary
    areas: list[AreaRating] = Field(default_factory=list)
    """Per-area ratings, strongest first. The headline in `summary` stays the
    pooled rating; this is the breakdown under it, and the number today's
    problem was pitched against."""

    target_area: str | None = None
    """The area this week's topic rates into -- the one driving difficulty."""

    problem: MathProblem | None = None
    hints_revealed: int = 0
    status: str = "open"
    score: Score | None = None
    hints_visible: list[str] = Field(default_factory=list)
    """Only the hints actually revealed. The unrevealed ones are not sent --
    previously the whole hint list shipped to the client and was merely not
    rendered."""


class MathArchiveEntry(Base):
    """One past day, for the archive.

    Carries the whole problem — statement, answer, hints and all. The archive
    is a reading surface for work already done, so withholding the answer the
    way the live panel does would only mean the record you came to consult is
    the one thing it will not show you. The exception is a day still open to
    late submission, which the modal keeps behind a reveal: seeing the answer
    first would make the self-report meaningless.
    """

    date: dt.date
    problem: MathProblem
    status: str
    score: Score | None = None
    hints_revealed: int = 0
    delta: int | None = None
    """Net pooled rating change on that date, summed across its rows."""

    late_eligible: bool = False
    """Lapsed, and still inside the submission window."""

    late_score_if_solved: Score | None = None
    """What a late `solved` would be worth, given the hints already revealed.

    Computed server-side and sent, rather than derived in the browser, for the
    same reason the elo update is: scoring lives in one place. It is also the
    number that makes the offer honest — "submit late" without saying what it
    will score is asking for a decision with the price hidden.
    """


class MathArchiveResponse(Base):
    entries: list[MathArchiveEntry] = Field(default_factory=list)
    late_window_days: int
    """From `scoring`, so the modal can say how long is left without keeping
    its own copy of the rule."""


class MathLateResponse(Base):
    """Both halves of what a late submission changes.

    One round trip rather than two: the panel's summary moved (rating, areas,
    history) and so did the archive row that was just submitted, and fetching
    them separately would flash the old numbers in between.
    """

    panel: MathPanel
    archive: MathArchiveResponse


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


class TheoremDeckEntry(Base):
    """A card plus the two numbers that explain its odds."""

    card: TheoremCard
    days_since: int | None = None
    """None for a card that has never been drawn."""

    seen: int = 0
    recalled: int = 0


class TheoremsPanel(Base):
    today: TheoremCard | None = None
    """Null until the day's draw happens, which is a GET of its own rather
    than part of the boot payload -- see `theorem_today` in the server."""

    recalled: bool | None = None
    """Today's self-check: null unanswered, else what you said."""

    deck: list[TheoremDeckEntry] = Field(default_factory=list)
    deck_size: int = 0
    """Active cards only. `len(deck)` includes the archived ones."""


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


class FeedMeta(Base):
    """How fresh the data behind a feed panel is.

    The server has always cached every feed and served the last good copy when
    the source was unreachable, which is the right behaviour and was entirely
    invisible: a Yahoo outage put week-old prices on screen looking exactly
    like live ones. These three fields are that state, crossing HTTP.

    `ttl_seconds` is here so the browser does not have to keep its own copy of
    the cache windows. The panel schedules its next poll from the server's own
    number, which means the interval lives in `config.toml` alone -- polling
    faster would only get the same cached answer handed back.
    """

    fetched_at: dt.datetime
    """When the value came off the wire. UTC; the browser renders it local."""

    ttl_seconds: int
    stale: bool = False
    """The refetch was attempted and the source did not answer."""


class NewsItem(Base):
    title: str
    link: Url
    """What the browser opens -- proxied through the library for a paywalled
    domain, so not the same string as `key`."""

    cat: str

    key: str = ""
    """The story's identity: the normalised link *before* proxying.

    Keyed on the original because `[news].paywalled` is config. Keying on the
    proxied URL would mean adding a domain to that list silently changes the
    identity of every story from it, and the whole back catalogue reappears
    once as new.
    """


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


class MarketsResponse(Base):
    """Was a bare `list[MarketRow]`. An envelope, so freshness has somewhere
    to live -- a list has no room for anything about itself."""

    rows: list[MarketRow] = Field(default_factory=list)
    feed: FeedMeta


class NewsResponse(Base):
    items: list[NewsItem] = Field(default_factory=list)
    feed: FeedMeta


class CalendarResponse(Base):
    today: list[CalendarEvent] = Field(default_factory=list)
    week: list[CalendarEvent] = Field(default_factory=list)

    feed: FeedMeta | None = None
    """Attached by the route, not by the fetcher.

    Optional because `Feeds._fetch_calendar` builds this response *inside* the
    cache and so cannot know when it will be considered fetched. The route
    wraps it once the cache has answered. Never None over HTTP.
    """


# --- streaming -------------------------------------------------------------


class MathStatusEvent(Base):
    """One ndjson line from POST /api/math/new while generation runs."""

    status: str


class MathDoneEvent(Base):
    problem: MathProblem
    hints_revealed: int = 0
