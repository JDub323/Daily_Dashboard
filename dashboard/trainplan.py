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
from pathlib import Path

from pydantic import Field, model_validator

from .models.common import Base, SessionKind
from .models.domain import PlanGroup, PrescribedBlock

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


# --- what today asks for ---------------------------------------------------


def camp_week(program: Program, today: dt.date) -> int | None:
    """Which week of the camp `today` falls in, or None if outside it."""
    offset = (today - program.start).days
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


def week_planned(program: Program, today: dt.date) -> bool:
    """Whether any block covers today's week.

    The distinction this exists to draw: an empty Saturday in week 7 and any
    day at all in week 12 both produce no prescription, and they mean opposite
    things. One is a rest day the plan chose; the other is eleven weeks nobody
    has written yet. A panel that renders them the same way is quietly telling
    you to rest for three months.
    """
    week = camp_week(program, today)
    return week is not None and any(week in b.week_numbers() for b in program.block)


def plan_ends(program: Program) -> dt.date | None:
    """The last date any block covers.

    Surfaced because the honest thing for the panel to say once VJP runs out is
    the date it ran out on, rather than nothing -- an empty day and an
    unwritten week look identical otherwise, and only one of them is a rest day.
    """
    weeks = [week for block in program.block for week in block.week_numbers()]
    if not weeks:
        return None
    return program.start + dt.timedelta(days=max(weeks) * 7 + 6)


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
        asks=ask.asks,
        detail=ask.exercise.detail,
        slot=slot,
        optional=ask.optional or optional,
        regress_to=ask.regress.display if ask.regress else None,
        group=group,
        extra=extra,
        source=source,
    )


def prescription(program: Program, today: dt.date) -> list[PrescribedBlock]:
    """What the plan asks for on `today`, blocks and standing commitments both.

    The two layers are merged here rather than kept apart, because the panel
    wants one list of things to fill in and does not care which file section
    each came from. What it does care about is `group`, which is what turns a
    practice's several parts into one card with one button.
    """
    out: list[PrescribedBlock] = []
    week = camp_week(program, today)

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


def groups(program: Program, today: dt.date, blocks: list[PrescribedBlock]) -> list[PlanGroup]:
    """The point-scoring bundles today is offering.

    One per standing commitment that is running, holding the keys that have to
    be logged before it counts. `extra` blocks are left out of `keys` on
    purpose: sparring is a thing a practice sometimes contained, and a bundle
    that only completed on the days you sparred would be unfinishable most of
    the week.
    """
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
