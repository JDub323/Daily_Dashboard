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

from pydantic import Field, computed_field, model_validator

from .common import (
    FEEL_NOTE_MAX,
    MEAL_LABEL_MAX,
    SESSION_FOCUS,
    Base,
    BathroomKind,
    ChessOutcome,
    ClockTime,
    DrinkName,
    DrinkTag,
    EntrySource,
    HealthMetric,
    InputState,
    Low,
    MathOutcome,
    MealFlag,
    MealSize,
    MealSlug,
    Rating,
    Score,
    SessionKind,
    Stool,
    Symptom,
    Tired,
    UrineColor,
    Url,
    Volume,
    canonical_feel,
    canonical_flags,
    canonical_tags,
    span_hours,
)
from .domain import (
    MAX_SENTENCES,
    SENTENCES,
    BathroomLog,
    DayCompletion,
    DrinkLog,
    FeelLog,
    Investigation,
    InvestigationDay,
    MathProblem,
    MealLog,
    Paper,
    PlanGroup,
    PrescribedBlock,
    RatingEvent,
    Readiness,
    SpellingWord,
    StudyPlan,
    TheoremCard,
    TodoItem,
    TopicInfo,
    TrainingSession,
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

    rubric_hit: list[bool] = Field(default_factory=list, max_length=6)
    """Which of a proof's checkpoints were ticked. Empty on a compute day.

    Stored beside the outcome rather than being turned into the score: the
    three-way self-report is what is rated, and the checkpoints are what make
    that report a claim about the mathematics instead of a feeling. Reading a
    `partial` back later and being able to see *which* half was missing is the
    reason to keep them.
    """


class MathCarryRequest(DateBody):
    """End day one of the two-day problem without a result.

    Its own endpoint rather than an outcome on `MathFinishRequest`, because it
    is the opposite of finishing: no score, no rating event, and the attempt
    stays alive into tomorrow."""


class MathOwnRequest(DateBody):
    """A problem you found and want to do instead.

    `source` is how you will recognise it in the archive a year from now, and
    it is UNIQUE in the database like every other problem's -- so reusing a
    label is a 409 rather than two rows called "the cylinder one".
    """

    statement: str = Field(min_length=20, max_length=4000)
    source: str = Field(min_length=3, max_length=120)


class StudySegmentRequest(DateBody):
    """Tick or skip one segment of Sunday's reading plan."""

    index: int = Field(ge=0, le=5)
    done: bool = True

    reason: str = Field(default="", max_length=200)
    """Why it was skipped -- "knew it", "no time". Ignored when `done`.

    The one field on this request worth having. Three segments are generated
    and one or two are expected, so a skip carries no information by itself;
    the reason is what the next generation needs in order to stop leading with
    material you already had."""


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

    today: dt.date | None = None
    """The browser's date, for resolving "wednesday" and "tomorrow".

    Optional rather than required, and deliberately not `DateBody`: the server
    can fall back to its own date, and the old contract for this endpoint took
    no date at all -- `test_calendar_add_takes_no_date` pins that a *stray*
    `date` key is still rejected.
    """

    calendar: str | None = None
    """The override. None means "use whatever the classifier picked".

    Set only when the user cycled the target away from the prediction, which
    is exactly the signal worth learning from -- the route records it.
    """


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


class InvestigateAddRequest(DateBody):
    """The day's list, pasted whole.

    One topic per line, and the box is the unit rather than the line -- the
    class hands out five or six at a time and typing them in one at a time is
    how a pool stops being filled. Same bargain `TodoWriteRequest` makes, and
    the same failure mode it avoids: a line that cannot be written comes back
    carrying its own reason rather than failing the other five.
    """

    text: str = Field(min_length=3, max_length=8000)


class InvestigateDrawRequest(DateBody):
    """No body beyond the date. The draw takes no argument -- picking is the
    server's job, and a client that could name the topic would be a reroll."""


class InvestigateClearRequest(DateBody):
    investigation_id: int


class InvestigateOpenRequest(DateBody):
    """No body beyond the date.

    Which file is opened is worked out from the day's draw. A client that could
    name a path would be naming the argument to an editor, which is the one
    thing this feature must never accept -- see `dashboard/term.py`.
    """


class InvestigateReadRequest(DateBody):
    """Take the file in as today's submission. No body beyond the date."""


class InvestigateBallotRequest(DateBody):
    """The graded result, pasted back whole.

    The whole reply rather than the JSON block, because asking for the block
    means asking you to find it -- the parser hunts for it, which is cheaper
    for you and no harder for it.
    """

    text: str = Field(min_length=2, max_length=60_000)


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


class TheoremOpenRequest(DateBody):
    """Put what is in the boxes into the file, before the editor opens it.

    The drafts travel *up* here, which is the opposite direction from every
    other body in this file, and it is the only reason this endpoint exists:
    the panel is holding text that has never been posted anywhere, and an
    editor that opened onto two empty sections would look exactly like having
    lost it.

    Both default empty and both are allowed to be, because the ordinary case
    is opening the editor to start writing. What is *not* here is a path --
    which file this is, is worked out from the day's draw. A client that could
    name one would be naming the argument to an editor, and `dashboard/term.py`
    exists on the promise that nothing can.
    """

    explanation: str = Field(default="", max_length=2000)
    application: str = Field(default="", max_length=2000)


class TheoremArchiveRequest(DateBody):
    card_id: int
    archived: bool = True


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

    source: EntrySource = EntrySource.manual
    """Defaulted rather than required: everything the browser posts is typed by
    hand, and only an importer has any business saying otherwise."""


MAX_IN_BED_HOURS = 14
"""The longest night in bed the check-in will take. See ."""


class ReadinessRequest(DateBody):
    """All five at once. The panel posts the whole row on every tap, so a
    half-answered questionnaire is not a state the table can be in."""

    sleep_quality: int = Field(ge=1, le=5)
    fatigue: int = Field(ge=1, le=5)
    soreness: int = Field(ge=1, le=5)
    stress: int = Field(ge=1, le=5)
    motivation: int = Field(ge=1, le=5)

    bed: ClockTime | None = None
    woke: ClockTime | None = None
    """Into bed and out of it. Optional here and asked for on the phone, for
    the reason `SessionRequest.started` gives: a check-in queued by the build
    before these existed is still owed delivery."""

    @model_validator(mode="after")
    def _a_whole_night(self) -> ReadinessRequest:
        """Both or neither, and a night a person could have had.

        Fourteen hours is the ceiling because the error it catches is the one
        a time wheel invites: bed at 11 in the *morning* and up at 7 reads as
        twenty hours, and a plausible-looking twenty is worse than a 422."""
        if (self.bed is None) != (self.woke is None):
            raise ValueError("bed and woke come together or not at all")
        if self.bed is not None and self.woke is not None:
            hours = span_hours(self.bed, self.woke)
            if not 0 < hours <= MAX_IN_BED_HOURS:
                raise ValueError(f"{self.bed} to {self.woke} is not a night in bed")
        return self


PlanKey = Annotated[str | None, Field(default=None, min_length=3, max_length=64)]
"""`<date>:<token>`, or None for a session logged freehand."""


class SessionRequest(DateBody):
    kind: SessionKind
    rpe: int = Field(ge=1, le=10)
    minutes: int = Field(ge=1, le=600)
    note: str | None = Field(default=None, max_length=200)

    started: ClockTime | None = None
    """When it began, after the warm-up. Required by the phone and optional
    here, on purpose: the queue on the phone may still hold a session posted by
    the build before this field existed, a 4xx is what the queue drops, and a
    session is worth more than its start time."""

    quality: int | None = Field(default=None, ge=1, le=5)
    """How well it went, 1-5. Optional, like `note` and for the same reason:
    a body posted before this field existed is still a legal body, and a
    session nobody was asked about is a real session."""

    focus: str | None = Field(default=None, max_length=64)
    """What the session was, one level below `kind` -- a dotted leaf of
    `SESSION_FOCUS`, or None where the tree was not walked that far.

    Defaulted for the same reason `plan_key` is: the body that was posted
    before this field existed is still a legal body, and declining to go a
    level deeper is a real answer rather than a missing one."""

    plan_key: PlanKey = None
    """Which prescribed block this answers. Defaulted, so the freehand form --
    which stays -- posts exactly the body it always did."""

    @model_validator(mode="after")
    def _focus_agrees_with_kind(self) -> SessionRequest:
        """A leaf implies its kind, and a body where the two disagree is
        refused.

        This is the only thing keeping the two columns honest, and it has to
        live here: SQLite cannot express "this text implies that enum" without
        the tree being hard-coded into the schema, which is the one thing
        migration 33 set out to avoid.

        Refusing rather than deriving `kind` from `focus`. Deriving would be
        tidier and would quietly rewrite a field the caller stated -- and the
        caller that states it is the plan, which names a kind for the block and
        has the better claim to it. A contradiction here is a client bug, and a
        client bug that silently produces a plausible row is one nobody finds.

        The unknown-leaf case is refused too, and that is the whole reason the
        vocabulary is enforced at the boundary rather than by a CHECK: a typo
        in the tree would otherwise write a category that exists in exactly one
        row and is never seen again.
        """
        if self.focus is None:
            return self
        implied = SESSION_FOCUS.get(self.focus)
        if implied is None:
            raise ValueError(f"{self.focus} is not a session focus")
        if implied is not self.kind:
            raise ValueError(f"{self.focus} is {implied.value}, not {self.kind.value}")
        return self


class BathroomRequest(DateBody):
    """One visit. `hour` is the browser's, rounded down -- [Invariant 1]
    applies to the clock exactly as it applies to the calendar."""

    hour: int = Field(ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    kind: BathroomKind
    volume: Volume
    solidity: Stool | None = None
    color: UrineColor | None = None

    @model_validator(mode="after")
    def _one_field_per_kind(self) -> BathroomRequest:
        """Stool carries a solidity, urine carries a colour, neither carries
        both.

        The table enforces this too, and this is not redundant with it: a body
        that fails here is a 422 naming the field, while the same body reaching
        SQLite is an IntegrityError and a 500 with a CHECK constraint in it.
        Same rule, stated twice, because the two statements have different
        audiences.
        """
        wanted = "solidity" if self.kind is BathroomKind.stool else "color"
        unwanted = "color" if self.kind is BathroomKind.stool else "solidity"
        if getattr(self, wanted) is None:
            raise ValueError(f"a {self.kind.value} needs a {wanted}")
        if getattr(self, unwanted) is not None:
            raise ValueError(f"a {self.kind.value} has no {unwanted}")
        return self


class MealRequest(DateBody):
    hour: int = Field(ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    size: MealSize
    quality: int = Field(ge=1, le=5)
    """1 = as processed as it gets, 5 = whole ingredients."""

    preset: MealSlug | None = None
    """What this meal was, or None. A name fills `size`, `quality` and `flags`
    in on the client and then stops mattering -- it is posted so the row can
    say what it was, not so the server can expand it. Expanding it here would
    make a name's meaning a thing that changes under rows already written, and
    a learned name's fills change by design."""

    label: str | None = Field(default=None, min_length=1, max_length=MEAL_LABEL_MAX)
    """The words for `preset` -- only the last part, `salsa` and not `chicken +
    rice · salsa`. Read only when the slug is new, which is how a name typed on
    the phone reaches the table: it travels with the first meal that carries it,
    through the queue, so naming a meal in a kitchen with no laptop in reach
    costs nothing extra.

    Never a reason to refuse the body. A slug the server has never seen and no
    label is still a meal, filed under the slug spelled out -- this body may
    have spent a day in a phone's queue, and a 422 is a drop."""

    flags: list[MealFlag] = Field(default_factory=list, max_length=len(MealFlag))
    """What was in it. Defaulted and canonicalised exactly as `DrinkRequest`
    does its tags, and for the same reasons -- an empty list is a real answer,
    and order is decided here so two clients cannot write the same meal two
    ways."""

    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def canonicalise(self) -> MealRequest:
        self.flags = canonical_flags(self.flags)
        return self


class DrinkRequest(DateBody):
    """One drink. `amount` is in drinks -- see `DrinkLog` for what a drink is,
    and for why alcohol counts differently."""

    hour: int = Field(ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)

    tags: list[DrinkTag] = Field(default_factory=list, max_length=len(DrinkTag))
    """What was in it, and none of it is a legitimate answer: an empty list is
    a glass of water. That is why this has a default rather than being required
    -- the commonest drink of the day posts no tags at all, and a required
    field would make the plain case the one that needs a special value.

    Order and repeats are the client's business to get wrong and this model's
    to fix: `canonical_tags` below dedupes and sorts, so a phone that sends
    `[caffeine, sugar]` and a script that sends `[sugar, sugar, caffeine]` write
    the same row. The `max_length` is the fat-finger guard the amount box has
    -- there are six tags, and a body with fifty is not a drink."""

    name: DrinkName | None = None
    """What it was, or None -- which is what "other" means on the phone and is
    the ordinary answer.

    Deliberately *not* validated against `tags`. The tree in `logs.js` decides
    which names it offers for a given set of tags, and that is a question about
    what is worth putting on a screen; making it a rule here would mean a
    reasonable body -- a beer logged without remembering to say carbonated --
    coming back a 422 for a disagreement about presentation. The tags are the
    scored half and they stand on their own; the name adds to them and cannot
    contradict them."""

    amount: float = Field(gt=0, le=20)
    """The bounds are the table's, restated here so a typo comes back as a 422
    naming the field rather than as a CHECK constraint in a 500. The panel
    parses the box itself and will not post a number outside them, which makes
    this the guard for everything that is not the panel."""

    @model_validator(mode="after")
    def canonicalise(self) -> DrinkRequest:
        """One spelling per drink, decided at the boundary.

        Here rather than in the repository because this is the edge every
        client comes through, and a row that reached the table with its tags in
        posting order would be a row read back in an order that depends on who
        wrote it.
        """
        self.tags = canonical_tags(self.tags)
        return self


class LogDeleteRequest(DateBody):
    """Undo, for either log. POST rather than DELETE and the id in the body
    rather than the path, for the reason `/api/session/delete` gives: the
    response is the rebuilt panel, and rebuilding it needs a date only the
    browser can supply."""

    entry_id: int


Grade = Annotated[int, Field(ge=1, le=3)]
"""A symptom's severity: 1 mild, 2 moderate, 3 severe. Jackson's 0 is the
symptom not being in the body at all."""


class FeelRequest(DateBody):
    """How you feel right now. See migration 39 and `FeelLog`."""

    hour: int = Field(ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    energy: int = Field(ge=1, le=5)
    """1 empty, 5 charged."""
    mood: int = Field(ge=1, le=5)
    """1 awful, 5 great."""

    tired: list[Tired] = Field(default_factory=list, max_length=len(Tired))
    low: list[Low] = Field(default_factory=list, max_length=len(Low))
    symptoms: dict[Symptom, Grade] = Field(default_factory=dict)

    note: str | None = Field(default=None, max_length=FEEL_NOTE_MAX)
    """The `other` box. Blank is the same as absent."""

    prompted: bool = False

    @model_validator(mode="after")
    def _kinds_follow_their_scale(self) -> FeelRequest:
        """A kind of tired needs a tired answer, and a kind of low a low one.

        Refused rather than dropped, which is the arrangement `SessionRequest`
        has with `focus`: the phone prunes these as the scale moves (`when` on
        the step, and `prunedDraft`), so a body that disagrees is a client bug,
        and a bug the server quietly repairs is one nobody ever finds.
        """
        if self.tired and self.energy > 2:
            raise ValueError("a kind of tired needs energy at 2 or under")
        if self.low and self.mood > 2:
            raise ValueError("a kind of low needs mood at 2 or under")
        self.tired = canonical_feel(self.tired, Tired)
        self.low = canonical_feel(self.low, Low)
        self.symptoms = {s: self.symptoms[s] for s in Symptom if s in self.symptoms}
        self.note = self.note or None
        return self


class PushKeys(Base):
    p256dh: str = Field(min_length=10, max_length=256)
    auth: str = Field(min_length=4, max_length=64)


class PushSubscribeRequest(Base):
    """`PushSubscription.toJSON()`, exactly as a browser hands it over, so the
    phone posts what it was given and nothing it had to assemble."""

    endpoint: str = Field(min_length=10, max_length=2048, pattern=r"^https://")
    expirationTime: int | None = None
    keys: PushKeys


class PushTestRequest(Base):
    """A ping now, to see that they arrive. Carries nothing."""


class PushStatus(Base):
    """What the laptop can do about pings, said to the phone.

    `public_key` is the application server key a subscription is made against,
    or None when pings cannot be sent at all -- `reason` says why, because the
    fix (install `pywebpush`, switch `[feel].pings` on) is on the laptop."""

    public_key: str | None = None
    reason: str | None = None
    subscriptions: int = 0
    sent: int = 0
    """For a test ping: how many phones it reached."""


UsageScreen = Annotated[str, Field(pattern=r"^[a-z][a-z._-]{0,31}$")]
"""A phone screen's name as `web/m/tally.js` counts it: `eat`, `eat.drinks`."""


class UsageRequest(Base):
    """The phone's screen-open counts, as many days as it still holds.

    Counts rather than events, so a resend is harmless -- `usage.merge` keeps
    the larger of the two. The bounds are the fat-finger guard: the phone keeps
    four weeks, and there are about ten screens."""

    days: dict[dt.date, dict[UsageScreen, Annotated[int, Field(ge=0, le=100_000)]]] = Field(
        max_length=62
    )

    @model_validator(mode="after")
    def _few_screens(self) -> UsageRequest:
        if any(len(screens) > 40 for screens in self.days.values()):
            raise ValueError("more screens in one day than the phone has")
        return self


class UsageReply(Base):
    usage_days: int
    """How many days the laptop now holds counts for."""


class GarminSyncRequest(DateBody):
    """A sync the user asked for, as against the one that runs itself.

    `days` is how far back to re-pull. Defaulted to a few, because the watch
    revises last night's sleep for a while after you get up, and a window that
    only ever covered today would freeze the first draft of it.
    """

    days: int = Field(default=3, ge=1, le=28)


class SessionPart(Base):
    """One row of a card. Same shape as a session minus the date, which the
    card carries once for all of them."""

    kind: SessionKind
    rpe: int = Field(ge=1, le=10)
    minutes: int = Field(ge=1, le=600)
    note: str | None = Field(default=None, max_length=200)
    plan_key: PlanKey = None


class SessionCardRequest(DateBody):
    """A whole prescribed outing, logged in one go.

    A practice is one thing that happened and, at John's instruction, several
    rows -- its conditioning and its technique are logged apart so the two
    volumes stay separable. That decision costs four fields a day; sending them
    as one request is what stops it also costing two round trips and a
    half-written day when the second one fails.
    """

    parts: list[SessionPart] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _keys_are_distinct(self) -> SessionCardRequest:
        keys = [p.plan_key for p in self.parts if p.plan_key]
        if len(keys) != len(set(keys)):
            raise ValueError("a card cannot log the same prescribed block twice")
        return self


class SessionDeleteRequest(DateBody):
    session_id: int


class CampPauseRequest(DateBody):
    """Stop the camp, from `date`.

    `reason` is required and has no default. That is the one piece of friction
    this control is supposed to have: pausing a camp is a decision you want to
    be able to read back in February, and a reason that could be skipped is one
    that would be.
    """

    reason: str = Field(min_length=1, max_length=200)


class CampResumeRequest(DateBody):
    """Start it again -- on the Sunday derived from `date`, never on `date`.

    The resume date is computed server-side from the browser's today rather
    than sent, because there is exactly one legal answer and a field that can
    only hold one value is a field a client can get wrong.
    """

    cancel: bool = False
    """Take back a resume that has not arrived yet, rather than setting one.

    One endpoint and a flag, rather than two routes, because they are the same
    decision in two directions and the thing that must not drift between them
    is which break they act on."""


class DayLogRequest(DateBody):
    text: str = Field(max_length=2000)


class PianoPracticeRequest(DateBody):
    #: False is a real value here -- the tick unticks, for the evening you
    #: press it and then do not get to the keyboard after all.
    practiced: bool = True


class CompletionRequest(DateBody):
    done: int = Field(ge=0)
    total: int = Field(gt=0)

    bonus: int = Field(default=0, ge=0)
    """The optional half, in points. Not part of done/total -- see
    `DayCompletion.bonus`. Defaulted rather than required so a client that
    predates the split still posts a valid body."""

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

    mode: str = "compute"
    """What today asks for, from `[math.week]`. The panel renders a different
    surface for each: a ladder rung, a proof, or a reading plan."""

    proof: AreaRating | None = None
    """The `proofs` bucket -- the rating four days of the week actually move.

    Sent beside `summary` rather than inside `areas`, because it is not one of
    the `[math.areas]` buckets and must not appear in the strip that iterates
    them. On a proof day the panel shows this as the headline instead of the
    pooled rating, which is the number that is standing still those days."""

    plan: StudyPlan | None = None
    """Sunday's reading plan, when there is one. Unrated, and the only day the
    hero panel holds something that is not a problem."""

    problem: MathProblem | None = None
    hints_revealed: int = 0
    status: str = "open"
    score: Score | None = None

    day: int = 1
    """Which day of a two-day problem today is."""

    hint_budget: int = 3
    """How many hints today is allowed to reveal -- *not* how many the problem
    has.

    Zero on day one of the two-day problem, whose two hints exist and are not
    reachable until tomorrow. The panel renders the button from this, and the
    server enforces it; the difference between the two numbers is the barrier.
    """

    free_day: bool = False
    """Day two of a two-day problem that was solved on day one.

    The day off that a day-one solve buys. Nothing is drawn and nothing lapses;
    the panel offers a problem of your own instead, and that one is worth a
    bonus point rather than a rating change."""

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


class TheoremScratch(Base):
    """The editor's file, as the two boxes it is a form of.

    Returned by both ends of the round trip -- opening the editor and reading
    it back afterwards -- because they are the same question asked twice, and a
    second shape would be a second thing to keep in step.
    """

    path: str
    """Where it is on disk, so the panel can say so. The file is a real file
    and opening it in a real terminal is a supported way to use this, which is
    the same bargain the investigation write-up makes."""

    scrollback: int
    explanation: str
    application: str


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
    """Null until the day's set is drawn, which is still a POST of its own
    rather than part of the boot payload -- drawing opens the round, and the
    boot payload is a read. `daily.js` makes that call at boot, so by the time
    anyone is looking this is null only on a morning the night's file is
    missing."""

    batched: bool = False
    """Whether tonight's file is on disk.

    What the boot gather reads to decide whether to draw at all: with nothing to
    draw from, `POST /api/trick/new` can only raise, and a page load should not
    put an error under a panel whose real answer is "the 3am task did not run".
    It is also the difference between the two empty states the panel can show.
    """

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
    panel, which has nowhere to put an investigate-track number) and the moves
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
    """The pattern-recall rating: the investigate track's `argument-patterns`
    bucket. Deliberately not one of the folders — see `ARGUMENT_AREA`."""

    events: int = 0
    """Graded recalls behind that rating. Zero means it is the pooled
    investigation rating standing in, not something earned."""

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


class InvestigatePanel(Base):
    """Today's investigation: what was drawn, and what is left to draw from.

    Two states so far -- nothing drawn, and drawn -- which is why `today` is
    nullable rather than the panel carrying a status enum. The states the
    write-up and the ballot add hang off the same row, so the shape holds.

    The whole pool travels with the payload. It is a few hundred short strings
    at the very most and the panel lists them so they can be struck off, which
    is the only way the pool ever shrinks without a day being spent.
    """

    today: Investigation | None = None
    """The topic this day drew, or None before the button is pressed.

    [Invariant] Drawing is a button. A panel that drew on render would spend a
    topic every morning the politics tab was painted, which is every morning --
    the rule the theorem draw and the math problem both hold.
    """

    drawn_on: dt.date | None = None
    """The date `today` was drawn for. Echoed back so the panel can tell "drawn
    this morning" from a stale payload it is about to replace."""

    day: InvestigationDay | None = None
    """The draw row, carrying the submitted snapshot if there is one."""

    scrollback: int = 1000
    """Lines the embedded terminal keeps. Carried on the panel rather than in
    `/api/config` because this panel is its only reader, and a global config
    field with one consumer is a field nobody can safely change."""

    file_body: str = ""
    """What is in the file right now, minus the scaffolded heading.

    Read from disk on every render rather than cached. The file is deliberately
    openable from a terminal the app knows nothing about, so a stored "written"
    flag would go on claiming a write-up that had since been emptied.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def written(self) -> bool:
        """Whether there is anything to submit. The file, not the snapshot --
        a day is written when you have typed something, not when the panel
        last noticed."""
        return bool(self.file_body)

    prompt: str | None = None
    """The assembled prompt, or None before anything has been read back.

    Sent with the panel rather than fetched on click, so
    the copy button is instant and what you copy is what you were looking at.
    """

    rubric: list[RubricLine] = Field(default_factory=list)
    """The five lines, so the panel can label a ballot it did not assemble."""

    summary: TrackSummary
    delta: int | None = None
    """What today's ballot did to the pooled investigation rating."""

    folders: list[AreaRating] = Field(default_factory=list)
    """Per-folder ratings, strongest first — the investigation track's answer
    to `MathPanel.areas`, and read the same way: the last line is the weak one."""

    pool: list[Investigation] = Field(default_factory=list)
    """Everything still open, oldest first -- the order they were handed out,
    which is the only order the list has ever had."""

    pool_size: int = 0
    investigated: int = 0
    """Topics carried all the way to a grade. Not `len(pool)`'s complement:
    struck-off topics are neither."""


class InvestigateLine(Base):
    """What one pasted line became.

    Exactly one of `topic` and `error` is set. The box needs both halves: a
    paste of six where the fourth was a stray page number should say so, and a
    paste where every line was already in the pool should not look like a
    success.
    """

    raw: str
    topic: str | None = None
    error: str | None = None


class InvestigateAddResponse(Base):
    """The panel, plus what the box did with each line.

    Two fields rather than folding the report into the panel: the report is
    about one request and is gone on the next render, while the panel is the
    state of the world. A panel carrying the last paste's errors would show
    them again after an unrelated redraw.
    """

    panel: InvestigatePanel
    lines: list[InvestigateLine] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def added(self) -> int:
        return sum(1 for line in self.lines if line.error is None)


class FoulCount(Base):
    kind: str
    count: int


class HealthSeries(Base):
    metric: HealthMetric
    points: list[tuple[dt.date, float]] = Field(default_factory=list)


class ZoneInput(Base):
    """One reading feeding one of the rail's four bars.

    Carried across the wire individually rather than summed server-side,
    because the zoom draws each of them as its own small bar against its own
    baseline -- and because a composite is only defensible if the thing it is
    composed of is one keystroke away. A number you cannot take apart is a
    number you end up believing.
    """

    key: str
    """Stable id — what the panel keys its rows off."""

    label: str
    """What the row is called on screen. Lower case, like everything else."""

    state: InputState
    """Whether this input scored, and if not, which kind of absence it is.
    See `InputState`: a charger night and a rest day must not read alike."""

    value: float | None = None
    """0-100, and only when `state` is `scored`. Every input is normalised to
    the same scale so the zoom's bars are comparable side by side; what each
    100 *means* is that input's own business and lives in `health.py`."""

    reading: str | None = None
    """The raw fact, in words -- `58 ms against a 62 baseline`. The score is
    what the bar draws; this is what makes it checkable, and it is the half a
    normalised number throws away."""


class ZoneScore(Base):
    """One bar on the rail, and everything under it.

    `value` is the mean of whichever inputs actually scored, equally weighted.
    Equal weights are a judgement and meant to be argued with -- what they buy
    is that the number cannot be quietly steered by a weighting nobody
    remembers choosing, and the zoom shows the parts, so a bar dragged down by
    one input is one keystroke from saying which.
    """

    value: float | None = None
    """0-100, or None when not one input could be scored honestly. None renders
    as words rather than as an empty bar -- see `note`."""

    inputs: list[ZoneInput] = Field(default_factory=list)

    note: str | None = None
    """Said under the bar whenever it is standing on less than all of itself:
    which inputs are missing and why. Absent when everything scored, because a
    bar with nothing wrong with it should say nothing at all."""


class HealthDerived(Base):
    """Everything computed rather than typed. See `dashboard/health.py`.

    Nearly every field is nullable, and that is the design: each one is absent
    until there is enough history behind it to be worth acting on, and the
    panel renders an absent field as nothing at all rather than as a dash or a
    zero. A quiet health tab in week one is the correct health tab.
    """

    acwr: float | None = None
    """This week's mean daily load over your four-week normal."""

    monotony: float | None = None
    strain: float | None = None
    """Weekly load x monotony. Volume weighted by how unrelenting it was."""

    week_load: int = 0
    prev_week_load: int = 0

    weight_ema: float | None = None
    weight_per_week: float | None = None

    # -- the rail ------------------------------------------------------------
    #
    # Four bars, each a mean over whichever of its inputs could be scored
    # honestly. They are composites, which this panel spent its first two
    # passes refusing to build -- see the note at the head of `panels/health.js`
    # for what changed and why. The short version is that a composite is only
    # as defensible as its parts are reachable, and these parts are one
    # keystroke away.

    recovery: ZoneScore = Field(default_factory=ZoneScore)
    load: ZoneScore = Field(default_factory=ZoneScore)
    fuel: ZoneScore = Field(default_factory=ZoneScore)
    hydration: ZoneScore = Field(default_factory=ZoneScore)

    readiness_week: float | None = None
    """Mean score over the last seven answered mornings."""

    readiness_baseline: float | None = None
    readiness_sd: float | None = None
    readiness_dip_days: int = 0
    """Consecutive mornings a full SD below baseline. The panel speaks at 3."""

    # -- overnight, once the watch is filling these in ----------------------

    sleep_week: float | None = None
    """Mean hours asleep over the last seven nights, from however many of them
    have a reading."""

    sleep_last: float | None = None
    in_bed_last: float | None = None
    """Last night. The pair rather than either alone: nine in bed and six
    asleep is a different problem from six of each, and only the gap says
    which one you have."""

    sleep_score_last: float | None = None

    hrv_last: float | None = None
    hrv_baseline: float | None = None
    hrv_dip_days: int = 0
    """Consecutive nights a full SD below your own trailing month. Not a
    number against a population range -- HRV's absolute scale is genetic, and
    a fortnight of your own is worth more than any published band."""

    rhr_last: float | None = None
    rhr_baseline: float | None = None
    rhr_spike_days: int = 0
    """The same, upward. A resting heart rate sitting above its own baseline
    for days is the classic non-functional-overreaching tell."""

    body_battery_last: float | None = None

    stress_last: float | None = None
    """Today's all-day stress average, 0-100, lower being calmer."""

    # -- the two slow trends -------------------------------------------------
    #
    # Not bars, and that is the whole reason they sit apart from the rail:
    # neither has a 0-100 direction without a target, and nobody has set one.
    # A weight of 172 is not 60% of anything.

    vo2max_last: float | None = None
    """The most recent estimate on file, whenever it arrived. `latest`
    elsewhere means "today or nothing"; here it deliberately does not, because
    an estimate only updates after an outdoor run and a rule that showed it
    only on the day it moved would show it about twice a month."""

    vo2max_on: dt.date | None = None
    """Which day `vo2max_last` came from, so the panel can say how stale it is
    rather than implying it is today's."""

    vo2max_change: float | None = None
    """Change over the trailing quarter, in ml/kg/min. None until there are
    two readings far enough apart to subtract -- a difference of two numbers a
    week apart is noise with a sign on it."""

    # -- in and out ---------------------------------------------------------

    dark_urine_days: int = 0
    """Consecutive days whose urine read dark, first void of each day thrown
    away. The panel speaks at 3."""

    hydration_ml: float | None = None
    """Mean ml a day tapped into the watch this week. Undercounts by whatever
    was forgotten, which is why it is printed beside the colour rather than
    instead of it."""

    liquid_days: int | None = None
    days_since_stool: int | None = None
    """Both None -- not zero -- when the log was too sparse to ask. Zero here
    means "none this week" and "went today", and those are answers."""

    intake_est: float | None = None
    """Mean estimated kcal in a day this week. An estimate over three buckets;
    `health.MEAL_KCAL` is the assumption and is meant to be argued with."""

    expenditure: float | None = None
    """Mean kcal out a day this week, as the watch measured it."""

    balance: float | None = None
    """`intake_est - expenditure`, and only when both exist."""

    balance_lb_per_week: float | None = None
    """What that balance predicts the scale will do. Printed *next to*
    `weight_per_week`, which is what the scale actually did -- the pair is the
    point, because when they disagree it is the estimate that is wrong."""

    diet_quality: float | None = None
    """Mean 1-5 over the week's meals: how whole the food was."""

    fluid_ml: float | None = None
    """Mean ml a day out of the drink log, alcohol excluded.

    The same quantity `hydration_ml` reports and a better measurement of it:
    that one is a glyph on a watch face you remember to tap, this one is the
    thing you were going to log anyway. Both are carried so the hero can prefer
    this and still say something on a week it was not kept."""

    caffeine_drinks: float | None = None
    """Mean caffeinated drinks a day this week -- twelve ounces each."""

    alcohol_week: float | None = None
    """Standard drinks in the last seven days. A total, not a mean, and 0.0 is
    a real answer that only exists when the log was being kept."""

    sweat_loss_ml: float | None = None
    """Mean ml a day the watch reckons were sweated out this week.

    Not a thing to be good or bad at -- it is the term that moves the fluid
    target, which is the entire reason it replaced `hydration_ml` as the
    watch's hydration input. A hard week needs more water than an easy one and
    a fixed daily goal cannot know that."""

    vert_jump_last: float | None = None
    """Inches, best of three, from the last testing day."""

    vert_jump_on: dt.date | None = None
    vert_jump_points: int = 0
    """How many testing days are on file. The load zoom stays quiet about the
    jump until there are about ten -- a trend through four points measured six
    weeks apart is a line drawn through nothing."""


class GarminStatus(Base):
    """Whether the watch is talking to us, said plainly.

    Every field here exists because this integration is built on an unofficial
    client against an API that has already broken it once -- `garth` was killed
    by Garmin's March 2026 auth change. So the panel never claims the numbers
    are current; it says when they last arrived, and what went wrong if they
    did not. A sync that has been quietly failing for a week is the failure
    mode worth designing against, since every reading it feeds degrades to
    silence rather than to an error.
    """

    installed: bool = False
    """`garminconnect` importable in this venv."""

    configured: bool = False
    """Enabled in config *and* an email in the environment."""

    last_sync: dt.datetime | None = None
    last_ok: bool = True
    last_error: str | None = None
    wrote: int = 0
    """Rows written by the last run. Zero after a successful run is ordinary:
    the window has usually already been pulled."""


class CampPause(Base):
    """The camp, stopped: since when, why, and what coming back would cost.

    Everything here is derived at read time from one `camp_break` row and the
    browser's `today`. Nothing is stored in this shape, which is what keeps
    `days` from being a number that was right on the morning it was written.
    """

    since: dt.date
    reason: str
    days: int = Field(ge=1)
    """Inclusive, so the day you stop is day 1. It is a count of days spent
    ill, and the day you went to bed was one of them."""

    resumes: dt.date
    """The Sunday the camp comes back on -- the one already committed to, or
    the one pressing resume today would use. The panel prints it either way,
    because the point of showing it is that the arithmetic is on screen
    *before* it is agreed to rather than discovered a week later."""

    committed: bool = False
    """Whether `resumes` has been pressed or is still hypothetical.

    The gap between the two is real and ordinary: resume on a Friday and there
    are two more days of nothing prescribed before the camp restarts. A panel
    that could not tell those apart would be offering a button that had already
    been pressed."""

    restarts_week: int | None = None
    """The camp week that runs again from `resumes`. The week you fell ill in,
    in full -- see `trainplan.shift` for why it is the whole week and not the
    remainder of it."""


class TrainingPlan(Base):
    """The camp: how far out the fight is, and what today asks for.

    Every field here is allowed to be absent, and absent renders as silence
    rather than as a placeholder -- the same rule `HealthDerived` follows. There
    is no plan for eleven of the twenty-eight weeks and that is a real state,
    not a gap to paper over.
    """

    name: str
    fight: dt.date | None = None
    days_out: int | None = None
    """None when no fight is set or it has already happened."""

    week: int | None = None
    """Which week of the camp today is, or None outside it."""

    weeks: int

    today: list[PrescribedBlock] = Field(default_factory=list)
    """Empty on a rest day *and* on an unwritten week -- `week_planned` is what
    tells those apart, and the difference matters: one of them is the plan
    working."""

    week_planned: bool = False
    """Whether any block covers this week at all. False through weeks 0-2 (the
    back-of-napkin block) and 8-18 (undecided until VJP has been run and the
    mentor consulted), and those weeks must not read as prescribed rest.

    Says nothing about `[[recurring]]`: practice runs through the unwritten
    weeks and is the whole reason it lives in its own layer. A week can have no
    block and still ask for something every day."""

    groups: list[PlanGroup] = Field(default_factory=list)
    """The point-scoring bundles today offers -- one per standing commitment
    that is running. `chips.js` reads these; the widgets read `today`."""

    ends: dt.date | None = None
    """The last date any block covers. The panel says this out loud once it is
    behind us, because a schedule that has run out and a schedule that says
    "rest" look identical from the inside, and only one of them means you have
    a decision to make."""

    paused: CampPause | None = None
    """Set only while the camp is stopped. Its presence is the state -- there
    is no boolean beside it that could disagree with it."""


class MealFills(Base):
    """What tapping a name answers for you, learned from how it was logged --
    see `menu.learned`. Every field stays editable on the phone; this is the
    starting point, not the answer."""

    size: MealSize
    quality: int = Field(ge=1, le=5)
    flags: list[MealFlag] = Field(default_factory=list)


class MealName(Base):
    """One entry on the meal menu, as `dashboard/menu.py` reads it today.

    Every name ever given is sent, hidden ones included, because the phone's
    name box searches all of them -- that search is what stops "chicken rice"
    and "chicken + rice" becoming two meals. `live` is what decides whether it
    is drawn in the tree."""

    slug: str
    label: str
    """Its own words only. A variation's is `salsa`; the phone puts its
    parent's in front where it is drawn away from the parent."""

    parent: str | None = None
    """The slug before the dot, or None for a base meal."""

    live: bool = False
    """On the menu: earned (three times inside three weeks) or given, and eaten
    inside the last thirty days."""

    rank: int = 0
    """Position among its siblings, 0 first. Moves at most once a week -- see
    `menu.rank_anchor` for why a menu that reshuffles daily is worse than one
    that is slightly out of date."""

    fills: MealFills | None = None
    """None until it has been logged at least once (or its parent has)."""

    hours: list[int] = Field(default_factory=lambda: [0] * 24)
    """How many times it was eaten at each hour over the last thirty days --
    what the phone's "likely now" row is picked from, against the hour it is
    logging for rather than the hour it is."""

    count: int = 0
    """Times logged, ever."""

    last: dt.date | None = None
    """When it was last eaten."""


class FeelPing(Base):
    """One of the day's pings, and what became of it.

    `state` is read off the feel log rather than stored: `answered` when a feel
    was logged inside the window (before the ping or after it), `sent` while it
    is out and waiting, `missed` once the window closed with nothing in it --
    which is never the same as fine -- and `waiting` before its minute.
    """

    slot: int
    name: str
    start: ClockTime
    end: ClockTime
    state: str
    sent: ClockTime | None = None


class FeelSignal(Base):
    """One thing measured in the hours before a feeling, against your normal.

    `dev` is how far from normal, on one scale for every signal: 0 is normal, 1
    is worth a look, 2 and up is far. What one unit means is each signal's own
    business and lives in `dashboard/feel.py`; what matters is that the list can
    be sorted by it. `tone` is how it is drawn: `hi`, `mid`, `lo`, or `check` --
    gold, the app's word for "this may be the log and not you".
    """

    key: str
    label: str
    value: str
    normal: str | None = None
    tag: str
    dev: float
    tone: str
    note: str | None = None


class FeelCount(Base):
    """Once a word has enough flags: how often one signal was off before it,
    against how often it was off before every other feel."""

    key: str
    flagged: int
    of: int
    otherwise: int
    of_other: int


class FeelLookback(Base):
    """What came before the day's latest flagged feel."""

    entry: FeelLog
    signals: list[FeelSignal] = Field(default_factory=list)
    suspects: dict[str, list[str]] = Field(default_factory=dict)
    """For each word on the entry, which signals are its suspects, by key.
    `other` has none chosen ahead of time and is ranked on everything."""
    flags: dict[str, int] = Field(default_factory=dict)
    """How many times each of the entry's words has been flagged, all told."""
    counts: dict[str, list[FeelCount]] = Field(default_factory=dict)
    """Only for words with enough flags behind them -- see `feel.MIN_FLAGS`."""


class FeelPanel(Base):
    """Everything the feel log adds to the health panel, beside its rows."""

    pings: list[FeelPing] = Field(default_factory=list)
    """Empty when `[feel].pings` is off -- which also takes the ping tasks off
    the day's list, since a point you cannot be asked for is not on offer."""
    flag: str | None = None
    """The flag line. None is "everything looks normal"."""
    landing: str | None = None
    """How the week's load is landing, in one line under the load zone."""
    lookback: FeelLookback | None = None


class HealthPanel(Base):
    """The health tab's whole payload.

    Replaces the bare `list[HealthSeries]` the boot used to carry. The series
    alone could only ever produce two charts; the questions this tab exists to
    answer -- am I recovering, am I training enough, which way is my weight
    going -- need the sessions and the derived block beside them.
    """

    series: list[HealthSeries] = Field(default_factory=list)
    readiness_today: Readiness | None = None
    sessions_today: list[TrainingSession] = Field(default_factory=list)

    bathroom_today: list[BathroomLog] = Field(default_factory=list)
    meals_today: list[MealLog] = Field(default_factory=list)
    drinks_today: list[DrinkLog] = Field(default_factory=list)
    """Today only. The windows behind the derived block are read server-side
    and stay there -- these three tables produce four to sixteen rows a day,
    and a month of them crossing the wire on every boot to redraw one list
    would be the largest thing in the payload by some margin."""

    feel_today: list[FeelLog] = Field(default_factory=list)
    """Today's feel rows, for the phone's list -- the same shape as the three
    above it, so the phone draws it the same way."""

    feel: FeelPanel = Field(default_factory=FeelPanel)

    meal_menu: list[MealName] = Field(default_factory=list)
    """The meal log's names -- the menu the phone draws and the vocabulary its
    name box searches. On this panel because every meal write answers with
    this panel, so a name the phone just sent comes back already on it."""

    garmin: GarminStatus = Field(default_factory=GarminStatus)

    synced_today: list[HealthMetric] = Field(default_factory=list)
    """Which of today's scalars the watch wrote, rather than a hand.

    A separate list instead of a third element on every `HealthSeries` point,
    because only one day's provenance is ever needed on screen and widening the
    point tuple would touch both charts to answer a question they do not ask.

    What it buys is one sentence on the morning panel: a box the watch has
    already filled is a box you can skip, and a box you typed is one you are
    responsible for. `Repository.log_health` guarantees the second outranks the
    first for good, so the distinction stays true rather than being this
    morning's accident."""

    loads: list[tuple[dt.date, float]] = Field(default_factory=list)
    """Daily load over the last four weeks, rest days included as zero -- the
    bar chart, and the reason it has gaps in it."""

    derived: HealthDerived = Field(default_factory=HealthDerived)

    plan: TrainingPlan | None = None
    """None when there is no `training.toml` at all -- no camp on, and the tab
    goes back to being the log it was before."""


class DashboardState(Base):
    """The boot payload. A response projection assembled from many tables --
    no longer the storage format, which is what let the old one be
    full-replaced by a stale tab."""

    date: dt.date
    math: MathPanel
    chess: ChessPanel
    investigate: InvestigatePanel
    patterns: PatternsPanel
    spelling: SpellingPanel
    tricks: TricksPanel
    word: WordOfDay | None = None
    games: GamesPanel
    theorems: TheoremsPanel
    papers: PapersPanel
    health: HealthPanel = Field(default_factory=HealthPanel)
    daylog: str | None = None
    #: Whether the day's scale and chord were actually played. The piano panel
    #: is otherwise the same card whether or not you sat down, and the whole
    #: point of a tick is that the border can tell.
    piano_practiced: bool = False
    completion: list[DayCompletion] = Field(default_factory=list)
    perfect_day_streak: int = 0


class ConfigResponse(Base):
    topic: str
    #: The training phase, or "" when there is no camp configured. The header
    #: prints it ahead of the topic and omits the segment entirely when empty,
    #: so a fresh clone with no training.toml is not headed by a stray colon.
    phase: str = ""
    tickers: list[str]
    #: `paused` in config.toml: the laptop draws the home tab and nothing else.
    paused: bool = False


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

    calendar: str = ""
    """Which of the account's calendars this event came from.

    Empty only for a row gcalcli returned without the column. The week grid
    colours by this, and the classifier is trained on it -- an event's calendar
    is the label, so reading it back is what makes the training set free.
    """

    end_date: dt.date | None = None
    """Set only when the event does not end on the day it starts.

    An all-day event spans `[date, end_date)` in Google's half-open convention:
    a one-day holiday comes back as 09-07 to 09-08. The grid would draw that as
    two days without somewhere to put the distinction.

    There is deliberately no `all_day` helper beside this. "Has no start time"
    is the whole test, both ends read it that way, and a bare `@property` would
    not survive `model_dump` anyway -- which is the bug `CalendarInfo.writable`
    was fixed for.
    """


class CalendarInfo(Base):
    """One row of `gcalcli list`."""

    name: str
    access: str

    @computed_field
    @property
    def writable(self) -> bool:
        """The two roles Google lets you insert events under.

        A `reader` calendar -- the US holidays feed, a shared course calendar
        -- is real, is drawn in the grid, and can never be an add target.

        `computed_field`, not a bare `@property`: the grid filters the Tab
        cycle on this, and a plain property is invisible to `model_dump`, so
        the client saw `undefined` and offered no calendars at all. The rule
        belongs on the server either way -- the browser should not be the
        second place that knows which access roles can be written to.
        """
        return self.access in ("owner", "writer")


class MarketsResponse(Base):
    """Was a bare `list[MarketRow]`. An envelope, so freshness has somewhere
    to live -- a list has no room for anything about itself."""

    rows: list[MarketRow] = Field(default_factory=list)
    feed: FeedMeta


class NewsResponse(Base):
    items: list[NewsItem] = Field(default_factory=list)
    feed: FeedMeta


class CalendarResponse(Base):
    """One week, as the grid draws it.

    Was `today` plus `week`, two flat lists built from two agenda calls -- and
    today's events appeared in both, because the week range started on today.
    The grid needs neither list: it needs a date window and every event inside
    it, and which day a given event belongs in is a property of the event.
    """

    start: dt.date | None = None
    """The Monday the window opens on. None only on an empty default."""

    days: int = 7
    events: list[CalendarEvent] = Field(default_factory=list)

    calendars: list[CalendarInfo] = Field(default_factory=list)
    """Every calendar on the account, writable or not.

    Sent with the events rather than fetched separately because the grid needs
    it to assign colours on first paint, and the add box needs the writable
    subset to offer as override targets.
    """

    feed: FeedMeta | None = None
    """Attached by the route, not by the fetcher.

    Optional because `Feeds._fetch_calendar` builds this response *inside* the
    cache and so cannot know when it will be considered fetched. The route
    wraps it once the cache has answered. Never None over HTTP.
    """


class ClassifierStatus(Base):
    """What the model is, after a retrain was asked for.

    Returned rather than a bare 204 because "it retrained" and "it retrained
    and found four more events than last time" are different answers, and the
    second is the one that tells you whether pressing the button did anything.
    """

    fitted: bool
    samples: int
    calendars: list[str] = Field(default_factory=list)
    corrections: int = 0
    unavailable: str | None = None


class CalendarGuess(Base):
    calendar: str
    score: float


class AliasHit(Base):
    """One alias that fired on a line, and what it stood for."""

    key: str
    name: str


class ResolvedEvent(Base):
    """What one line of typing was understood to mean, before anything is written.

    `POST /api/calendar/preview` returns this and touches nothing. It exists so
    the guess is visible *while you type* rather than discovered afterwards in
    Google -- the classifier is trained on noisy labels and will be wrong, and
    a wrong guess you can see is a keystroke rather than an event to go and
    delete.
    """

    title: str
    date: dt.date | None = None
    """Null only for a `todo` line, which has no day by construction."""

    start: str | None = None
    end: str | None = None

    calendar: str | None = None
    """Null for a `todo` line. Nothing is filed anywhere, and naming a
    calendar in the preview would promise a Google event that never arrives."""

    source: str = Field(
        pattern=r"^(rule|model|correction|fallback|subject|default|chosen|todo)$"
    )
    """Which stage decided. Shown in the preview, because "why did it pick
    that" is the first question a wrong guess raises.

    The last three are the todo layer's and are not interchangeable with
    `fallback`, which means "the model had nothing useful to say". `default` is
    the opposite of that -- a confident prior, not a shrug -- and labelling it
    "no guess" in the preview would misreport the one decision the box makes
    most often. `calendar_correction.source` is still the original closed set;
    the mapping between them lives in `server._CORRECTION_SOURCE`.
    """

    ranked: list[CalendarGuess] = Field(default_factory=list)
    """Every writable calendar, best first. The UI cycles this with Tab, so the
    second-best guess is one key away rather than a menu."""

    warning: str | None = None
    """Set when the text parsed but something about it is worth saying -- no
    time found, an end before its start, a date read as next week."""

    # -- the todo half ------------------------------------------------------
    #
    # The box writes two kinds of thing and the fields below are how it says
    # which. There is no mode switch and no sigil: the target calendar decides,
    # because the picker that chooses it was already on screen and already
    # says where the thing is going. See `[todo].todo_calendars`.

    raw: str = ""
    """The line as typed, before aliases were expanded. The box puts this back
    when a line fails, so a rejected paste is edited rather than retyped."""

    aliases: list[AliasHit] = Field(default_factory=list)
    """Which aliases fired, in the order they appear. Drives the indicator on
    the box -- the whole point of which is that a *forgotten* alias, or one
    misspelled, looks different from one that worked, before Enter."""

    is_todo: bool = False
    """Whether this becomes a tracked item as well as an event."""

    undated: bool = False
    """The line began with `todo`. It becomes a tracked item and *no* event —
    the one thing the box writes that Google never hears about."""

    recurrence: str | None = None
    """Human-readable, e.g. "every week × 12". Null for a one-off."""

    dates: list[dt.date] = Field(default_factory=list)
    """Every day this line writes to. One entry unless it repeats."""

    error: str | None = None
    """Why this line cannot be written at all. A `warning` still commits; this
    does not. The one that matters is a line with no day in it -- see
    `todo.resolve`, which refuses rather than defaulting to today."""


class CalendarPreview(Base):
    """Every line in the box, read. Writes nothing."""

    lines: list[ResolvedEvent] = Field(default_factory=list)
    calendars: list[str] = Field(default_factory=list)
    """Writable calendars, best first -- the picker's contents."""


class CalendarWrite(Base):
    """What one commit did.

    Split rather than aggregated, because the box does different things with
    the halves: what was written disappears, and what failed stays behind with
    its error attached. Committing the good lines and keeping the bad ones is
    the only behaviour that makes a forty-line paste with one typo in it worth
    pasting -- all-or-nothing punishes the other thirty-nine.
    """

    written: list[ResolvedEvent] = Field(default_factory=list)
    failed: list[ResolvedEvent] = Field(default_factory=list)
    events: int = 0
    """Google events created. More than `len(written)` when something repeats."""


class TodoAlias(Base):
    key: str
    name: str
    calendar: str | None = None


class TodoPanel(Base):
    items: list[TodoItem] = Field(default_factory=list)
    aliases: list[TodoAlias] = Field(default_factory=list)
    """The legend behind the `?`. Sent with the panel rather than fetched on
    demand -- it is five lines of text and one fewer round trip."""

    default_calendar: str = "Homework"
    todo_calendars: list[str] = Field(default_factory=list)

    recur_weeks: int = 12
    """How far an unbounded "every tuesday" runs. Sent so the help modal can
    print the real horizon rather than a 12 that someone has since changed in
    config.toml -- the one number in there that is configuration and not
    grammar."""


class TodoWriteRequest(Base):
    text: str = Field(min_length=1, max_length=8000)
    """The whole box, newline-separated. One line was the old contract; a
    semester's reading list is the case this exists for."""

    today: dt.date | None = None
    calendar: str | None = None
    """The picker. None means `auto` -- defer to the classifier."""


class TodoDoneRequest(Base):
    todo_id: int
    done: bool = True

    today: dt.date | None = None
    """The browser's date, for the window the panel comes back with. The clock
    belongs to the client here as it does everywhere else."""


# --- the day, for the phone ------------------------------------------------


class DayPanel(Base):
    """The three questions the phone's day screen asks, in one answer.

    They have nothing to do with each other -- what today asks for, what is on
    the list, what is on the calendar -- and they arrive together because the
    screen asking them is on a phone that may have four seconds of a laptop's
    attention and then none until tomorrow. One request that either works or
    does not is a screen with one state; three are a screen with eight.
    """

    date: dt.date

    state: DashboardState
    """The whole boot payload, and not a summary of it.

    Deliberate, and the size of it is the price of the thing it buys. What a day
    asks for is defined exactly once, in `web/app/day.js`, and both clients run
    that same function -- the desktop over its store, the phone over this. A
    projection would mean writing the projection twice, and the second copy is
    precisely what goes quietly out of step: two devices disagreeing about
    whether the day is finished is worse than either of them being wrong,
    because there is no way to tell which one to believe.

    It is ~47KB, over a tailnet, once per opening of the screen.
    """

    todo: TodoPanel

    events: list[CalendarEvent] = Field(default_factory=list)
    """Today's and tomorrow's, unsorted -- the client groups by `date`.

    Tomorrow is here for the rollover, which on this client is not a rare case:
    the laptop is asleep most of the day, so a phone opened on Tuesday morning
    may not have spoken to it since Monday evening. One day of lookahead means
    the schedule screen still has something true on it when that happens. Two
    would not: an event moved on Monday night is wrong by Wednesday, and a
    schedule that is confidently wrong is worse than one that admits it is
    empty.
    """

    calendar_reached: bool = False
    """Whether gcalcli answered at all.

    Not the same question as `events` being empty, and the difference is the
    whole reason it is here: a day with nothing on it and a day nobody could
    read are the same list and completely different facts, and the phone is
    the client least able to guess which it is looking at.
    """

    paused: bool = False
    """`paused` in config.toml. The phone draws only its todo and agenda tabs.

    Here rather than on the health panel because this is the payload the phone
    keeps across a rollover, so a phone that has not reached the laptop today
    still opens on the right bar.
    """


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
