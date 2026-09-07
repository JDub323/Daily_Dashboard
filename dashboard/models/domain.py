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
    Deck,
    DrinkName,
    DrinkTag,
    EntrySource,
    HealthMetric,
    MathStatus,
    MealFlag,
    MealPreset,
    MealSize,
    PaperStatus,
    ProblemKind,
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

    kind: ProblemKind = ProblemKind.compute
    """Numeric answer, or proof. Frozen on the row at generation time for the
    reason `topic` and `area` are: `[math.week]` is editable, and a problem must
    not change into something else when the week is rearranged."""

    answer: str
    """What you were supposed to produce.

    The final answer on a compute problem; the book's solution sketch on a
    proof. One column rather than two because it has always meant the same
    thing -- it is what the panel reveals and what the archive reads back --
    and a proof whose `answer` column was empty would be a row that cannot
    show you what you got wrong."""

    hints: list[str] = Field(max_length=3)
    """Was hint1/hint2/hint3. The client immediately built an array anyway.
    The topic/textbook links are not hints and are not counted here.

    The *count* is per-kind and enforced at the boundary in `content.to_problem`
    rather than here: three on a compute problem, two on a proof (a nudge and a
    giveaway -- see `scoring.PROOF_HINT_COSTS`), none on a problem you brought
    yourself, which nobody wrote hints for. Encoding that as a bound on the
    field would make the model unable to hold two of the three."""

    rubric: list[str] = Field(default_factory=list, max_length=6)
    """Checkpoints a complete proof has to hit, written from the book's own
    printed solution.

    Empty on a compute problem: there is nothing to itemise about 137. On a
    proof this is what makes `partial` a claim rather than a mood -- the
    checkpoints are on screen when the button is pressed."""

    location: str | None = None
    """Where in the book -- "2.2.4 Viete's Relations, p. 70".

    `source` ("P&B 199") is the unique key and the do-not-repeat handle; this
    is the pointer a person can act on, so the panel can say where to go and
    read around it."""

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

    day: int = Field(default=1, ge=1, le=2)
    """Which day of a two-day problem this is.

    Two rows point at one problem and only the second is rated. Always 1 on
    every other kind of day, and on every row written before the column."""

    hints_revealed: int = Field(ge=0, le=3, default=0)
    status: MathStatus = MathStatus.open
    score: Score | None = None
    """NULL while open *or carried*. Derived server-side from status +
    hints_revealed."""

    rubric_hit: list[bool] = Field(default_factory=list, max_length=6)
    """Which of the problem's rubric checkpoints you ticked. Empty on a compute
    day, and on a proof closed without itemising."""

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


class SkippedSegment(Base):
    index: int = Field(ge=0)
    reason: str = Field(default="", max_length=200)
    """"knew it", "no time". Short, and free text rather than an enum: the
    useful reasons are not a list anybody could write in advance, and this is
    read by a generator rather than aggregated."""


class StudySegment(Base):
    """One half-hour of Sunday: something to read, then problems to work."""

    title: str
    reading: str
    """What to read, with pages -- "2.2.4, pp. 70-74, through the worked
    example". Pages, because a segment that says "read about Viete" is one you
    have to plan before you can start."""

    problems: list[str] = Field(default_factory=list, max_length=30)
    """Problem numbers to work, as printed. Many and easy is the point: this is
    the day for getting used to the objects, not for being stuck."""

    minutes: int = Field(default=30, ge=5, le=120)
    why: str = ""
    """One line on what this segment buys you, so choosing between three of
    them is a decision rather than a guess."""


class StudyPlan(Base):
    """Sunday's reading plan. Unrated -- it ticks the day and moves nothing.

    Three segments generated and one or two expected, so `done` and `skipped`
    are both partial by design and neither is a failure. `skipped` carries a
    reason because that is the only thing that stops next month's plan opening
    with the same introduction.
    """

    date: dt.date
    topic: str
    book: str
    segments: list[StudySegment] = Field(min_length=1, max_length=6)

    done: list[int] = Field(default_factory=list)
    skipped: list[SkippedSegment] = Field(default_factory=list)

    model: str
    generated_at: dt.datetime

    @computed_field
    @property
    def started(self) -> bool:
        """Any segment done at all -- which is what ticks the day."""
        return bool(self.done)


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


