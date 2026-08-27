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
    BathroomKind,
    ChessStatus,
    DebateSide,
    DebateStatus,
    Deck,
    EntrySource,
    FactStance,
    HealthMetric,
    MathStatus,
    MealSize,
    PaperStatus,
    Provenance,
    Rating,
    Score,
    SessionKind,
    Stool,
    Track,
    UrineColor,
    Url,
    Volume,
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

    deck: Deck = Deck.math
    """Which deck this belongs to. `math` on every card that predates the
    column, which is what all of them were."""


class TheoremGrade(Base):
    """What the grader made of a written recall."""

    explained: bool
    applied: bool
    note: str
    """One line back to you -- what was missing, or what was right. The half
    that failed is worth naming, since "0" on its own teaches nothing."""

    area: str | None = None
    """Which rating bucket this theorem belongs to, chosen by the grader from
    the known list.

    Asked for rather than parsed out of the card's `topic`, because a card's
    topic is whatever the generation that wrote it felt like saying -- one in
    the deck reads "Combinatorics / Generating Functions (Complex Analysis
    applications in competition mathematics)". The grader is already reading
    the statement and the list is six words long, so this costs nothing and is
    right far more often than string matching. Validated against the real list
    on the way in; an unrecognised answer falls back to matching."""


class TheoremDraw(Base):
    """The day's theorem, what you wrote about it, and how it graded.

    Append-only and keyed by date, so it is both the record and the schedule:
    `days since last appeared` is a query over this table, not a stored
    countdown that can drift out of step with what you were actually shown.
    """

    date: dt.date
    deck: Deck = Deck.math
    """Part of the key: one draw per deck per day, so a theorem and an argument
    pattern can both come up on the same morning."""

    card_id: int

    recalled: bool | None = None
    """NULL until it is graded. Deliberately not defaulted to False -- "you
    have not answered yet" and "you missed it" are different days, and only
    the second one is a data point.

    Set from `explained` rather than from the pair, so it goes on meaning what
    it has always meant: did you know the theorem. The archive's "recalled 3x"
    therefore still counts the same thing it counted before the answer became
    two-part, and the application half is kept separately rather than folded
    into a number whose history would then be measuring two different things.
    """

    explanation: str | None = None
    """The theorem in your own words. NULL on every draw before migration 9,
    and on a draw not yet answered."""

    application: str | None = None
    """A problem you needed it for. Not required to name it -- "the polynomial
    that agreed with a rational function on [0,1]" is the point, not a
    citation."""

    explained: bool | None = None
    applied: bool | None = None
    note: str | None = None


# --- tricks -----------------------------------------------------------------


class TrickProblem(Base):
    """One problem in the day's set, frozen onto the round.

    `answer` is canonical and `accept` holds the other spellings of the same
    answer -- `1/2` and `0.5`, `n(n+1)/2` and `\\frac{n(n+1)}{2}`. The marking
    is a string comparison (see `dashboard/tricks.py`), so a right answer
    written the other obvious way has to be listed or it marks wrong, and the
    generator is asked for those alternatives rather than the marker being
    taught algebra.
    """

    band: str = Field(pattern=r"^(easy|medium|callback|challenge)$")
    """Which rung. `callback` is a medium-difficulty problem on an *older*
    trick, which is the whole reinforcement mechanism -- it is a band rather
    than a flag so it can carry its own label in the panel."""

    prompt: str = Field(min_length=3)
    answer: str = Field(min_length=1)
    accept: list[str] = Field(default_factory=list, max_length=6)
    solution: str = ""
    """One or two lines, shown after the set is marked. The point of the day is
    the trick, so getting a problem wrong and not being told why would leave
    the one thing worth learning unsaid."""


