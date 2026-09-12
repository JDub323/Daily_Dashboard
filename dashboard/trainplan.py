"""The training plan: what a given day asks of you, and how far out the fight is.

Pure over its inputs -- no database, no clock, no LLM. The plan is a file, and
every function that needs to know what day it is takes `today` as an argument,
for the same reason the rest of the server does ([Invariant 1]: the browser owns
"today").

The organising decision here is that **a prescription and a session are
different things and are never the same row**. `training_session` records what
happened; this module only ever produces what was *asked*. Collapsing the two --
a "planned" flag on the session table, say -- reads fine until the plan is
edited underneath a month of history, at which point every past row starts
lying about what it was measured against.

--- why the sheet's notation survives ---------------------------------------

The schedule is written in the shorthand from the spreadsheet's own index
(`LFC+N * A3`), rather than expanded into longhand. That is a deliberate choice
and it is about the *editing*, not the parsing: filling in a week is one line,
the file stays readable to the person who invented the notation, and the eleven
undecided weeks stay cheap to add. `calparse.py` is the precedent -- a small
hand-written parser beats a general one when the grammar is five rules and
belongs to one person.

--- the grammar -------------------------------------------------------------

    day   := slot ("*" slot)*        two workouts spaced apart
    slot  := token ("+" token)*      two workouts added together
    token := NAME [digits] ["'" [target]]

`'` at the end of a token makes it optional; `'` followed by something makes
that something a regression -- the escape hatch for a day you are cooked, and
it counts as done, because a regression you get no credit for is one you skip.

Names are matched **longest-first** against the library. That is the one piece
of the parse that is not obvious, and it is what keeps `LFC` from being read as
`LC`, and `VO2` from being read as a name `V` carrying the parameter `O2`.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field, model_validator

from .models.common import SESSION_FOCUS, Base, SessionKind
from .models.domain import CampBreak, PlanGroup, PrescribedBlock

SUNDAY = 6
"""`date.weekday()`, where Monday is 0."""

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""Indexed by `date.weekday()`. The file uses these three-letter keys because
the sheet's grid does, and a schedule you can read across in one line is worth
more than a schedule that spells out Wednesday."""


# --- the file --------------------------------------------------------------