# --- ballots ---------------------------------------------------------------


class RubricItem(Base):
    """One line of the judge's ballot.

    `score` is 0/1/2 against the anchored band descriptors in
    `investigate.RUBRIC`, not a free-running number: the whole reason the
    rubric is shaped this way is that "7 out of 10 on rigour" means something
    different to every model version, and a rating history has to survive its
    judge being upgraded.
    """

    key: str
    score: int = Field(ge=0, le=2)

    note: str = ""


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
        return self.sleep_quality + self.fatigue + self.soreness + self.stress + self.motivation


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

    focus: str | None = None
    """What the session actually was -- a dotted leaf of `SESSION_FOCUS`, or
    None for a session logged without going a level deeper.

    Below `kind` and never instead of it. `kind` is what `daily_load` and the
    camp plan are written against and is unchanged; this says which of the
    things inside that bucket it was, so that "how much sparring" and "how
    much of it was touch" are both answerable afterwards.

    A leaf implies its kind, and `SessionRequest` refuses a body where the two
    disagree. Validated at the boundary rather than here, so that a row
    already in the table stays readable after the tree is edited -- a session
    logged as `cardio.steady.stairmaster` must not become unloadable the day
    the stairmaster leaves the gym."""

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

    preset: MealPreset | None = None
    """What it was, where it was one of the things it usually is -- the
    identity half, and None is the ordinary answer.

    A preset does not *mean* anything the two fields above it do not already
    say; it filled them in. Its value is that "chicken, broccoli, sweet
    potato" is now one tap and one number rather than a judgement made afresh
    each time, so the same plate scores the same every time it is eaten."""

    flags: list[MealFlag] = Field(default_factory=list)
    """What was in it, as a set. Empty is a real answer, exactly as an empty
    `DrinkLog.tags` is. Always in `MealFlag` order; `canonical_flags` is what
    puts it there, on the way in."""

    note: str | None = Field(default=None, max_length=200)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_snack(self) -> bool:
        """Whether this row counts toward the three meals the day requires.

        Computed rather than left to each caller, because the answer decides
        whether a required task is done and two callers disagreeing about it
        would be a day that is complete on the chip and incomplete in the
        ring. The apple is still food -- it counts for the energy balance and
        for diet quality, both of which read `size` and `quality` and neither
        of which looks at this."""
        return MealFlag.snack in self.flags

    @computed_field  # type: ignore[prop-decorator]
    @property
    def at(self) -> str:
        return f"{self.hour:02d}:00"


class DrinkLog(Base):
    """One drink: when, what was in it, and how many.

    `hour` is the browser's and rounded, as on both logs beside this one.

    `tags` is what was in it and it is a *set* -- coffee with sugar is both,
    and a sports drink is `electrolyte` and `sugar` together. Empty is a real
    answer and the commonest one: no tags is a glass of water, because every
    drink in this log is water with something in it and a tag saying so would
    be on every row. Always in `DrinkTag` order; `canonical_tags` is what puts
    it there, on the way in.

    `amount` is in *drinks*, and a drink is twelve ounces of whatever it was --
    a can, roughly, so a 16.9oz bottle is 1.4 and a mug is about 0.8. Decimals
    because the number is printed on the bottle: this is the one field in
    either log that is measured rather than estimated, and bucketing a
    measurement is throwing away accuracy nobody had to work for.

    Alcohol is the exception and it is a real one. There the unit is the
    standard drink -- a shot, a beer and a glass of wine are all 1 -- because
    twelve ounces of spirits is not a quantity anybody means. The two units
    share a column because they share a question ("how many"), and nothing
    reads `amount` without first reading `tags`: see `health.DRINK_ML`, which
    refuses to convert a row carrying `alcohol` to a volume at all.
    """

    id: int
    date: dt.date
    hour: int = Field(ge=0, le=23)
    tags: list[DrinkTag] = Field(default_factory=list)

    name: DrinkName | None = None
    """What it was, where the tags made a name possible -- and None wherever
    they did not, or wherever it was not worth saying.

    Never read by `health.py` and never to be: see `DrinkName` for why a name
    that gets scored has become a tag with a second spelling. What it is for
    is reading a row back a year later, when `sugar + carbonated + caffeine`
    is a complete description that does not tell you whether that was a Coke
    or a Celsius."""

    amount: float = Field(gt=0, le=20)

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

    focus: str | None = None
    """Which leaf of the session tree this is, where the plan knows -- and what
    lets the phone prefill the whole path rather than asking three questions
    with one legal answer each. None on every `choose` token by construction:
    those are the ones where what you did is the question."""

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