class Trick(Base):
    """One trick, identity or technique. The deck this feature accumulates.

    Kept in its own table rather than as a third `Deck` on `theorem_card`,
    which was the near miss. The theorem deck's whole machinery is a
    recency-weighted *draw for recall* -- adding tricks to it would put them in
    the theorem panel's rotation and ask you to recite them, which is not what
    this is for. What a past trick is drawn for here is a problem, and that is a
    different question with a different answer.
    """

    id: int | None = None

    name: str = Field(min_length=2, max_length=120)
    """UNIQUE, case-insensitively. This is also the do-not-repeat list: "have
    I had this trick" is `SELECT name FROM trick`, the rule `math_problem.source`
    already follows."""

    statement: str = Field(min_length=3)
    """The identity or rule itself, with every formula in LaTeX delimiters."""

    idea: str = Field(default="", max_length=1200)
    """When to reach for it, and what it buys -- the half that makes it a
    *trick* rather than a fact. A statement you can recite and never recognise
    an occasion for is the failure mode this field exists against."""

    topic: str
    """The rotation topic it was drawn for, frozen at generation time for the
    reason `MathProblem.topic` is: the rotation is config and can be edited."""

    kind: str = Field(default="trick", pattern=r"^(trick|technique)$")
    """The fallback that makes topic-coupling survivable. Most weeks yield a
    computational shortcut; a week on measure theory or topology does not, and
    the honest answer there is a reusable *technique* -- the standard opening
    move, the canonical construction -- rather than a shortcut invented to fill
    the slot. Recorded so it is visible which the day got."""

    added: dt.date


class TrickRound(Base):
    """One day's trick and the set of problems that teach it.

    Date-keyed like every other daily table here, which is what makes a reload
    idempotent rather than a reroll -- and this one costs a generation to
    reroll, so it matters more than usual.
    """

    date: dt.date
    trick_id: int
    callback_id: int | None = None
    """The older trick one problem in the set reaches back to, when there was
    one to reach for. NULL on the first day, and on any day the draw found
    nothing off cooldown."""

    problems: list[TrickProblem] = Field(default_factory=list, max_length=20)
    """Frozen at draw time, the challenge last. `answers` is positional against
    the non-challenge entries."""

    answers: list[str] = Field(default_factory=list, max_length=20)
    correct: int | None = None
    """NULL while open. Derived server-side from `answers`; the client never
    sends a score, exactly as it never sends a math outcome's."""

    status: str = "open"

    challenge: str | None = None
    """What was written for the optional problem. NULL means not attempted,
    which is a legitimate way to finish the day -- it is optional."""

    challenge_ok: bool | None = None
    challenge_note: str | None = None
    """One line from the grader. The challenge asks for more than applying the
    trick, so "wrong" without saying where teaches nothing."""


class TrickGrade(Base):
    """What the grader made of a challenge answer."""

    correct: bool
    note: str


# --- spelling ---------------------------------------------------------------


class SpellingWord(Base):
    """One drawn word, frozen onto the round.

    `anchor` is resolved from the tier at draw time and stored, the rule
    `math_problem.anchor` and `rating_event.opponent` both follow: the tier
    table is config and the pool is a file, and a round has to go on being
    scored against what it was drawn as.
    """

    word: str = Field(min_length=1, max_length=64)
    tier: str
    hint: str = ""
    """Spoken-word disambiguation. `principle` and `principal` sound
    identical, so without this the bee measures hearing rather than spelling."""

    anchor: Rating


class SpellingRound(Base):
    """One day's five words, and how they went."""

    date: dt.date
    words: list[SpellingWord] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    """What was typed, in word order. Empty while the round is open."""

    correct: int | None = None
    """NULL until submitted. Derived server-side from `answers`; the client
    never sends a score, exactly as it never sends a math outcome's score."""

    status: str = "open"


# --- debate ----------------------------------------------------------------