class Exercise(Base):
    """One library entry: what a token means.

    Deliberately carries no `minutes` and no `rpe`. Load is rpe x minutes and
    both halves are measurements; a plan that pre-filled either would be
    handing `health.py` a number nobody took, and every derived figure
    downstream -- acwr, monotony, strain -- is a multiple of exactly those two.
    """

    name: str = Field(min_length=1)
    """What the session actually is. `lower french contrast`."""

    label: str = Field(min_length=1)
    """What the panel says. `strength training`.

    Plainer than `name` on purpose: the headline has to be something you can be
    nudged about at 7am, while `name` is the thing you look up. Both are shown,
    so the nudge stays readable without the plan going vague about what it
    wants -- those two goals genuinely pull against each other and showing one
    line each is the only way to serve both.
    """

    kind: SessionKind | None = None
    """The bucket a log of this lands in. `None` means you choose -- see
    `choose`, and exactly one of the two must be set."""

    choose: list[SessionKind] = Field(default_factory=list)
    """The offered kinds, when the plan genuinely does not decide.

    One token uses this: cardio. It is not indecision -- it is that shadowboxing
    and roadwork are different training and answering "how much technique did I
    do" later requires the difference to have been recorded at the time.
    """

    focus: str | None = None
    """Which leaf of the session tree a log of this is, where the plan knows.

    The reason the phone can prefill the whole path: `LFC` is
    `strength.vjp.lower_french_contrast`, so answering that block asks nothing
    about what it was -- three rows of buttons collapse to none, because a
    question with one legal answer is not a question.

    Optional and blank for most tokens. A block with a `kind` and no focus
    still logs exactly as it did; the tree simply opens at the level the kind
    fixes. Never set on a `choose` token, where the whole point is that what
    you did is unknown until you say.

    Validated against `SESSION_FOCUS` at load, so a typo here is a plan that
    refuses to compile rather than a session that posts a 422 from a gym.
    """

    param: str | None = Field(default=None, pattern=r"^(deciminutes|index)$")
    """What a numeric suffix means. `deciminutes` -- `A3` is 30 minutes -- or
    `index`, which just numbers a progression (`C2` is core 2)."""

    detail: str | None = None
    """What to actually do, for the panel to show on demand."""

    @model_validator(mode="after")
    def _one_of_kind_or_choose(self) -> Exercise:
        if bool(self.kind) == bool(self.choose):
            raise ValueError(
                f"{self.name!r}: set exactly one of `kind` or `choose`, not both and not neither"
            )
        return self

    @model_validator(mode="after")
    def _focus_is_a_leaf_of_this_kind(self) -> Exercise:
        """A focus has to exist, and has to agree with the kind beside it.

        Checked here, at load, rather than left to the request model. The plan
        is read once at startup and every session logged against it carries the
        same focus, so a typo caught here is one message on a laptop and a typo
        caught there is a block that cannot be logged, discovered in a gym.
        """
        if self.focus is None:
            return self
        implied = SESSION_FOCUS.get(self.focus)
        if implied is None:
            raise ValueError(f"{self.name!r}: {self.focus!r} is not a session focus")
        if self.kind is not None and implied is not self.kind:
            raise ValueError(
                f"{self.name!r}: {self.focus!r} is {implied.value}, not {self.kind.value}"
            )
        if self.choose:
            raise ValueError(f"{self.name!r}: a `choose` token cannot name a focus")
        return self


class Block(Base):
    """One or more weeks that share a pattern.

    Weeks rather than a single week because five of the sheet's rows are two
    microcycles: weeks 5 and 6 are identical, and 3 and 4 differ in one cell.
    Writing them out as independent weeks would be committing a copy-paste to
    the repository and would make the difference between them invisible.
    """

    weeks: str = Field(pattern=r"^\d+(-\d+)?$")
    source: str | None = None
    """Where this came from, for the panel: `VJP week 1`."""

    mon: str | None = None
    tue: str | None = None
    wed: str | None = None
    thu: str | None = None
    fri: str | None = None
    sat: str | None = None
    sun: str | None = None

    def week_numbers(self) -> list[int]:
        first, _, last = self.weeks.partition("-")
        return list(range(int(first), int(last or first) + 1))


class Recurring(Base):
    """A standing weekly commitment, outside the program's blocks.

    Club practice is the reason this exists, and it belongs in its own layer for
    a reason that only shows up later: the blocks are a five-week thing that
    gets replaced, while the club schedule persists across whatever replaces
    them. Written inside `[[block]]` it would have to be retyped into every
    future block -- and, worse, it would vanish through the eleven weeks nobody
    has written yet, which is precisely the stretch where it is the *only*
    training happening.
    """

    name: str = Field(min_length=1)
    """The group. One chip, one card, however many parts."""

    do: str = Field(min_length=1)
    """The same notation the blocks use, so a practice that is a HIIT workout
    and technique together is just `HIIT+TECH` -- `+` already means "one outing,
    two things" and there is no second concept to invent."""

    days: list[str] = Field(default_factory=list)
    optional_days: list[str] = Field(default_factory=list)
    """Days it may or may not be running yet. The parts are still offered, so a
    day you do go is loggable, but they stay out of the points denominator
    until logged -- a Saturday practice that has not started would otherwise
    make every Saturday score against three points you could not earn."""

    extras: list[str] = Field(default_factory=list)
    """Things a session sometimes contains and the plan cannot predict --
    sparring, neck. Offered as toggles on the card, never prescribed, and never
    counted against you for being absent."""

    points: int = Field(default=0, ge=0)

    starts: dt.date | None = None
    ends: dt.date | None = None

    @model_validator(mode="after")
    def _days_are_days(self) -> Recurring:
        for day in [*self.days, *self.optional_days]:
            if day not in DAY_KEYS:
                raise ValueError(f"{self.name!r}: {day!r} is not one of {list(DAY_KEYS)}")
        if overlap := set(self.days) & set(self.optional_days):
            raise ValueError(f"{self.name!r}: {sorted(overlap)} is both required and optional")
        return self

    def on(self, date: dt.date) -> str | None:
        """`"required"`, `"optional"` or None -- whether it runs on `date`."""
        if self.starts and date < self.starts:
            return None
        if self.ends and date > self.ends:
            return None
        day = DAY_KEYS[date.weekday()]
        if day in self.days:
            return "required"
        return "optional" if day in self.optional_days else None


