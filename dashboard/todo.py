"""Aliases, recurrence, and the rule that a due date has to be typed.

The calendar box already knew how to turn a line into a title and a span
(`calparse`) and how to pick a calendar for it (`calclass`). What it did not
know is that most of what gets typed into it is *homework* -- a thing with a
subject, a due date, and a state of being done or not. This module is that
layer, and it is deliberately thin: three transformations on the text before
`calparse` sees it, and one rule applied after.

--- aliases ----------------------------------------------------------------

An alias is a textual substitution and nothing more, in the sense a variable
is: wherever the whole word appears it is replaced by what it stands for.

    fpm Thornhill Chapter 3 due thursday
      -> US Foreign Policy Making Thornhill Chapter 3 due thursday

Whole *word*, which is the entire subtlety. `os` -> `Operating Systems` has to
fire in "new os homework" and must not fire in "buy banjos", so the pattern is
anchored on word boundaries rather than on being a substring, and rather than
on being the first token -- an alias that only worked at the start of a line
would be a keyword, and keywords have to be remembered in a way that
substitutions do not.

A leading backslash cancels it: `\\os` is the literal word. This is not written
anywhere in the interface on purpose. It is an escape hatch for the rare
collision, and putting it on screen would advertise a rule that costs more to
read than the collision costs to work around.

--- recurrence -------------------------------------------------------------

`gcalcli add` cannot create a recurring event -- there is no flag for it, and
the API call it makes writes a single instance. So a repeating item becomes N
separate events, one per occurrence, each with its own row here.

That sounds like a workaround and is actually the behaviour that was wanted:
deleting one week's reading has to leave the other weeks alone, which is
exactly what independent events give you for free. A real recurring event
would have made "delete just this one" the hard case instead of the only case.

The recurrence phrase is rewritten rather than deleted, which is the trick that
keeps this module from having to know anything about dates:

    every tuesday   -> tuesday      (interval 1)
    every other tue -> tue          (interval 2)

`calparse` then resolves "tuesday" into the first occurrence with the same
weekday logic it uses everywhere else, and reports it as an explicitly named
day -- so a recurring line satisfies the due-date rule without a special case.

--- the rule ---------------------------------------------------------------

A line that does not name a day is refused. `calparse` reports this rather than
enforcing it (see `Parsed.date_explicit`) because the two callers want opposite
things: an event on the wrong day is one drag in Google, but a *due date* that
nobody typed is a lie about when work is owed, and it is worse than no item.

--- todo -------------------------------------------------------------------

The rule above has one exception, and it is the first word of the line:

    todo fix the coffee table
    todo: fix the coffee table

`todo` -- in any case, with or without the colon -- says that the omission was
deliberate. The line then writes a row and *no Google event*: there is no day
to put one on, and a calendar is not a place to keep things that are not
happening at a time. So an undated item lives only here, which is the one thing
in this feature that is not mirrored anywhere.

It has to *begin* the line, which is what makes it safe to hand a word this
ordinary that much power. "add a todo item to the list thursday" is a Thursday
event about a list; only a line that opens by announcing itself is read as
undated. The word is stripped before `calparse` sees the line, like the
recurrence phrases above it, and it is `RESERVED` so no alias can shadow it.

Repeating something with no date is a contradiction rather than a shorthand,
and is refused as one -- "todo water the plants every tuesday" has no reading.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple

from . import calparse

# How far a bare "every tuesday" runs when nothing says otherwise. A semester
# is about fifteen weeks; twelve is a default that reaches the end of the
# current block of work without filling the calendar to the horizon.
DEFAULT_RECUR_WEEKS = 12

# The cap, applied after `for N weeks` and `until <date>` have had their say.
# Every occurrence is a separate write to Google, so this is also the bound on
# how long one Enter key can take.
MAX_OCCURRENCES = 40

MAX_LINES = 40

# Words the parser reads as dates. An alias key colliding with one of these
# would be invisible: `calparse` runs after the substitution and would find a
# day where the subject was, or the substitution would eat the day. Refused at
# config load rather than debugged at the box.
RESERVED = (
    set(calparse.WEEKDAYS)
    | set(calparse.MONTHS)
    | {"today", "tonight", "tomorrow", "tmrw", "overmorrow", "every", "other",
       "until", "due", "by", "weekly", "week", "day", "days", "weeks", "at",
       "from", "on", "in", "all", "todo"}
)

# `every other tuesday`, `every tuesday`, `every 2 weeks`, `weekly`.
_WEEKDAY_ALT = "|".join(sorted(calparse.WEEKDAYS, key=len, reverse=True))
EVERY = re.compile(
    rf"\bevery\s+(?:(other)\s+)?(?:(\d{{1,2}})\s+weeks?|({_WEEKDAY_ALT})|weeks?)\b",
    re.I,
)
WEEKLY = re.compile(r"\b(?:weekly|each\s+week)\b", re.I)
FOR_N_WEEKS = re.compile(r"\bfor\s+(\d{1,2})\s+weeks?\b", re.I)
UNTIL = re.compile(r"\buntil\s+(.+)$", re.I)
# Anchored: only a line that *opens* with it is undated. The colon is optional
# because both spellings get typed and neither is a different thought, and the
# word boundary is what keeps "todos for friday" an ordinary Friday event.
TODO = re.compile(r"^\s*todo\b\s*:?\s*", re.I)


class AliasHit(NamedTuple):
    key: str
    """What was typed, as it was typed."""
    name: str
    """What it stood for."""
    calendar: str | None
    """The subject's own calendar, if it names one."""


