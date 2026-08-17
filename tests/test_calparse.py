"""The quick-add grammar.

Cases are the ones actually typed into the box, plus the two that motivated
the parser existing at all -- a time range with no meridiem on either end, and
a title carrying digits that must not be read as a clock.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard.calclass import RULES, CalendarClassifier, Sample, samples_from_events
from dashboard.calparse import parse
from dashboard.models.api import CalendarEvent

MONDAY = dt.date(2026, 8, 17)


@pytest.mark.parametrize(
    ("text", "title", "date", "start", "end"),
    [
        # The two the feature was asked for.
        ("add move to ND 8am-10am wednesday", "Move to ND", "2026-08-19", "08:00", "10:00"),
        (
            "add real analysis test on wednesday from 9:25-10:30",
            "Real analysis test",
            "2026-08-19",
            "09:25",
            "10:30",
        ),
        # A bare range infers a meridiem from the opening hour: 9 is morning,
        # 2 is afternoon.
        ("office hours 2-3 tuesday", "Office hours", "2026-08-18", "14:00", "15:00"),
        # ...and an end that would land before its start crosses noon instead.
        ("study session 11-1 friday", "Study session", "2026-08-21", "11:00", "13:00"),
        ("dinner with Anna friday 6pm", "Dinner with Anna", "2026-08-21", "18:00", "19:00"),
        (
            "lunch with Nathan at 12:30 thursday",
            "Lunch with Nathan",
            "2026-08-20",
            "12:30",
            "13:30",
        ),
        # Naming today's own weekday means today, and "next" is a week past it.
        ("gym monday", "Gym", "2026-08-17", None, None),
        ("gym next monday", "Gym", "2026-08-24", None, None),
        ("flight home dec 20 all day", "Flight home", "2026-12-20", None, None),
        ("pset 4 due tomorrow", "Pset 4 due", "2026-08-18", None, None),
    ],
)
def test_lines_that_get_typed(text, title, date, start, end):
    got = parse(text, MONDAY)
    assert got.title == title
    assert got.date.isoformat() == date
    assert (got.start.strftime("%H:%M") if got.start else None) == start
    assert (got.end.strftime("%H:%M") if got.end else None) == end


def test_a_number_in_the_title_is_not_a_time():
    """`pset 4` and a course code are the reason a bare digit is never a clock.

    A lone number becomes a time only with a meridiem or an explicit "at";
    anything else stays in the title where it was typed.
    """
    assert parse("CSE 20289 exam 8/19 at 10am", MONDAY).title == "CSE 20289 exam"
    assert parse("pset 4 due tomorrow", MONDAY).all_day
    assert parse("read chapter 12 friday", MONDAY).all_day


def test_a_line_with_no_day_lands_today_and_says_so():
    got = parse("lift 6am", MONDAY)
    assert got.date == MONDAY
    assert got.warning == "no day found; used today"


def test_a_date_that_has_gone_means_next_year():
    # Written in August, "feb 3" is not five months ago.
    assert parse("conference feb 3", MONDAY).date == dt.date(2027, 2, 3)


def test_duration_and_when_are_what_gcalcli_gets():
    got = parse("seminar wednesday 9:25-10:30", MONDAY)
    assert got.when() == "2026-08-19 09:25"
    assert got.duration_minutes == 65

    allday = parse("reading day friday", MONDAY)
    assert allday.when() == "2026-08-21"
    assert allday.all_day


# --- the classifier --------------------------------------------------------


WRITABLE = ["Special Events", "Tests", "Classes", "Homework", "Piano App Deadlines"]


def _fitted() -> CalendarClassifier:
    events = [
        CalendarEvent(date=MONDAY, title=t, calendar=c)
        for t, c in [
            ("Data Structures CSE 20312 01", "Classes"),
            ("Introduction to Probability MATH 30530 01", "Classes"),
            ("ASME Kickoff Meeting!", "Classes"),
            ("Probability Midterm 1", "Tests"),
            ("Systems Final Exam", "Tests"),
            ("patty bday", "Homework"),
            ("henry f bday", "Homework"),
            ("prob hw9", "Homework"),
            ("Piano Deadline 3- Live Keyboard", "Piano App Deadlines"),
            ("Blue and Gold Game", "Special Events"),
            ("Bengal Bouts Final", "Special Events"),
        ]
    ]
    clf = CalendarClassifier("Special Events")
    clf.fit(samples_from_events(events, WRITABLE), WRITABLE)
    return clf


def test_the_rule_beats_the_history():
    """"test" reaches Tests even though the history never uses the word.

    This account writes exams down as "Exam", "Midterm" and "Final", so a model
    fitted on the history alone sends "real analysis test" to Classes -- next
    to the lectures that share its name.
    """
    clf = _fitted()
    assert clf.predict("Real analysis test").calendar == "Tests"
    assert clf.predict("Real analysis test").source == "rule"
    # ...and the word has to be the whole word.
    assert clf.predict("Testing the new build").source != "rule"


def test_the_model_learns_filing_no_keyword_would_guess():
    """Birthdays live on Homework on this account. Only the data knows that."""
    assert _fitted().predict("cassie bday").calendar == "Homework"


def test_every_writable_calendar_is_offered():
    """Tab cycles `ranked`, so a calendar missing from it is unreachable."""
    ranked = _fitted().predict("something entirely new").ranked
    assert {guess for guess, _ in ranked} == set(WRITABLE)


def test_recurring_events_are_deduplicated():
    """A class meeting 40 times is one habit, not 40 votes."""
    repeated = [
        CalendarEvent(date=MONDAY + dt.timedelta(days=i), title="Lecture", calendar="Classes")
        for i in range(40)
    ]
    assert len(samples_from_events(repeated, WRITABLE)) == 1


def test_an_unfittable_history_still_answers():
    """One class is not a classification problem -- rules and the default remain."""
    clf = CalendarClassifier("Special Events")
    clf.fit([Sample("Lecture", "Classes", 1.0)], WRITABLE)
    assert not clf.fitted
    assert clf.predict("anything").calendar == "Special Events"
    assert clf.predict("final exam").calendar == "Tests"  # the rule does not need a model


def test_rules_name_calendars_that_have_to_exist():
    """A rule pointing at a calendar this account does not have is skipped."""
    clf = CalendarClassifier("Special Events")
    clf.fit([], ["Special Events"])
    assert clf.predict("final exam").calendar == "Special Events"
    assert {calendar for _, calendar in RULES} == {"Tests", "Piano App Deadlines"}