class Program(Base):
    name: str = Field(min_length=1)
    #: What this stretch of the camp is *for*, in one word -- the training
    #: quality the blocks below are currently chasing. It rides in the header
    #: next to the maths topic because those two are the day's two headings:
    #: what the body is working on and what the mind is.
    #:
    #: Free text and deliberately not derived from the blocks. The blocks say
    #: what to do on Tuesday; this says why, and no arrangement of tokens adds
    #: up to a word like "explosiveness" on its own.
    phase: str = ""
    start: dt.date
    weeks: int = Field(gt=0, le=200)
    fight: dt.date | None = None

    library: dict[str, Exercise] = Field(default_factory=dict)
    block: list[Block] = Field(default_factory=list)
    recurring: list[Recurring] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Program:
        if self.start.weekday() != SUNDAY:
            raise ValueError(
                f"[program].start ({self.start}) is a "
                f"{self.start.strftime('%A')}; the grid runs Sunday to Saturday, "
                "so it must be a Sunday or the whole camp is shifted by a day"
            )

        # A week claimed twice is ambiguous, and the ambiguity would resolve
        # itself silently -- last block wins -- which is the kind of bug you
        # find in November.
        seen: dict[int, str] = {}
        for block in self.block:
            for week in block.week_numbers():
                if week in seen:
                    raise ValueError(
                        f"week {week} is claimed by two blocks ({seen[week]} and {block.weeks})"
                    )
                seen[week] = block.weeks
                if week >= self.weeks:
                    raise ValueError(
                        f"block {block.weeks!r} covers week {week}, "
                        f"past the end of a {self.weeks}-week program"
                    )

        # The load-bearing check. The schedule is regenerated from the
        # spreadsheet whenever it is re-translated, and a token that arrives
        # without a library entry would otherwise prescribe *nothing* -- a day
        # that silently asks for less than it should, which is the one failure
        # this feature cannot afford. Startup is the right place to notice.
        for block in self.block:
            for key in DAY_KEYS:
                text = getattr(block, key)
                if text:
                    _parse_day(text, self.library, f"week {block.weeks} {key}")

        for entry in self.recurring:
            parsed = _parse_day(entry.do, self.library, f"recurring {entry.name!r}")
            for token in entry.extras:
                _parse_token(token, self.library, f"recurring {entry.name!r} extras")

            # A recurring entry and a block can both land on the same day, and
            # the two are merged into one list of keys -- so a token in both
            # would produce two blocks nothing could tell apart once one was
            # logged. Caught here against every day either could cover, rather
            # than at 7am on the one Tuesday they happen to collide.
            mine = {ask.token for slot in parsed for ask in slot}
            for block in self.block:
                for key in DAY_KEYS:
                    text = getattr(block, key)
                    if not text or key not in (*entry.days, *entry.optional_days):
                        continue
                    theirs = {
                        ask.token for slot in _parse_day(text, self.library, "") for ask in slot
                    }
                    if clash := mine & theirs:
                        raise ValueError(
                            f"{entry.name!r} and week {block.weeks} {key} "
                            f"both prescribe {sorted(clash)}"
                        )
        return self


