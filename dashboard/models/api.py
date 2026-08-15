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
from typing import Annotated

from pydantic import Field, model_validator

from .common import (
    Base,
    ChessOutcome,
    FactStance,
    HealthMetric,
    MathOutcome,
    Rating,
    Score,
    Url,
)
from .domain import (
    MAX_SENTENCES,
    SENTENCES,
    DayCompletion,
    DebateFact,
    DebateReading,
    DebateSession,
    MathProblem,
    Paper,
    RatingEvent,
    SpellingWord,
    TheoremCard,
    TopicInfo,
    Trick,
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


Minutes = Annotated[int | None, Field(default=None, ge=1, le=1440)]
"""Self-reported solve time, to the minute. See `domain.MathAttempt.minutes`."""


class MathFinishRequest(DateBody):
    outcome: MathOutcome
    """The client reports what happened; the server derives the score from
    hints_revealed and applies the elo update in one transaction."""

    minutes: Minutes = None
    """Optional. Read only on a `solved` outcome -- a failure scores zero
    whether it took three minutes or two hours, so the server does not store a
    time it would then have to explain having ignored."""


class MathLateRequest(DateBody):
    """`date` is today, as everywhere; `target` is the lapsed day being
    submitted. Both, because the window is measured between them and only the
    browser knows which day today is."""

    target: dt.date
    outcome: MathOutcome
    minutes: Minutes = None


class ChessNewRequest(DateBody):
    pass


class ChessFinishRequest(DateBody):
    outcome: ChessOutcome


class WordTodayRequest(DateBody):
    pass


class WordSentencesRequest(DateBody):
    """Autosave of the boxes, without grading.

    Bounded by `MAX_SENTENCES` rather than by `SENTENCES`: this is the autosave
    a tab left open from before the count changed will still be firing, and
    rejecting it would lose typing to a number that is none of its business.
    """

    sentences: list[str] = Field(max_length=MAX_SENTENCES)


class WordCheckRequest(DateBody):
    word: str = Field(min_length=1)
    sentences: list[str] = Field(min_length=SENTENCES, max_length=SENTENCES)
    """The "need all of them" rule lives in the contract rather than in a
    client-side `if`. Previously a short list was a 500."""


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


class PatternAddRequest(DateBody):
    """ "add motte and bailey" — a name, not a statement. Same bargain the
    theorem deck makes: adding costs one phrase, and the LLM writes it out."""

    prompt: str = Field(min_length=2, max_length=200)


class PatternRecallRequest(DateBody):
    explanation: str = Field(min_length=3, max_length=2000)
    application: str = Field(min_length=3, max_length=2000)


class PatternArchiveRequest(DateBody):
    card_id: int
    archived: bool = True


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
    """The written recall: the theorem, and somewhere you needed it.

    Bounded generously at both ends. The floor stops an empty box being posted
    as an answer and graded as a miss; the ceiling is a sanity limit, not a
    target -- the worked example for Cauchy-Schwarz is "the dot product's
    magnitude is at most the product of the magnitudes", and a good answer to
    either half is usually one sentence.

    `application` is required. Half the rating rides on it and a blank is a
    legitimate answer to give -- but it has to be given by submitting an
    admission, not by leaving the field empty, because those two want
    different things back from the grader.
    """

    explanation: str = Field(min_length=3, max_length=2000)
    application: str = Field(min_length=3, max_length=2000)


class TheoremArchiveRequest(DateBody):
    card_id: int
    archived: bool = True


class DebateOpenRequest(DateBody):
    pass


class DebateFactRequest(DateBody):
    """One sourced claim for the dossier.

    `source` is required and free text. Required because a claim you cannot
    attribute is not yet a fact and the `evidence` line of the ballot will find
    that out; free text because a fact can come from a book, and a URL-only
    field is one you route around by pasting a search link.
    """

    claim: str = Field(min_length=3, max_length=600)
    source: str = Field(min_length=4, max_length=500)
    stance: FactStance = FactStance.context


class DebateFactDeleteRequest(DateBody):
    fact_id: int


class DebatePositionRequest(DateBody):
    """What you believe, before or after arguing it."""

    position: str = Field(min_length=3, max_length=1000)
    confidence: int | None = Field(default=None, ge=0, le=100)
    after: bool = False


class DebateBriefRequest(DateBody):
    pass


class DebateReadRequest(DateBody):
    """Tick or untick one item of the week's reading."""

    reading_id: int
    read: bool = True


class DebateVerdictRequest(DateBody):
    """The ballot, pasted whole.

    Deliberately the raw text rather than parsed JSON from the browser. Wordle
    and Pokedoku parse client-side because their share text is a shape, not a
    document; a ballot is a document whose reading decides a rating, so it is
    parsed where the scoring lives and where the tests are.
    """

    text: str = Field(min_length=2, max_length=20000)


class SpellingNewRequest(DateBody):
    pass


class SpellingSubmitRequest(DateBody):
    """The whole round at once, in word order.

    Answers rather than a score: the client never marks its own work here, for
    the same reason it never derives a math score. A blank is a wrong answer,
    which is why the entries may be empty strings.
    """

    answers: list[str] = Field(max_length=20)


class TrickNewRequest(DateBody):
    pass


class TrickSubmitRequest(DateBody):
    """The marked set at once, in problem order.

    Answers rather than a score, for the reason `SpellingSubmitRequest` gives:
    the client never marks its own work. A blank is a wrong answer, which is
    why the entries may be empty strings.
    """

    answers: list[str] = Field(max_length=20)


class TrickPasteRequest(DateBody):
    """A set written in the Claude app, pasted back whole.

    The raw text rather than parsed JSON from the browser, exactly as
    `DebateVerdictRequest` is: this is a document whose reading decides what
    nine problems say and what counts as answering them, so it is parsed where
    the rules live and where the tests are.
    """

    text: str = Field(min_length=2, max_length=40000)


class TrickChallengeRequest(DateBody):
    """The optional problem, answered in prose.

    Its own endpoint rather than a field on the submit above, because the two
    are answered at different times: the challenge is the one you go away and
    think about, and making it part of the set would mean either submitting the
    day early or holding the other eight hostage to it.
    """

    answer: str = Field(min_length=3, max_length=4000)


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

    minutes: int | None = None
    """What was submitted, once the day is closed."""

    par_minutes: int | None = None
    """The reference time for this problem's tier, so the panel can price the
    minutes box as you type it.

    Sent rather than derived in the browser for the same reason
    `late_score_if_solved` is: the curve lives in one place. Offering a bonus
    without saying what the number in the box is worth is asking for a
    self-report with the price hidden, which is the one thing that makes a
    self-report worth less than nothing.
    """


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
    minutes: int | None = None
    par_minutes: int | None = None
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


class TheoremWritten(Base):
    """A graded recall, as the panel shows it back."""

    explanation: str
    application: str
    explained: bool
    applied: bool
    note: str


class TheoremDeckEntry(Base):
    """A card plus the two numbers that explain its odds."""

    card: TheoremCard
    days_since: int | None = None
    """None for a card that has never been drawn."""

    seen: int = 0
    recalled: int = 0


class SpellingPanel(Base):
    """The bee: today's words, what you typed, and how it went.

    [Not an invariant, deliberately] The words *are* in this payload before the
    round is marked, unlike the math answer or the theorem statement. They have
    to be — `speechSynthesis` runs in the browser, so the string it says out
    loud has to reach it. That is the same bargain the math panel already
    acknowledges about its answer: declining to render it was never a control,
    only an inconvenience. Marking still happens server-side, so the *score*
    is not the client's to decide.
    """

    enabled: bool = True
    words: list[SpellingWord] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    marks: list[bool] = Field(default_factory=list)
    """Per word, once the round is closed. Empty while it is open."""

    correct: int | None = None
    status: str = "open"

    summary: TrackSummary
    delta: int | None = None
    """Net rating change across today's five results."""


class TrickPrompt(Base):
    """One problem of the day's set, as the panel is allowed to see it.

    [Invariant] `answer`, `accept` and `solution` are empty until the set has
    been marked. The whole feature is worked by hand against a comparison the
    server performs, so shipping the answers with the problems would make the
    marking ceremonial -- the same rule `MathPanel.hints_visible` and
    `TheoremsPanel.today` follow, and the reason this is a projection rather
    than `domain.TrickProblem` sent as-is.

    The trick's *statement* is not withheld, and that is not an inconsistency.
    It is the thing being taught: the day is "here is the identity, now use it
    eight times", not "recall the identity".
    """

    band: str
    prompt: str

    callback_of: str | None = None
    """The older trick this problem is on, when it is the callback. The panel
    labels the row with it -- an unlabelled callback is just a problem that
    does not seem to be about today's trick."""

    answer: str = ""
    accept: list[str] = Field(default_factory=list)
    solution: str = ""

    typed: str = ""
    """What was submitted for it. Empty while the set is open."""

    correct: bool | None = None
    """Null until marked."""


class TricksPanel(Base):
    """The trick of the day: the card, the set, and the rating it moves."""

    enabled: bool = True
    """False when `[tricks.anchors]` is empty. The panel says so rather than
    erroring, the way an unconfigured spelling bee does."""

    trick: Trick | None = None
    """Null until the day's set is drawn, which is a POST of its own rather
    than part of the boot payload -- drawing costs a generation on any morning
    the overnight task did not run, so it cannot happen because a tab loaded."""

    prompt: str | None = None
    """The whole generation, assembled and ready to paste into the Claude app.

    Sent with the panel rather than fetched on click, exactly as
    `DebatePanel.prompt` is: the copy button should be instant, and what you
    copy should be what you were looking at. Null once the day has a set, since
    there is then nothing left to generate.
    """

    batched: bool = False
    """Whether tonight's file is already on disk. What lets the panel say the
    set is free before you press anything."""

    problems: list[TrickPrompt] = Field(default_factory=list)
    challenge: TrickPrompt | None = None
    """The optional problem, apart from the set: it is graded by a model rather
    than compared, and it can be answered after the rest is closed."""

    answers: list[str] = Field(default_factory=list)
    correct: int | None = None
    status: str = "open"

    challenge_answer: str | None = None
    challenge_ok: bool | None = None
    challenge_note: str | None = None

    callback_of: str | None = None
    """The older trick today's set reaches back to, if any. Named on the panel
    header as well as on the row, so the reinforcement is visible before you
    have scrolled to the problem doing it."""

    rating: Rating
    """The trick rating: the math track's `tricks` bucket. Deliberately not one
    of the areas and deliberately not the pooled math rating -- see
    `TRICK_AREA`."""

    events: int = 0
    """Results behind that rating. Zero means it is the pooled math rating
    standing in, not something earned."""

    delta: int | None = None
    """What today did to it, summed across the set and the challenge."""

    total: int = 0
    """Tricks learned, ever. The deck this accumulates."""


class TrickArchiveEntry(Base):
    """One past day, for the archive. Carries the whole set, answers and all --
    a reading surface for work already done, on `MathArchiveEntry`'s reasoning."""

    date: dt.date
    trick: Trick
    problems: list[TrickPrompt] = Field(default_factory=list)
    challenge: TrickPrompt | None = None
    correct: int | None = None
    status: str = "open"
    delta: int | None = None


class TrickArchiveResponse(Base):
    entries: list[TrickArchiveEntry] = Field(default_factory=list)


class PatternsPanel(Base):
    """The argument deck: today's card, the deck, and the pattern rating.

    Its own payload rather than a second `TheoremsPanel`, because the two
    panels ask about different things and share only their machinery. This one
    carries a rating (the theorem panel reads its area rating off the math
    panel, which has nowhere to put a debate-track number) and the patterns
    the last ballot named, which is where most cards come from.
    """

    today: TheoremCard | None = None
    """[Invariant] `statement` is blanked until the day is graded, exactly as
    `TheoremsPanel.today` is and for the same reason."""

    recalled: bool | None = None
    written: TheoremWritten | None = None
    delta: int | None = None

    deck: list[TheoremDeckEntry] = Field(default_factory=list)
    deck_size: int = 0

    rating: Rating
    """The pattern-recall rating: the debate track's `argument-patterns`
    bucket. Deliberately not one of the issues — see `ARGUMENT_AREA`."""

    events: int = 0
    """Graded recalls behind that rating. Zero means it is the pooled debate
    rating standing in, not something earned."""

    suggested: list[str] = Field(default_factory=list)
    """Patterns the most recent ballot named that are not in the deck yet.
    Offered as buttons; nothing is added without a press."""


class TheoremsPanel(Base):
    today: TheoremCard | None = None
    """Null until the day's draw happens, which is a GET of its own rather
    than part of the boot payload -- see `theorem_today` in the server.

    [Invariant] `today.statement` is blanked until the day is graded. The
    panel used to receive the statement with the draw and merely decline to
    render it behind a reveal button, which made "have you recalled this" a
    question the client was trusted to ask honestly of a value it was already
    holding. Nothing renders it early now because nothing is sent it early --
    the same rule `MathPanel.hints_visible` follows.
    """

    recalled: bool | None = None
    """Did you know the theorem: null until graded, else the `explained` half."""

    written: TheoremWritten | None = None
    """What you wrote and how it graded. Null until submitted."""

    delta: int | None = None
    """What the grade did to the area rating -- +3, 0 or -3."""

    deck: list[TheoremDeckEntry] = Field(default_factory=list)
    deck_size: int = 0
    """Active cards only. `len(deck)` includes the archived ones."""


class RubricLine(Base):
    """One ballot line, as the panel needs it to render a result back.

    Shipped rather than duplicated in JavaScript for the reason the time bonus
    is: the eight labels and their band descriptors are the rubric, and a second
    copy in the browser is a second thing to keep in step with the prompt.
    """

    key: str
    label: str
    asks: str


class DebatePanel(Base):
    """Today's debate: what the week is about, where the session has got to,
    and everything needed to draw it.

    One payload for four panel states -- unopened, prep, briefed, scored -- for
    the same reason `MathPanel` covers three: the states share a header, and
    fetching a different shape per state would mean the panel could not tell
    which one it was in without asking twice.
    """

    enabled: bool = True
    """False when `[[debate.issues]]` is empty. The panel says so rather than
    erroring, the way an empty theorem deck does."""

    reading_day: bool = False
    """Sunday: the week's sub-area opens, the readings appear, and the day is
    done at `min_facts`. No debate, so nothing rates."""

    issue: str | None = None
    """The issue slug -- also the rating bucket and the dossier's filename."""

    issue_name: str | None = None
    subarea: str | None = None
    angle: str | None = None
    angle_label: str | None = None
    angle_brief: str | None = None

    session: DebateSession | None = None
    prompt: str | None = None
    """The assembled prompt, or None before the session is opened. Sent with
    the panel rather than fetched on click so the copy button is instant and
    so what you copy is what you were looking at."""

    facts_today: list[DebateFact] = Field(default_factory=list)
    facts_total: int = 0
    min_facts: int = 0
    grace_days: int = 1

    summary: TrackSummary
    issues: list[AreaRating] = Field(default_factory=list)
    """Per-issue ratings, strongest first -- the debate track's answer to
    `MathPanel.areas`, and read the same way: the last line is the weak one."""

    rubric: list[RubricLine] = Field(default_factory=list)
    delta: int | None = None
    """What today's ballot did to the pooled debate rating."""

    reading: list[DebateReading] = Field(default_factory=list)
    """The week's assigned reading, required articles first.

    Carried on every day of the week, not only the reading day: the debate
    prompt quotes what you marked as read, and Thursday should be able to tick
    the piece it did not get to on Sunday.
    """

    unread_required: int = 0
    """Required articles still unticked. What the reading day is waiting on."""

    week_start: dt.date | None = None


class RubricTrend(Base):
    """One ballot line's record across every scored debate.

    The thing the whole feature was asked for -- "you don't improve what you
    don't measure" -- and the reason the rubric is eight fixed lines rather
    than a number. A rating says you are getting better; this says at what.
    """

    key: str
    label: str
    scores: list[int] = Field(default_factory=list)
    """0/1/2 per debate, oldest first."""

    average: float = 0.0
    recent: float = 0.0
    """Mean over the last `RECENT_DEBATES`, so a line you have fixed stops
    being dragged down by the month you had not."""

    trend: float = 0.0
    """`recent` minus the mean of everything before it. Positive is improving.
    Zero until there is enough history for the two windows to differ, which is
    honest rather than flat -- a trend computed from two debates is noise."""


class FoulCount(Base):
    kind: str
    count: int


class ConvictionPoint(Base):
    """What you thought, and how sure, on one day."""

    date: dt.date
    issue: str
    confidence: int
    position: str
    moved: bool = False
    """The debate changed your stated position. Counted rather than judged --
    changing your mind is neither the goal nor a failure, but never doing it
    over six months is worth being able to see."""


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
    debate: DebatePanel
    patterns: PatternsPanel
    spelling: SpellingPanel
    tricks: TricksPanel
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


# --- spend -----------------------------------------------------------------


class SpendKind(Base):
    """One call kind's share of a window."""

    kind: str
    calls: int
    usd: float


class SpendWindow(Base):
    days: int
    label: str
    usd: float
    calls: int
    by_kind: list[SpendKind] = Field(default_factory=list)


class SpendResponse(Base):
    """Estimated LLM spend, and whether the overnight batch is earning its keep.

    Every figure here is an *estimate*, priced from `[llm.pricing]` at the
    moment of each call. A later rate change does not rewrite history, which is
    why the label says estimate wherever this surfaces -- it is a spend log,
    not an invoice.
    """

    windows: list[SpendWindow] = Field(default_factory=list)

    window_days: int
    """The span the two counts below are measured over -- the longest window,
    so the strip can say what "6 of 7" is counting without hardcoding 30."""

    problems_served: int
    problems_paid: int
    """Math problems drawn, and how many of them cost a live generation. The
    difference is what the batch and the curated pool covered; counting them
    from two different sources is what makes the difference meaningful."""

    last_live_generation: dt.date | None = None
    tracking_since: dt.date | None = None
    """First day the log has any record of. Anything before this is not $0, it
    is unmeasured, and the strip says so rather than implying a clean history."""

    unreadable_lines: int = 0
    """Lines the parser could not use. Surfaced rather than swallowed: a total
    computed from a partial read is not wrong, but it is not complete, and
    those are different claims."""


# --- history ---------------------------------------------------------------


class AreaSeries(Base):
    """One area's rating trajectory, for the retrospective charts.

    The pooled headline already has a sparkline. This is the number that
    actually decides what you get served each morning, and it has never been
    plotted -- only ever shown as a single current figure in the panel strip.
    """

    area: str
    points: list[tuple[dt.date, int]] = Field(default_factory=list)
    events: int


class DebateHistory(Base):
    """Everything retrospective about the debate that is not in the panel.

    Below `AreaSeries` because it reuses it for the per-issue rating lines.

    Its own endpoint rather than part of `/api/history`, because it is fetched
    by one panel on one tab and because the ballot scorecards it reads are the
    largest rows in the schema -- shipping them with the boot payload would
    make every page load carry six months of debate.
    """

    debates: int = 0
    lines: list[RubricTrend] = Field(default_factory=list)
    fouls: list[FoulCount] = Field(default_factory=list)
    voided: int = 0
    """Ballot lines zeroed for claiming a point with no quotation. A count
    worth watching: a judge that pads is one whose other numbers read high."""

    issues: list[AreaSeries] = Field(default_factory=list)
    conviction: list[ConvictionPoint] = Field(default_factory=list)
    scores: list[tuple[dt.date, float]] = Field(default_factory=list)


class DayLogEntry(Base):
    date: dt.date
    text: str


class HistoryResponse(Base):
    """Everything backward-looking that is not already in the boot payload.

    `completion` is deliberately absent: it ships whole with `/api/state`
    already, so the heatmap draws from the store and this response carries only
    what would otherwise be unreachable.
    """

    areas: list[AreaSeries] = Field(default_factory=list)
    daylogs: list[DayLogEntry] = Field(default_factory=list)


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


# --- the portfolio ---------------------------------------------------------
#
# Every model below crosses the fingerprint gate except `PortfolioStatus`,
# which is what the locked panel draws itself from and therefore must contain
# nothing worth locking: whether a credential exists, whether an unlock is
# live, and where the page has to be loaded from. No symbol, no total, no
# count of positions.


class PortfolioStatus(Base):
    enabled: bool = True
    enrolled: bool = False
    """Whether any authenticator has been registered. False means the enrol
    button, which is unauthenticated -- see `POST /api/portfolio/enroll/begin`
    for why the first one has to be, and what that costs."""

    unlocked: bool = False
    expires_in: int = 0
    """Seconds left on the caller's token, for the countdown. 0 when locked."""

    present: bool = False
    """Whether the positions file exists at all. A fresh clone has none, and
    'no file yet' is a different message from 'locked'."""

    origin: str
    """Where WebAuthn will work. The panel compares this against its own
    `location.origin` and explains the mismatch rather than letting the browser
    reject the ceremony with a console error nobody is looking at."""


class PortfolioPosition(Base):
    """One row of the export.

    Every number is optional and independently so: a money market fund has no
    cost basis and a pending trade has no quantity. See `dashboard/portfolio.py`
    -- modelling those as 0.0 would put them in the totals as real numbers.
    """

    symbol: str
    description: str = ""
    account_type: str = ""
    quantity: float | None = None
    last_price: float | None = None
    last_change: float | None = None
    value: float | None = None
    day_gain: float | None = None
    day_gain_pct: float | None = None
    total_gain: float | None = None
    total_gain_pct: float | None = None
    pct_of_account: float | None = None
    cost_basis: float | None = None
    avg_cost: float | None = None


class PortfolioTotals(Base):
    value: float | None = None
    day_gain: float | None = None
    day_gain_pct: float | None = None
    total_gain: float | None = None
    total_gain_pct: float | None = None


class PortfolioResponse(Base):
    positions: list[PortfolioPosition] = Field(default_factory=list)
    totals: PortfolioTotals = Field(default_factory=PortfolioTotals)
    warnings: list[str] = Field(default_factory=list)
    """Cells that could not be read, by line and column name. Never contains a
    cell value -- the point of the file is that its numbers stay in it."""

    as_of: dt.datetime | None = None
    """The file's mtime. The export carries no timestamp of its own."""

    expires_in: int = 0


class EnrollFinish(DateBody):
    credential_id: str = Field(min_length=1, max_length=1024)
    client_data_json: str = Field(min_length=1, max_length=8192)
    public_key: str = Field(min_length=1, max_length=8192)
    """SPKI DER, base64url, from `AuthenticatorAttestationResponse.getPublicKey()`."""

    alg: int
    label: str = Field(default="", max_length=64)


class UnlockFinish(DateBody):
    credential_id: str = Field(min_length=1, max_length=1024)
    client_data_json: str = Field(min_length=1, max_length=8192)
    authenticator_data: str = Field(min_length=1, max_length=8192)
    signature: str = Field(min_length=1, max_length=8192)