class Reading(Base):
    """One article offered for the day's issue.

    Frozen onto the session at open time rather than re-ranked on every render.
    The headline pool turns over through the day, and a prep list that changed
    while you were reading it would be a list you could not finish -- the same
    argument that makes today's math problem a stored row rather than a query.
    """

    title: str
    link: Url
    """Proxied for a paywalled domain, exactly as `NewsItem.link` is."""


class RubricItem(Base):
    """One line of the judge's ballot.

    `score` is 0/1/2 against the anchored band descriptors in `debate.RUBRIC`,
    not a free-running number: the whole reason the rubric is shaped this way
    is that "7 out of 10 on rhetoric" means something different to every model
    version, and a rating history has to survive its judge being upgraded.
    """

    key: str
    score: int = Field(ge=0, le=2)
    quote: str = ""
    """Verbatim from what *you* wrote. An item claiming a point with no quote
    is voided to zero server-side -- see `debate.apply_quote_rule`."""

    note: str = ""


class Foul(Base):
    """A hard foul: something that costs a point outright rather than scoring
    a band. Named separately because these are events, not qualities."""

    kind: str
    quote: str = ""


class Scorecard(Base):
    """What the opponent returns at the end, pasted back as JSON.

    Parsed and re-scored here rather than trusted: the model reports what it
    observed, and `scoring.debate_score` decides what that is worth. A judge
    that hands back its own total is a judge whose scale you have to trust
    across model versions.
    """

    items: list[RubricItem] = Field(default_factory=list)
    fouls: list[Foul] = Field(default_factory=list)
    verdict: str = ""

    patterns: list[str] = Field(default_factory=list, max_length=3)
    """Argument patterns that showed up and are worth being able to name.

    Free, since the judge is already writing a ballot, and the answer to the
    real problem with a curated deck: you cannot add a card for a pattern you
    did not know existed. This is the thing that noticed it. The panel offers
    each one as a button; nothing is added without a press.
    """

    voided: list[str] = Field(default_factory=list)
    """Item keys that claimed a point without a quote and were zeroed. Kept on
    the card so the panel can say so -- a rule you cannot see enforced is one
    you assume is not."""


class DebateReading(Base):
    """One item of the week's reading: a long-form article, or a book.

    Both in one table because the panel lists them together and the only real
    difference is which fields are populated -- an article has a publisher and
    a date, a book has an author and a lean. A second table would be two
    queries and one more join for a distinction the reader does not make.
    """

    id: int | None = None
    week_start: dt.date
    """The Sunday the week opens on. Not the day it is read."""

    issue: str
    subarea: str
    kind: str = Field(pattern=r"^(article|book)$")

    title: str
    url: Url | None = None
    author: str | None = None
    publisher: str | None = None
    published: dt.date | None = None
    summary: str | None = None

    lean: str | None = None
    """`left` / `right` / `neither` for a book; None for an article."""

    required: bool = True
    """Books are never required -- see the CHECK in migration 11."""

    read_on: dt.date | None = None


class DebateSession(Base):
    """The day's debate, from opening the issue to pasting the ballot."""

    date: dt.date
    issue: str
    """The issue slug: the rating bucket, frozen here."""

    subarea: str
    angle: str
    motion: str
    motion_source: str | None = None
    """`batch` when the 3am task resolved it against the news, `template` when
    it is the frame the opponent sharpens itself. None predates the column."""

    motion_note: str | None = None
    """What makes it live right now. Only the batch produces one."""

    motion_url: Url | None = None
    format: str
    side: DebateSide
    anchor: Rating
    contra: bool = False

    position: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    position_after: str | None = None
    confidence_after: int | None = Field(default=None, ge=0, le=100)

    readings: list[Reading] = Field(default_factory=list)
    status: DebateStatus = DebateStatus.prep
    briefed_on: dt.date | None = None
    scorecard: Scorecard | None = None
    score: Score | None = None