def load_program(path: Path) -> Program | None:
    """Read `training.toml`. `None` when there is no plan file at all.

    Absent is a supported state, not an error: the dashboard predates the plan
    and a fresh clone has no camp on. Every caller renders `None` as silence.
    """
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    # `[program]` is lifted to the top level rather than modelled as a nested
    # field. The file wants the header -- it separates the three lines about the
    # camp from the eighty about its contents -- and the code wants
    # `program.start` rather than `program.program.start`. `extra="forbid"`
    # still catches a typo inside the section, because the lifted keys are
    # validated by the same model as everything else.
    meta = data.pop("program", {})
    if overlap := meta.keys() & data.keys():
        raise ValueError(f"[program] and the top level both define {sorted(overlap)}")
    return Program(**meta, **data)


_cache: tuple[Path, float, Program | None] | None = None


def program(path: Path) -> Program | None:
    """`load_program`, cached on the file's mtime.

    Re-read rather than frozen at boot, because the edit this file exists to
    absorb -- moving the neck workout to whichever end of the day turns out to
    suit it -- should cost a page refresh and not a restart. Parsing it costs
    nothing; the cache is here so it does not happen on every keystroke's worth
    of polling, not because it is expensive.

    A malformed file **raises**, here and at boot, and is deliberately not
    swallowed into "no camp on". Silence is this feature's answer to a rest day;
    if it were also the answer to a typo, the two would be indistinguishable on
    the one morning it mattered.
    """
    global _cache
    stamp = path.stat().st_mtime if path.exists() else 0.0
    if _cache is not None and _cache[0] == path and _cache[1] == stamp:
        return _cache[2]
    loaded = load_program(path)
    _cache = (path, stamp, loaded)
    return loaded


# --- the parse -------------------------------------------------------------


class Ask(Base):
    """One parsed token: a thing the day asks for."""

    token: str
    """The text as written, parameter included -- `A3`, `C2`, `LFC`. This is
    what the plan key is built from, so it has to distinguish `C1` from `C2`."""

    exercise: Exercise
    value: int | None = None
    optional: bool = False
    regress: Ask | None = None

    @property
    def display(self) -> str:
        """`name`, with an `index` parameter folded in: `core 2`."""
        if self.exercise.param == "index" and self.value is not None:
            return f"{self.exercise.name} {self.value}"
        return self.exercise.name

    @property
    def asks(self) -> str | None:
        """The duration the plan asks for, when it asks for one.

        Printed, never pre-filled. `A3` means "go and do thirty minutes"; what
        you then log is however long you were actually out.
        """
        if self.exercise.param == "deciminutes" and self.value is not None:
            return f"{self.value * 10} min"
        return None


def _split_name(text: str, library: dict[str, Exercise]) -> tuple[str, int | None]:
    """`"A3"` -> `("A", 3)`. Longest name first -- see the module docstring."""
    for name in sorted(library, key=len, reverse=True):
        if text.startswith(name):
            rest = text[len(name) :]
            if not rest:
                return name, None
            if rest.isdigit():
                return name, int(rest)
    raise ValueError(f"no library entry matches {text!r}")


def _parse_token(text: str, library: dict[str, Exercise], where: str) -> Ask:
    head, tick, tail = text.partition("'")
    try:
        name, value = _split_name(head, library)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None

    # `X'` is optional; `X'Y` is a regression. One apostrophe, two meanings,
    # told apart by whether anything follows it -- which is exactly how the
    # sheet's own index defines them.
    regress = None
    if tick and tail:
        # A bare number regresses within the same movement: `C2'1` is core 2,
        # dropping to core 1. Anything else is a different token entirely.
        target = f"{name}{tail}" if tail.isdigit() else tail
        regress = _parse_token(target, library, where)

    return Ask(
        token=head,
        exercise=library[name],
        value=value,
        optional=bool(tick) and not tail,
        regress=regress,
    )


