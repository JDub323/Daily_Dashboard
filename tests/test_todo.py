"""The three transformations a line goes through before `calparse` sees it.

Kept apart from `test_api.py` for the same reason `test_calparse.py` is: these
are pure functions over a string, and the interesting cases are the ones that
would take a running server and a stubbed gcalcli to reach through the routes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import todo
from dashboard.config import Subject

SUNDAY = dt.date(2026, 7, 26)

SUBJECTS = {
    "fpm": Subject(name="US Foreign Policy Making"),
    "os": Subject(name="Operating Systems"),
    "ra": Subject(name="Real Analysis", calendar="Tests"),
}


def read(text: str, today: dt.date = SUNDAY) -> todo.Resolved:
    return todo.resolve(text, today, SUBJECTS)


# --- aliases ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,title",
    [
        # The line the whole feature was described with.
        ("fpm Thornhill Chapter 3 due thursday", "US Foreign Policy Making Thornhill Chapter 3"),
        # Anywhere in the line, not just at the front. An alias that only
        # worked leading would be a keyword, and keywords have to be
        # remembered in a way that substitutions do not.
        ("new os homework friday", "Operating Systems homework"),
        ("finish the os project by monday", "Finish the Operating Systems project"),
        # ...but only as a whole word. This is what makes a two-letter subject
        # name safe to hand out.
        ("buy banjos saturday", "Buy banjos"),
        ("oscillation notes friday", "Oscillation notes"),
        ("ratio problems friday", "Ratio problems"),
        # Case folds on the way in and the expansion is verbatim on the way out.
        ("FPM memo tuesday", "US Foreign Policy Making memo"),
    ],
)
def test_an_alias_is_a_whole_word_substitution(text, title):
    assert read(text).parsed.title == title


def test_a_backslash_cancels_an_alias():
    """The escape hatch for a genuine collision. Undocumented in the UI on
    purpose -- it costs more to explain than the collision costs to work
    around, and it is here for the day a subject is named after a real word."""
    got = read(r"\os is a strange word tuesday")
    assert got.parsed.title == "Os is a strange word"
    # And it reports as having matched nothing, which is the truth -- so the
    # box's indicator says "no alias" rather than claiming one fired.
    assert got.hits == ()


def test_which_aliases_fired_is_reported():
    got = read("fpm and os both due friday")
    assert [(h.key, h.name) for h in got.hits] == [
        ("fpm", "US Foreign Policy Making"),
        ("os", "Operating Systems"),
    ]


def test_a_subject_can_name_its_own_calendar():
    assert read("ra pset friday").hits[0].calendar == "Tests"
    assert read("os pset friday").hits[0].calendar is None


# --- the due date rule -----------------------------------------------------


@pytest.mark.parametrize(
    "text", ["fix my printing setup", "set up old laptop as workstation", "read chapter 4"]
)
def test_a_line_with_no_day_is_refused(text):
    """`calparse` would put these on today. Here that is an error.

    The asymmetry is deliberate and is the reason `date_explicit` is reported
    rather than enforced downstream: an event on the wrong day is one drag in
    Google, a due date nobody typed is a lie about when work is owed.
    """
    got = read(text)
    assert not got.ok
    assert "needs a day" in got.error


@pytest.mark.parametrize(
    "text,date",
    [
        ("read ch4 thursday", "2026-07-30"),
        ("read ch4 due thursday", "2026-07-30"),
        ("read ch4 due by thursday", "2026-07-30"),
        ("read ch4 tomorrow", "2026-07-27"),
        ("read ch4 aug 3", "2026-08-03"),
        ("read ch4 in 2 weeks", "2026-08-09"),
    ],
)
def test_every_spelling_of_a_day_still_counts_as_one(text, date):
    got = read(text)
    assert got.ok and got.parsed.date.isoformat() == date


def test_a_time_survives_the_todo_layer():
    got = read("ra pset due friday 5pm")
    assert got.parsed.start == dt.time(17, 0)
    assert not got.parsed.all_day


# --- recurrence ------------------------------------------------------------


def test_every_weekday_supplies_the_first_occurrence():
    """The recurrence phrase is rewritten to just its weekday rather than
    deleted, so `calparse` resolves the first date with the logic it already
    has -- and a repeating line satisfies the due-date rule with no special
    case for it anywhere."""
    got = read("os reading every tuesday")
    assert got.ok
    assert got.parsed.title == "Operating Systems reading"
    assert got.dates()[0] == dt.date(2026, 7, 28)
    assert len(got.dates()) == todo.DEFAULT_RECUR_WEEKS


@pytest.mark.parametrize(
    "text,interval,count,first",
    [
        ("os reading every tuesday for 3 weeks", 1, 3, "2026-07-28"),
        ("os reading every other tuesday for 6 weeks", 2, 3, "2026-07-28"),
        ("os reading every 3 weeks for 9 weeks tuesday", 3, 3, "2026-07-28"),
        ("os quiz friday weekly for 2 weeks", 1, 2, "2026-07-31"),
    ],
)
def test_the_shape_of_a_repetition(text, interval, count, first):
    got = read(text)
    assert got.recurrence.interval_weeks == interval
    assert len(got.dates()) == count
    assert got.dates()[0].isoformat() == first
    step = (got.dates()[1] - got.dates()[0]).days if count > 1 else interval * 7
    assert step == interval * 7


def test_until_bounds_a_repetition():
    got = read("os reading every tuesday until august 25")
    assert got.dates()[-1] <= dt.date(2026, 8, 25)
    assert got.dates()[0] == dt.date(2026, 7, 28)


def test_until_is_not_read_as_a_horizon_on_a_one_off():
    """`until` is also how a time range is spelled. A bound with nothing to
    bound is not a bound, so the horizon clause is only looked for on a line
    that already repeats -- otherwise this loses its end time."""
    got = read("practice 9am until 10:30 thursday")
    assert (got.parsed.start, got.parsed.end) == (dt.time(9, 0), dt.time(10, 30))
    assert got.recurrence is None


def test_a_repetition_is_capped():
    got = read("os reading every week monday for 52 weeks")
    assert len(got.dates()) == todo.MAX_OCCURRENCES


# --- the whole box ---------------------------------------------------------


def test_blank_lines_are_dropped_not_reported():
    lines = todo.resolve_all(
        "fpm ch3 thursday\n\n   \nos pset friday", SUNDAY, SUBJECTS
    )
    assert len(lines) == 2
    assert all(line.ok for line in lines)


def test_one_bad_line_does_not_taint_the_others():
    lines = todo.resolve_all(
        "fpm ch3 thursday\nfix the printer\nos pset friday", SUNDAY, SUBJECTS
    )
    assert [line.ok for line in lines] == [True, False, True]
    # The refused line keeps what was typed, so the box can put it back.
    assert lines[1].raw == "fix the printer"


def test_a_key_that_collides_with_the_date_parser_is_refused():
    """Caught at config load, which is the only point where it is cheap to
    notice: `sat` expanding inside a line about to be read for a date fails
    silently in both directions."""
    from dashboard.config import TodoSettings

    with pytest.raises(ValueError, match="date parser"):
        TodoSettings(subjects={"sat": Subject(name="Systems Architecture Theory")})

    with pytest.raises(ValueError, match="single word"):
        TodoSettings(subjects={"real analysis": Subject(name="Real Analysis")})


# --- todo ------------------------------------------------------------------


def test_todo_writes_an_item_with_no_date():
    """The exception to the day rule, and it is a word rather than a mode."""
    line = read("todo fix the coffee table")
    assert line.ok
    assert line.undated
    assert line.parsed is not None
    # The word is stripped before `calparse` sees the line, exactly like the
    # recurrence phrases -- otherwise it ends up in the title.
    assert line.parsed.title == "Fix the coffee table"


def test_an_undated_line_writes_to_no_day_at_all():
    """`dates()` is what the write loops over, so empty here *is* no event."""
    assert read("todo fix the coffee table").dates() == []


@pytest.mark.parametrize(
    "text",
    [
        "todo fix the coffee table",
        "todo: fix the coffee table",
        "TODO fix the coffee table",
        "ToDo:fix the coffee table",
        "  todo   fix the coffee table",
    ],
)
def test_every_spelling_of_todo(text):
    line = read(text)
    assert line.undated
    assert line.parsed is not None and line.parsed.title == "Fix the coffee table"


@pytest.mark.parametrize(
    "text",
    [
        # Not the first word, so not the rule -- these are ordinary lines and
        # the day in them is a real day.
        "add a todo item to the list thursday",
        "write the todo panel thursday",
        # Not the whole word either.
        "todos for thursday",
    ],
)
def test_todo_only_counts_as_the_first_word(text):
    line = read(text)
    assert line.ok
    assert not line.undated
    assert line.parsed is not None and "todo" in line.parsed.title.lower()


def test_todo_still_expands_aliases():
    line = read("todo os reading")
    assert line.undated
    assert line.parsed is not None
    assert line.parsed.title == "Operating Systems reading"
    assert [h.key for h in line.hits] == ["os"]


def test_todo_and_a_repeat_contradict_each_other():
    """A thing with no day cannot happen every week, and there is no reading of
    the line that rescues it -- so it is refused rather than resolved to one."""
    line = read("todo water the plants every tuesday")
    assert not line.ok
    assert line.error is not None and "contradict" in line.error


def test_todo_with_nothing_left_is_still_refused():
    line = read("todo")
    assert not line.ok
    assert line.error == "nothing left for a title"


def test_a_dateless_line_is_told_about_todo():
    """The refusal has to name the way out, or the feature is undiscoverable —
    there is nothing on the box that mentions it."""
    line = read("fix the printer")
    assert not line.ok
    assert line.error is not None and "todo" in line.error