# --- the proof textbook ----------------------------------------------------
#
# Parsed once from the PDF's contents pages and committed as `proofbook.json`;
# see `dashboard/proofbook.py` for why the index is in git and the book is not.
# Nothing here is generated content -- it is a description of a printed object,
# which is precisely what makes it worth having.


class ProofSection(Base):
    """One leaf of the contents -- the unit a week's problems are drawn from.

    Leaves only: a section with subsections is represented by its subsections
    and not by itself. Six pages of "Viète's Relations" is a week's worth of
    proof problems; the seventeen pages of "Polynomials" that contain it are a
    chapter's worth, and pointing the generator at the coarser one is how a
    week on symmetric functions comes back with a problem about irreducibility.
    """

    number: str = Field(pattern=r"^\d+\.\d+(\.\d+)?$")
    title: str
    parent: str | None = None
    """The enclosing section's title, where there is one. Carried because the
    leaf title alone is often not enough to know what it is about -- "Some
    General Problems" means nothing without "Integer-Valued Sequences and
    Functions" above it."""

    page: int = Field(gt=0)
    end_page: int = Field(gt=0)
    """The printed page numbers, as the contents give them."""

    pdf_page: int = Field(gt=0)
    pdf_end_page: int = Field(gt=0)
    """The same span as 1-based PDF pages -- which is what a reader actually
    opens, and not the printed number plus a constant.

    Both are stored because the difference is not a constant. It is 15 at the
    start of this book and 10 by the solutions: pages the printing counts are
    missing from the scan. A single offset applied to a printed number would
    land five pages off in the back half, which is the exact failure the index
    exists to prevent, so the offset is measured per page and the answer is
    written down rather than recomputed.
    """

    first_problem: int | None = None
    last_problem: int | None = None
    """The span of problem numbers printed in this section, or None where the
    scan found none it believed. None is a usable answer: the generator is
    given the page range either way, and the numbers are a narrowing, not a
    prerequisite."""


class ProofChapter(Base):
    number: str = Field(pattern=r"^\d+$")
    title: str
    page: int = Field(gt=0)
    solutions_pdf_page: int | None = None
    solutions_pdf_end_page: int | None = None
    """Where this chapter's printed solutions live, in PDF pages. The hints and
    the rubric are written from those pages rather than from the model's own
    solution, which is the whole reason the index bothers to find them."""

    sections: list[ProofSection] = Field(default_factory=list)


class ProofBook(Base):
    title: str
    pdf: str
    pages: int = Field(gt=0)
    chapters: list[ProofChapter] = Field(min_length=1)


# --- the todo list ---------------------------------------------------------