def _parse_day(text: str, library: dict[str, Exercise], where: str) -> list[list[Ask]]:
    """`"LFC+N * A3"` -> `[[LFC, N], [A3]]`. `*` splits outings, `+` joins."""
    slots = [
        [_parse_token(part.strip(), library, where) for part in slot.split("+") if part.strip()]
        for slot in text.split("*")
    ]
    slots = [slot for slot in slots if slot]

    # Keys are `<date>:<token>`, so a token appearing twice in one day would
    # produce two blocks that cannot be told apart once logged.
    tokens = [ask.token for slot in slots for ask in slot]
    if len(tokens) != len(set(tokens)):
        raise ValueError(f"{where}: {text!r} uses the same token twice")
    return slots


# --- breaks ----------------------------------------------------------------
#
# A camp that stops for a fortnight and picks up where it left off, which is
# what being ill for two weeks actually does to a plan.
#
# The obvious implementation is to move `[program].start` forward, and it is
# wrong in a way that only shows up later: every function below reads the week
# out of `today - start`, so moving `start` renumbers days already lived. The
# Tuesday that asked for UFC stops having asked for it, and the week of
# compliance behind it silently becomes a week of something else. A break is
# dated instead, and a day is never inside a break that began after it -- so
# history recompiles to exactly what it was, forever, which is the property the
# whole design is bent around.
#
# Everything here takes `breaks` as an argument and defaults it to empty. The
# default is load-bearing: a camp with no breaks behaves byte for byte as it
# did before this existed, so the existing tests are the regression net for
# that claim rather than a wall of churn.


def week_start(day: dt.date) -> dt.date:
    """The Sunday on or before `day` -- the start of the grid row it is in."""
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def next_sunday(day: dt.date) -> dt.date:
    """The Sunday on or after `day`. `day` itself, when it is one.

    What the resume control offers. You press it on the day you feel better and
    the camp comes back at the top of the next row of the grid, because a row
    is the unit the sheet is written in and re-entering one halfway through is
    how the week index and the weekday lookup start disagreeing.
    """
    return day + dt.timedelta(days=(6 - day.weekday()) % 7)


def paused_on(breaks: Sequence[CampBreak], today: dt.date) -> CampBreak | None:
    """The break `today` falls inside, if any.

    Half-open -- `started <= today < resumed` -- which is what makes the
    Sunday you come back on the first day that asks for something again rather
    than one more day off.
    """
    for entry in breaks:
        if entry.started <= today and (entry.resumed is None or today < entry.resumed):
            return entry
    return None


def shift(breaks: Sequence[CampBreak], today: dt.date) -> dt.timedelta:
    """How far back the grid has been pushed, as of `today`.

    Counted from the *Sunday* of the week a break started in, not from the day
    it started, so the shift is always a whole number of weeks: `resumed` is a
    Sunday by CHECK and this subtracts another one. The consequence, which is
    intended, is that the week you fell ill in runs again in full when you come
    back -- including the days of it you had already done. After a fortnight
    off that is the right training, and the plan keys are date-stamped, so
    nothing collides with what was logged the first time round.

    A break that has not reached its Sunday yet contributes nothing. That is
    the two days between pressing resume on a Friday and the camp restarting:
    still paused, not yet shifted.
    """
    return sum(
        (entry.resumed - week_start(entry.started) for entry in breaks if _effective(entry, today)),
        dt.timedelta(),
    )


def _effective(entry: CampBreak, today: dt.date) -> bool:
    return entry.resumed is not None and entry.resumed <= today


# --- what today asks for ---------------------------------------------------