class DebateFact(Base):
    """One sourced claim in the dossier."""

    id: int | None = None
    issue: str
    date: dt.date
    claim: str = Field(min_length=3, max_length=600)
    source: str = Field(min_length=4, max_length=500)
    stance: FactStance = FactStance.context


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

    source: EntrySource = EntrySource.manual
    """Who wrote it. `manual` outranks `garmin` and a sync must respect that;
    the rule lives in `Repository.log_health` so there is one place to get it
    wrong."""


class Readiness(Base):
    """The morning questionnaire. Five items, one row a day.

    All five run 1 = worst, 5 = best -- see the migration for why that diverges
    from how Hooper is published. `score` is the sum, so 5 is the floor and 25
    the ceiling, and it is deliberately never compared against an absolute
    threshold: what a 17 means is entirely a question of what your own 17s have
    meant lately.

    What the *panel* shows is no longer this: fatigue, soreness and stress are
    presented counting up in the thing they name, because "5 fatigue = fresh"
    is a scale you have to translate before you can answer it. The flip happens
    in `web/app/panels/checkin.js` and nowhere else, so this column keeps one
    meaning across every row ever written and every mean taken over them.
    """

    date: dt.date
    sleep_quality: int = Field(ge=1, le=5)
    fatigue: int = Field(ge=1, le=5)
    soreness: int = Field(ge=1, le=5)
    stress: int = Field(ge=1, le=5)
    motivation: int = Field(ge=1, le=5)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> int:
        return (
            self.sleep_quality + self.fatigue + self.soreness + self.stress + self.motivation
        )