class TodoItem(Base):
    """One dated piece of work, and the Google event that mirrors it.

    A *row* is an occurrence, not a repetition -- see the `todo` table. The
    Google event is a mirror rather than the record: it exists so the due date
    shows up in the week grid and on a phone, while whether the thing is
    finished is answered here, instantly and without a network call.
    """

    id: int
    raw: str
    """The line as typed, before aliases were expanded."""

    title: str
    subject: str | None = None

    calendar: str | None = None
    """Where the mirror was filed. Null exactly when there is no mirror — see
    `due_date`."""

    due_date: dt.date | None = None
    """Null for an undated (`todo`) item: work that is owed but not on a day.

    Those are the rows this dashboard owns outright. There is no Google event
    behind one, so no calendar, no `event_id`, and nothing to delete when it is
    ticked — the schema enforces all four together."""

    start: dt.time | None = None
    end: dt.time | None = None

    series: str | None = None
    """Shared by every occurrence of one "every tuesday"."""

    event_id: str | None = None
    event_url: str | None = None

    done_at: dt.datetime | None = None
    event_deleted: bool = False
    """Whether the mirror has been removed from Google.

    Not the same question as `done_at`, and they come apart in exactly one
    direction: ticking an item is local and instant, deleting its event is a
    network call that can fail afterwards. An item that is done with its event
    still standing is a state the panel can show and retry, which is better
    than a tick that blocks on gcalcli.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def done(self) -> bool:
        return self.done_at is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_day(self) -> bool:
        return self.start is None


# --- investigations --------------------------------------------------------


class Correction(Base):
    """One factual claim the grader checked and found wanting.

    The part of the ballot worth the most, and the reason the prompt tells it
    to go and search: a grade is an opinion, and this is a fact you got wrong
    with a source saying so.
    """

    claim: str = ""
    """What the write-up said."""

    correction: str = ""
    source: str = ""


class InvestigationBallot(Base):
    """What the grader returns, pasted back as JSON.

    Parsed and re-scored rather than trusted -- the model reports what it
    observed and `investigate.score_ballot` decides what that is worth. Same
    bargain the math grader makes, and for the same reason: a judge that hands
    back its own total is a judge whose scale has to be trusted across model
    versions, and this rating is meant to outlive several.
    """

    items: list[RubricItem] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)

    feedback: str = ""
    """The prose half, addressed to you. Not scored, and the part actually
    worth reading twice -- which is why it is a stored column of its own and
    not something reconstructed from the five numbers."""

    patterns: list[str] = Field(default_factory=list, max_length=3)
    """Reasoning moves worth being able to name, offered to the argument deck.

    Free, since the grader is already writing a ballot, and the answer to the
    standing problem with a curated deck: you cannot add a card for a pattern
    you did not know existed. The panel offers each as a button; nothing is
    added without a press.
    """

    verdict: str = ""

    capped: list[str] = Field(default_factory=list)
    """Lines the server pulled down from what the grader awarded. Said out loud
    on the panel, because a rule you cannot see enforced is one you assume is
    not being enforced -- see `investigate.apply_accuracy_rule`."""


class Investigation(Base):
    """One thing to look into, sitting in the pool until a day draws it.

    The pool is a queue only in the sense that things join the back of it --
    the draw is uniform over whatever is open, because the list the class hands
    out is a list of things worth doing, not a list in priority order.

    `used_on` and `cleared_on` are both "no longer in the pool" and are kept
    apart on purpose: one says the topic was investigated, the other says it
    was struck off. Only the first should ever be counted as work done.
    """

    id: int | None = None
    added_on: dt.date
    topic: str = Field(min_length=3, max_length=300)

    used_on: dt.date | None = None
    cleared_on: dt.date | None = None

    path: str | None = None
    """The write-up's file, relative to `[paths].investigations`.

    On the topic rather than on the day, so a topic drawn twice opens the same
    file the second time -- with whatever was written the first time still in
    it. That is what makes an unfinished day cost nothing.
    """

    folder: str | None = None
    """The subject area it was filed under, set when it is graded.

    Also the rating `area` -- one string, so the folder on disk and the rating
    it moves cannot drift apart. None until the day it is graded.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def open(self) -> bool:
        return self.used_on is None and self.cleared_on is None


class InvestigationDay(Base):
    """The topic one day drew.

    Nothing here records whether the work was done -- that lives on the
    `Investigation` this points at, which is what lets an unanswered day expire
    on its own. The row is the claim "this day drew this topic", and that claim
    stays true whatever happens next.
    """

    date: dt.date
    investigation_id: int
    drawn_at: dt.datetime | None = None

    write_up: str | None = None
    """What the file said when it was submitted.

    A snapshot, and duplicating the file on purpose. The file goes on being
    editable -- it is a file -- and a grade has to stay attached to the text
    that earned it. None means the day was drawn and never handed in.
    """

    written_at: dt.datetime | None = None

    ballot: InvestigationBallot | None = None
    """The graded result, pasted back. None until it is."""

    score: Score | None = None
    """`investigate.score_ballot`'s number, not the grader's own. Stored
    because it is what the rating event was computed from, and a rubric edit
    must not retroactively rewrite what a past day scored."""