def camp_week(
    program: Program, today: dt.date, breaks: Sequence[CampBreak] = ()
) -> int | None:
    """Which week of the camp `today` falls in, or None if outside it.

    **Frozen while a break is open**, at the week it stopped in. An open break
    has no shift yet -- nobody knows how long it will be -- so without the
    freeze the number would go on climbing through the illness and the panel
    would say "week 6 of 28" about a camp that stopped in week 4 and has not
    run since. It would then jump *backwards* on the Sunday it resumed. The
    week you are paused in is the only one of those three that is true.
    """
    entry = paused_on(breaks, today)
    if entry is not None:
        today = entry.started
    offset = (today - program.start - shift(breaks, today)).days
    if offset < 0:
        return None
    week = offset // 7
    return week if week < program.weeks else None


def days_out(program: Program, today: dt.date) -> int | None:
    """Days until the fight. None when no fight is set, or it has passed."""
    if program.fight is None:
        return None
    delta = (program.fight - today).days
    return delta if delta >= 0 else None


def week_planned(program: Program, today: dt.date, breaks: Sequence[CampBreak] = ()) -> bool:
    """Whether any block covers today's week.

    The distinction this exists to draw: an empty Saturday in week 7 and any
    day at all in week 12 both produce no prescription, and they mean opposite
    things. One is a rest day the plan chose; the other is eleven weeks nobody
    has written yet. A panel that renders them the same way is quietly telling
    you to rest for three months.

    A paused day is a third of those, and it stays True here on purpose: the
    week *is* planned, you are simply not in it today. The panel tells that
    apart by asking `paused_on` first, which is the only place the difference
    is worth a sentence.
    """
    week = camp_week(program, today, breaks)
    return week is not None and any(week in b.week_numbers() for b in program.block)


def plan_ends(program: Program, breaks: Sequence[CampBreak] = ()) -> dt.date | None:
    """The last date any block covers.

    Surfaced because the honest thing for the panel to say once VJP runs out is
    the date it ran out on, rather than nothing -- an empty day and an
    unwritten week look identical otherwise, and only one of them is a rest day.

    Every break that has been given a resume date moves this, including one
    whose Sunday has not arrived yet: pressing resume is the decision, and the
    date the camp now runs to is the first thing that decision changes. An open
    break moves nothing, because how long it will be is not known.
    """
    weeks = [week for block in program.block for week in block.week_numbers()]
    if not weeks:
        return None
    pushed = sum(
        (entry.resumed - week_start(entry.started) for entry in breaks if entry.resumed),
        dt.timedelta(),
    )
    return program.start + dt.timedelta(days=max(weeks) * 7 + 6) + pushed


def _block(
    ask: Ask,
    today: dt.date,
    slot: int,
    *,
    optional: bool = False,
    group: str | None = None,
    extra: bool = False,
    source: str | None = None,
) -> PrescribedBlock:
    return PrescribedBlock(
        key=f"{today.isoformat()}:{ask.token}",
        token=ask.token,
        name=ask.display,
        label=ask.exercise.label,
        kind=ask.exercise.kind,
        choose=ask.exercise.choose,
        focus=ask.exercise.focus,
        asks=ask.asks,
        detail=ask.exercise.detail,
        slot=slot,
        optional=ask.optional or optional,
        regress_to=ask.regress.display if ask.regress else None,
        group=group,
        extra=extra,
        source=source,
    )