class Recurrence(NamedTuple):
    interval_weeks: int
    count: int

    def label(self) -> str:
        every = "week" if self.interval_weeks == 1 else f"{self.interval_weeks} weeks"
        return f"every {every} × {self.count}"


class Resolved(NamedTuple):
    """One line, read. `error` set means nothing about it is usable."""

    raw: str
    parsed: calparse.Parsed | None
    hits: tuple[AliasHit, ...]
    recurrence: Recurrence | None
    error: str | None = None
    undated: bool = False
    """The line began with `todo`. It has a title and no date, and it writes a row
    without an event -- see the module docstring. `parsed` is still set, because
    the title still had to be read out of the text; only its `date` is
    meaningless and nothing may use it."""

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None

    def dates(self) -> list[dt.date]:
        """Every day this line writes to, first one first.

        Empty for an undated line, and that emptiness is load-bearing: it is
        what `_write_line` loops over, so "no days" is already "no events"
        without a branch anywhere near Google.
        """
        if self.parsed is None or self.undated:
            return []
        first = self.parsed.date
        if self.recurrence is None:
            return [first]
        step = dt.timedelta(weeks=self.recurrence.interval_weeks)
        return [first + step * n for n in range(self.recurrence.count)]


def compile_aliases(subjects: dict[str, object]) -> re.Pattern[str] | None:
    """One alternation over every key, longest first.

    Longest first so `fpm` wins over a hypothetical `fp` -- Python's `|` is
    first-match, not longest-match, and the alternative is a subject whose
    abbreviation happens to start with another subject's silently never firing.

    The optional leading backslash is part of the match rather than a lookaround
    so that the replacement can see it and hand back the bare word.
    """
    if not subjects:
        return None
    keys = sorted((re.escape(k) for k in subjects), key=len, reverse=True)
    return re.compile(rf"\\?\b({'|'.join(keys)})\b", re.I)


def expand(
    text: str, subjects: dict[str, object], pattern: re.Pattern[str] | None = None
) -> tuple[str, list[AliasHit]]:
    """Substitute every whole-word alias. Returns the new text and what fired."""
    if pattern is None:
        pattern = compile_aliases(subjects)
    if pattern is None:
        return text, []

    hits: list[AliasHit] = []

    def swap(match: re.Match[str]) -> str:
        word = match.group(1)
        if match.group(0).startswith("\\"):
            # Escaped. The backslash is consumed and the word stands as typed --
            # and no hit is recorded, so the box reports this line as having
            # matched nothing, which is the truth.
            return word
        subject = subjects.get(word.lower())
        if subject is None:
            return match.group(0)
        name = getattr(subject, "name", None) or str(subject)
        hits.append(AliasHit(word, name, getattr(subject, "calendar", None)))
        return name

    return pattern.sub(swap, text), hits


def _cut(text: str, span: tuple[int, int]) -> str:
    """Remove a matched span, leaving one space where it was."""
    return f"{text[: span[0]]} {text[span[1] :]}"