class TrainingSession(Base):
    """One session. Many per day is normal and supported.

    `load` is session-RPE -- rpe x minutes -- which is the load metric that
    works for a sport a wrist optical sensor cannot measure. It is computed
    rather than stored for the same reason `pct` is absent from
    `day_completion`: storing it beside its two inputs lets the three disagree.
    """

    id: int
    date: dt.date
    kind: SessionKind
    rpe: int = Field(ge=1, le=10)
    minutes: int = Field(ge=1, le=600)
    note: str | None = Field(default=None, max_length=200)

    garmin_activity_id: int | None = None
    """Set only by an import, and UNIQUE, so re-running a sync cannot
    double-count a session that is already in the table."""

    plan_key: str | None = None
    """Which prescribed block this answers -- `<date>:<token>` -- or None for a
    session logged freehand, which stays the ordinary case.

    UNIQUE where present, so a widget double-submitted cannot log the same
    practice twice and double the day's load. Not a foreign key: the plan is a
    file, and this is a record of what it said at the time rather than a live
    pointer into a file that can be re-edited underneath it."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def load(self) -> int:
        return self.rpe * self.minutes


class BathroomLog(Base):
    """One visit. Stool or urine, and each carries exactly its own field.

    The invariant -- `solidity` iff stool, `color` iff urine -- is a CHECK on
    the table rather than a validator here, so it holds for anything that ever
    writes to the file, this process included on a day someone is poking at it
    with `sqlite3`.

    `hour` is the browser's, rounded down, and there is no minute. See the
    migration for why: a field you cannot answer honestly is a field that gets
    answered anyway.
    """

    id: int
    date: dt.date
    hour: int = Field(ge=0, le=23)
    kind: BathroomKind
    solidity: Stool | None = None
    color: UrineColor | None = None
    volume: Volume

    @computed_field  # type: ignore[prop-decorator]
    @property
    def at(self) -> str:
        """`14:00` -- what the panel prints, formatted once, here."""
        return f"{self.hour:02d}:00"


class MealLog(Base):
    """One meal: when, how big, and how processed.

    `size` is a bucket rather than a calorie count on purpose -- see
    `MealSize`. `quality` runs 1 = the most processed thing you could have
    eaten to 5 = whole ingredients you cooked, and it is the axis that pairs
    with the weight trend: a camp lost on food is usually lost on quality
    weeks before it shows up as a number.
    """

    id: int
    date: dt.date
    hour: int = Field(ge=0, le=23)
    size: MealSize
    quality: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=200)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def at(self) -> str:
        return f"{self.hour:02d}:00"


class PrescribedBlock(Base):
    """One thing the plan asks for today -- as opposed to `TrainingSession`,
    which is one thing that happened.

    Never stored. It is compiled out of `training.toml` on every read, and that
    is the point: the plan is a file that gets edited, and a prescription
    persisted beside the session it produced would start lying about what the
    session was measured against the first time a week was rearranged.

    Flat on purpose. Whatever notation the plan is written in -- the
    spreadsheet's shorthand today, whatever replaces it after February -- it
    compiles down to this, and this is the only shape the rest of the app sees.
    """

    key: str
    """`<date>:<token>`, and the column a session links back through.

    No ordinal and no weekday in it, which is deliberate: the one edit actually
    expected here is moving a movement to a different day of the week, and this
    key survives it. The old day stops offering the key, the new day starts, and
    sessions already logged keep theirs as provenance rather than as a live
    pointer into a file that has since changed.
    """

    token: str
    """As written in the plan, parameter included -- `A3`, `C2`, `LFC`."""

    name: str
    """What it actually is: `lower french contrast`."""

    label: str
    """What the panel says: `strength training`. Plainer than `name` because a
    nudge has to be readable before coffee; both are shown."""

    kind: SessionKind | None = None
    choose: list[SessionKind] = Field(default_factory=list)
    """Exactly one of the two is set. `choose` is the cardio slot, where what
    you did is a real question -- shadowboxing and roadwork are different
    training, and the difference is only recoverable if it is recorded at the
    time."""

    asks: str | None = None
    """The duration the plan asks for, when it asks for one. Printed, never
    pre-filled: load is rpe x minutes and both have to be measured."""

    detail: str | None = None
    slot: int = 0
    """Which outing. Same slot means the same trip out of the house."""

    optional: bool = False
    """The plan's apostrophe. It decides whether the day owes you a session,
    which is what makes requiring the session chip safe at all."""

    regress_to: str | None = None
    """The easier option, when there is one. Doing it counts as done -- a
    regression you get no credit for is a regression you skip."""

    group: str | None = None
    """Which standing commitment this is part of, if any. `practice` binds a
    HIIT block and a technique block into one card with one button -- they are
    one session that happens to be logged as two rows, and making them two
    cards would be asking twice for a thing you did once."""

    extra: bool = False
    """Offered on the card, never prescribed and never owed.

    Sparring and neck are the cases: a practice sometimes contains them and the
    plan cannot know in advance. They are kept out of the group's completion
    keys, because a practice bundle that only finished on the days you sparred
    would be unfinishable four days a week.
    """

    source: str | None = None


class PlanGroup(Base):
    """A point-scoring bundle of prescribed blocks -- one practice, one chip."""

    name: str
    points: int = Field(ge=0)
    keys: list[str] = Field(default_factory=list)
    """The blocks that must all be logged before the points are earned."""

    provisional: bool = False
    """Offered, but not yet part of the points on offer.

    The Saturday practice that has not started running yet. Counting it toward
    the denominator would score every Saturday against points that cannot be
    earned; leaving it out entirely would mean a Saturday you did go was worth
    nothing. So it joins the total only on a day it is actually logged.
    """


class DayLog(Base):
    date: dt.date
    text: str = Field(max_length=2000)


class DayCompletion(Base):
    date: dt.date
    done: int = Field(ge=0)
    total: int = Field(gt=0)

    bonus: int = Field(default=0, ge=0)
    """Points from the optional half of the day.

    Deliberately outside `done`/`total`: the required core is what a perfect
    day is counted off, and folding the optional work back into the
    denominator is exactly what made a perfect day depend on whether the NYT
    published a Wordle. Defaulted so rows written before the split -- and the
    importer, which has no notion of points -- read as core only.
    """

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