def prescription(
    program: Program, today: dt.date, breaks: Sequence[CampBreak] = ()
) -> list[PrescribedBlock]:
    """What the plan asks for on `today`, blocks and standing commitments both.

    The two layers are merged here rather than kept apart, because the panel
    wants one list of things to fill in and does not care which file section
    each came from. What it does care about is `group`, which is what turns a
    practice's several parts into one card with one button.

    A paused day asks for nothing, and the check is here rather than in the two
    callers so that it cannot be remembered in one and forgotten in the other --
    `state.py` calls this for today and again for each of the last seven days,
    and a compliance window that counted blocks from days you were ill would be
    the one number this feature exists to stop being wrong.

    The pause takes `[[recurring]]` with it. A break is a fact about the body,
    not about which section of the file a thing was written in, and there is no
    reading of "too ill to lift" that leaves you at practice. What it does not
    touch is `starts` on a commitment: that is a date in the world -- the gym
    reopening -- and nothing about being ill moves it.
    """
    if paused_on(breaks, today):
        return []

    out: list[PrescribedBlock] = []
    week = camp_week(program, today, breaks)

    block = None
    if week is not None:
        block = next((b for b in program.block if week in b.week_numbers()), None)
    text = getattr(block, DAY_KEYS[today.weekday()]) if block else None
    if text:
        for index, slot in enumerate(_parse_day(text, program.library, f"week {week}")):
            out.extend(_block(ask, today, index, source=block.source) for ask in slot)

    # Standing commitments come after whatever the block asked for, in slots of
    # their own -- practice is a separate trip out of the house from the lift,
    # which is exactly what a slot means.
    for entry in program.recurring:
        running = entry.on(today)
        if running is None:
            continue
        base = max((b.slot for b in out), default=-1) + 1
        for index, slot in enumerate(_parse_day(entry.do, program.library, entry.name)):
            out.extend(
                # Optional in the *ring* sense: a points-bearing thing is by
                # definition on the other layer of the day, and there is no
                # second flag saying so -- the weights are the one definition.
                # Whether it also counts toward the points on offer is a
                # different question, and it belongs to the group, not here.
                _block(ask, today, base + index, optional=entry.points > 0, group=entry.name)
                for ask in slot
            )
        # Offered, never owed. An extra already prescribed today is not
        # repeated -- the card ticks that block off instead of opening a second
        # form for the same neck workout.
        prescribed = {b.token for b in out}
        for token in entry.extras:
            ask = _parse_token(token, program.library, entry.name)
            if ask.token not in prescribed:
                out.append(_block(ask, today, base, optional=True, group=entry.name, extra=True))
    return out


def groups(
    program: Program,
    today: dt.date,
    blocks: list[PrescribedBlock],
    breaks: Sequence[CampBreak] = (),
) -> list[PlanGroup]:
    """The point-scoring bundles today is offering.

    One per standing commitment that is running, holding the keys that have to
    be logged before it counts. `extra` blocks are left out of `keys` on
    purpose: sparring is a thing a practice sometimes contained, and a bundle
    that only completed on the days you sparred would be unfinishable most of
    the week.

    Nothing is offered on a paused day. `blocks` is already empty by then, so a
    bundle built anyway would carry no keys, and `every` over no keys is true --
    three points for a day spent in bed. `day.js` happens to skip a keyless
    group and would have caught it, but that is the client's guard against its
    own arithmetic, and a rule about what the camp offers should not be sitting
    on the far side of the wire from the camp.
    """
    if paused_on(breaks, today):
        return []

    out: list[PlanGroup] = []
    for entry in program.recurring:
        running = entry.on(today)
        if running is None or not entry.points:
            continue
        out.append(
            PlanGroup(
                name=entry.name,
                points=entry.points,
                # A Saturday practice that has not started yet is offered and
                # not yet owed. Counting it toward the points *available* would
                # score every Saturday against three points that cannot be
                # earned -- the moving-target problem `chips.js` already refuses
                # to have. So it joins the denominator only on a day it is
                # actually logged: never a penalty, always a reward.
                provisional=running == "optional",
                keys=[b.key for b in blocks if b.group == entry.name and not b.extra],
            )
        )
    return out


def required(blocks: list[PrescribedBlock]) -> list[PrescribedBlock]:
    """The ones a complete day is counted off -- everything not marked `'`.

    The apostrophe carries real weight now: it is the difference between a day
    that owes you a session and one that does not, which is what makes the
    session chip safe to require at all.
    """
    return [block for block in blocks if not block.optional]