def _horizon(text: str, today: dt.date, default_weeks: int) -> tuple[str, int]:
    """Pull `for N weeks` / `until <date>` off the line. Returns weeks.

    Only ever called on a line that is already known to repeat, and that
    ordering is load-bearing rather than tidy: `until` is also how `calparse`
    spells a time range, so "practice 9am until 10:30 thursday" would otherwise
    lose its end time to a horizon clause. A bound with nothing to bound is not
    a bound.
    """
    if match := FOR_N_WEEKS.search(text):
        return _cut(text, match.span()), max(1, int(match.group(1)))

    if match := UNTIL.search(text):
        # The clause is parsed as its own line. `calparse` needs no context to
        # read "december 15", and doing it this way means `until` understands
        # every date spelling the box already understood.
        clause = calparse.parse(match.group(1), today)
        if clause.date_explicit:
            weeks = max(1, ((clause.date - today).days // 7) + 1)
            return _cut(text, match.span()), weeks

    return text, default_weeks


def _todo(text: str) -> tuple[str, bool]:
    """Pull a leading `todo` off the line. Returns the rest, and whether it fired.

    Only the first word, and only ever one: "todo todo call mum" keeps the
    second one in the title, because a line that says it twice is a typo rather
    than twice as undated, and eating both would silently accept a sentence
    nobody meant to write.
    """
    match = TODO.match(text)
    if match is None:
        return text, False
    return text[match.end() :], True


def _recurrence(text: str) -> tuple[str, int | None]:
    """Rewrite the recurrence phrase away, keeping any weekday it named."""
    if match := EVERY.search(text):
        other, n_weeks, weekday = match.groups()
        interval = int(n_weeks) if n_weeks else (2 if other else 1)
        # The weekday survives; `every` and `other` do not. Everything else
        # about the date is left to `calparse`.
        return f"{text[: match.start()]} {weekday or ''} {text[match.end() :]}", interval

    if match := WEEKLY.search(text):
        return _cut(text, match.span()), 1

    return text, None


def resolve(
    text: str,
    today: dt.date,
    subjects: dict[str, object],
    *,
    pattern: re.Pattern[str] | None = None,
    default_weeks: int = DEFAULT_RECUR_WEEKS,
) -> Resolved:
    """One line -> what it means, or why it cannot be written.

    Order matters and is the whole design. Aliases first, because they are a
    substitution on the text a human typed and everything downstream should see
    the expanded form -- the preview, the title, and the classifier all agree
    that way. Then the recurrence clauses, which are removed so that
    `calparse` never sees a "week" it might read as a date. Then the parse.
    """
    raw = text.strip()
    if not raw:
        return Resolved(raw, None, (), None, "empty line")

    expanded, hits = expand(raw, subjects, pattern)
    # Before the recurrence clauses and well before `calparse`, for the reason
    # they are stripped too: a word left in the text becomes part of the title.
    body, undated = _todo(expanded)
    body, interval = _recurrence(body)
    weeks = default_weeks
    if interval is not None:
        body, weeks = _horizon(body, today, default_weeks)

    parsed = calparse.parse(body, today)

    if undated:
        if interval is not None:
            return Resolved(
                raw, None, tuple(hits), None,
                "“todo” and a repeat contradict each other — a thing with no "
                "day cannot happen every week",
            )
        if parsed.title == "(untitled)":
            return Resolved(raw, None, tuple(hits), None, "nothing left for a title")
        # `parsed` rides along for its title and nothing else. Its `date` is
        # whatever today happened to be, and `dates()` refuses to hand it out.
        return Resolved(raw, parsed, tuple(hits), None, undated=True)

    if not parsed.date_explicit:
        return Resolved(
            raw, None, tuple(hits), None,
            "needs a day — add one, e.g. “thursday”, “oct 3”, “tomorrow” — "
            "or start the line with “todo” if it has none",
        )
    if parsed.title == "(untitled)":
        return Resolved(raw, None, tuple(hits), None, "nothing left for a title")

    recurrence = None
    if interval is not None:
        count = max(1, min(MAX_OCCURRENCES, -(-weeks // interval)))
        recurrence = Recurrence(interval_weeks=interval, count=count)

    return Resolved(raw, parsed, tuple(hits), recurrence)


def resolve_all(
    text: str,
    today: dt.date,
    subjects: dict[str, object],
    *,
    default_weeks: int = DEFAULT_RECUR_WEEKS,
) -> list[Resolved]:
    """A whole box of lines. Blank lines are dropped, not reported as errors.

    The pattern is compiled once for the batch. Pasting a semester's reading
    list is the case this exists for, and rebuilding a forty-branch alternation
    per line is the kind of waste that only shows up at exactly that moment.
    """
    pattern = compile_aliases(subjects)
    lines = [line for line in text.splitlines() if line.strip()][:MAX_LINES]
    return [
        resolve(line, today, subjects, pattern=pattern, default_weeks=default_weeks)
        for line in lines
    ]
