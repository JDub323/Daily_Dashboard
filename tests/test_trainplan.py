"""The plan: the grammar, the calendar arithmetic, and the guards that make
re-translating the spreadsheet safe.

The heaviest tests here are not about the parser being clever. They are about
the two ways this feature fails silently: a token that resolves to nothing (a
day that asks for less than it should), and an unwritten week that reads as a
rest day (three months of the plan telling you to put your feet up). Both are
invisible at a glance and both are caught at load.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from pydantic import ValidationError

from dashboard import trainplan
from dashboard.config import project_root
from dashboard.models.common import SessionKind

# A minimal library, so the grammar tests do not depend on the real plan's
# contents and the real plan is free to change without breaking them.
LIB = {
    "library": {
        "LFC": {"name": "lower french contrast", "label": "strength training", "kind": "strength"},
        "LC": {"name": "lower concentrics", "label": "strength training", "kind": "strength"},
        "VO2": {"name": "intervals", "label": "intervals", "kind": "conditioning"},
        "A": {
            "name": "cardio",
            "label": "cardio",
            "param": "deciminutes",
            "choose": ["roadwork", "boxing"],
        },
        "C": {"name": "core", "label": "core", "kind": "other", "param": "index"},
        "N": {"name": "neck", "label": "neck", "kind": "other"},
        "R": {"name": "1x20", "label": "1x20", "kind": "strength"},
    }
}

SUNDAY = dt.date(2026, 8, 9)


def program(**blocks: str) -> trainplan.Program:
    """A one-week program whose week 0 is `blocks`."""
    return trainplan.Program(
        name="test", start=SUNDAY, weeks=4, **LIB, block=[{"weeks": "0", **blocks}]
    )


def asks(text: str, on: dt.date = SUNDAY) -> list:
    return trainplan.prescription(program(sun=text), on)


# --- the grammar -----------------------------------------------------------


def test_plus_is_one_outing_and_star_is_two() -> None:
    """The only thing the notation says about the *shape* of a day.

    `LFC+N * A3` is a gym trip and then, later, a run. Collapsing the two into
    one list would lose that, and the shape is what the panel groups by.
    """
    blocks = asks("LFC+N * A3")
    assert [b.token for b in blocks] == ["LFC", "N", "A3"]
    assert [b.slot for b in blocks] == [0, 0, 1]


def test_a_trailing_apostrophe_is_optional() -> None:
    (block,) = asks("R'")
    assert block.optional is True
    assert block.regress_to is None
    assert trainplan.required([block]) == []


def test_an_apostrophe_with_a_target_is_a_regression_not_an_option() -> None:
    """`C2'1` is "core 2, or core 1 if you are cooked" — and it is *required*.

    The distinction is the whole reason the apostrophe is overloaded in the
    sheet, and getting it backwards would make every regression day optional.
    """
    (block,) = asks("C2'1")
    assert block.optional is False
    assert block.name == "core 2"
    assert block.regress_to == "core 1"
    assert trainplan.required([block]) == [block]


def test_a_bare_digit_regresses_within_the_same_movement() -> None:
    """`C2'1` means core 1, not a token called `1`."""
    (block,) = asks("C2'1")
    assert block.regress_to == "core 1"


def test_longest_name_wins_so_vo2_is_not_a_token_carrying_a_parameter() -> None:
    """The one non-obvious rule in the parse.

    `VO2` starts with no library name of length 1, but a shortest-first matcher
    would still have to be told that. `LFC` against `LC` is the case that
    actually bites: both are real tokens and one is not a prefix of the other,
    but a lazy matcher that scanned letters would confuse them.
    """
    (block,) = asks("VO2")
    assert block.token == "VO2"
    assert block.name == "intervals"

    (block,) = asks("LFC")
    assert block.name == "lower french contrast"


def test_a_deciminute_parameter_is_printed_and_never_becomes_minutes() -> None:
    """`A3` asks for thirty minutes and pre-fills nothing.

    Load is rpe x minutes. A plan that filled in either half would be handing
    `health.py` a number nobody measured, and acwr, monotony and strain are all
    multiples of exactly those two.
    """
    (block,) = asks("A3")
    assert block.asks == "30 min"
    assert not hasattr(block, "minutes")


def test_a_token_with_choose_offers_kinds_instead_of_naming_one() -> None:
    """Cardio is the one slot where what you did is a real question.

    Until November the shadowboxing filling these slots *is* the technique work,
    and if it all logged as `conditioning` there would be no way to ask later
    how much technique survived the gym closure.
    """
    (block,) = asks("A3")
    assert block.kind is None
    assert block.choose == [SessionKind.roadwork, SessionKind.boxing]


def test_an_unknown_token_is_a_load_error_not_an_empty_day() -> None:
    """The guard that makes re-translating the spreadsheet safe.

    The schedule is regenerated wholesale whenever the sheet is re-translated. A
    token that arrives without a library entry would otherwise prescribe
    nothing at all — a day that quietly asks for less than it should, which is
    the one failure mode this feature cannot afford.
    """
    with pytest.raises(ValidationError, match="no library entry matches 'ZZZ'"):
        program(mon="ZZZ")


def test_a_token_used_twice_in_one_day_is_rejected() -> None:
    """Keys are `<date>:<token>`, so a repeat would produce two blocks nothing
    could tell apart once one of them was logged."""
    with pytest.raises(ValidationError, match="same token twice"):
        program(mon="N * N")


# --- the file's own guards -------------------------------------------------


def test_the_start_must_be_a_sunday() -> None:
    """The sheet's grid runs Sunday to Saturday. An off-by-one here shifts the
    entire camp by a day, silently, and every prescription with it."""
    with pytest.raises(ValidationError, match="must be a Sunday"):
        trainplan.Program(name="t", start=dt.date(2026, 8, 10), weeks=4, **LIB)


def test_two_blocks_cannot_claim_the_same_week() -> None:
    with pytest.raises(ValidationError, match="claimed by two blocks"):
        trainplan.Program(
            name="t",
            start=SUNDAY,
            weeks=4,
            **LIB,
            block=[{"weeks": "1-2", "mon": "LC"}, {"weeks": "2", "mon": "LC"}],
        )


def test_an_exercise_sets_exactly_one_of_kind_or_choose() -> None:
    for entry in ({}, {"kind": "strength", "choose": ["roadwork"]}):
        with pytest.raises(ValidationError, match="exactly one of"):
            trainplan.Exercise(name="x", label="x", **entry)


# --- the calendar ----------------------------------------------------------


def test_camp_week_counts_from_the_start_and_stops_at_the_end() -> None:
    p = program(mon="LC")
    assert trainplan.camp_week(p, SUNDAY) == 0
    assert trainplan.camp_week(p, SUNDAY + dt.timedelta(days=6)) == 0
    assert trainplan.camp_week(p, SUNDAY + dt.timedelta(days=7)) == 1
    assert trainplan.camp_week(p, SUNDAY - dt.timedelta(days=1)) is None
    assert trainplan.camp_week(p, SUNDAY + dt.timedelta(weeks=4)) is None


def test_days_out_goes_quiet_once_the_fight_has_happened() -> None:
    p = trainplan.Program(name="t", start=SUNDAY, weeks=4, fight=dt.date(2026, 8, 20), **LIB)
    assert trainplan.days_out(p, dt.date(2026, 8, 10)) == 10
    assert trainplan.days_out(p, dt.date(2026, 8, 20)) == 0
    assert trainplan.days_out(p, dt.date(2026, 8, 21)) is None


def test_a_rest_day_and_an_unwritten_week_are_told_apart() -> None:
    """Both produce no prescription and they mean opposite things.

    A Saturday the plan left empty is rest. A week nobody has written is a
    decision outstanding. If the panel rendered them the same way, eleven
    unwritten weeks would read as three months of prescribed rest.
    """
    p = trainplan.Program(
        name="t", start=SUNDAY, weeks=4, **LIB, block=[{"weeks": "0", "mon": "LC"}]
    )
    saturday = SUNDAY + dt.timedelta(days=6)
    next_week = SUNDAY + dt.timedelta(days=8)

    assert trainplan.prescription(p, saturday) == []
    assert trainplan.week_planned(p, saturday) is True

    assert trainplan.prescription(p, next_week) == []
    assert trainplan.week_planned(p, next_week) is False


def test_plan_ends_on_the_saturday_of_the_last_written_week() -> None:
    p = trainplan.Program(
        name="t", start=SUNDAY, weeks=8, **LIB, block=[{"weeks": "1-2", "mon": "LC"}]
    )
    assert trainplan.plan_ends(p) == SUNDAY + dt.timedelta(days=2 * 7 + 6)


# --- the key ---------------------------------------------------------------


def test_the_key_survives_a_movement_moving_day() -> None:
    """The one edit this file exists to absorb.

    Neck moves off Monday's lift and onto Wednesday's cardio — an A/B test the
    spreadsheet poses in its own margin. Monday simply stops offering the key
    and Wednesday starts, with no ordinal or weekday baked in to dangle.
    """
    monday = SUNDAY + dt.timedelta(days=1)
    wednesday = SUNDAY + dt.timedelta(days=3)

    before = program(mon="LFC+N", wed="VO2")
    after = program(mon="LFC", wed="VO2+N")

    assert [b.key for b in trainplan.prescription(before, monday)] == [
        f"{monday}:LFC",
        f"{monday}:N",
    ]
    assert [b.key for b in trainplan.prescription(after, wednesday)] == [
        f"{wednesday}:VO2",
        f"{wednesday}:N",
    ]


# --- the real plan ---------------------------------------------------------


def real() -> trainplan.Program:
    plan = trainplan.load_program(project_root() / "training.toml")
    assert plan is not None
    return plan


def test_the_shipped_plan_loads() -> None:
    """Which also runs every guard above against it, since they are validators.

    This is the test that catches a bad re-translation: the schedule is
    regenerated from the spreadsheet wholesale, and a typo in it fails here
    rather than at 7am on a Monday.
    """
    plan = real()
    assert plan.start.weekday() == trainplan.SUNDAY
    assert plan.fight == dt.date(2027, 2, 17)


def test_the_fight_is_the_wednesday_of_the_last_week_of_the_program() -> None:
    """The sheet never wrote the date down; this is the arithmetic that
    recovered it, kept as a test so it stays checked rather than remembered."""
    plan = real()
    assert plan.fight.strftime("%A") == "Wednesday"
    assert trainplan.camp_week(plan, plan.fight) == plan.weeks - 1


def test_the_vjp_block_is_five_weeks_and_stops(tmp_path: Path) -> None:
    """Weeks 8-18 are blank on purpose: VJP is five weeks long and what comes
    after is not decided. A plan that invented something for them would be
    worse than one that says nothing."""
    plan = real()
    assert trainplan.plan_ends(plan) == dt.date(2026, 10, 3)
    assert trainplan.week_planned(plan, dt.date(2026, 10, 5)) is False


def test_the_neck_ab_test_is_preserved_across_weeks_three_and_four() -> None:
    """The sheet's own open question — "neck in morning or afternoon as cool
    down" — is one cell's difference between two otherwise identical weeks, and
    it is the sort of thing a transcription flattens by accident."""
    plan = real()
    week3 = trainplan.prescription(plan, dt.date(2026, 8, 31))
    week4 = trainplan.prescription(plan, dt.date(2026, 9, 7))

    neck3 = next(b for b in week3 if b.token == "N")
    neck4 = next(b for b in week4 if b.token == "N")
    lift3 = next(b for b in week3 if b.token == "LFC")
    lift4 = next(b for b in week4 if b.token == "LFC")

    assert neck3.slot == lift3.slot, "week 3 does the neck with the lift"
    assert neck4.slot != lift4.slot, "week 4 does it with the cardio instead"


# --- across the wire -------------------------------------------------------


def test_the_plan_reaches_the_browser_on_the_health_panel(client) -> None:
    """End to end: route -> state -> trainplan -> JSON.

    Worth a test of its own because everything above this line runs on Python
    objects, and the panel reads none of those -- it reads whatever survives
    serialisation. `kind` in particular is a StrEnum that has to arrive as a
    plain string, and `choose` as a list of them.
    """
    body = client.get("/api/state", params={"date": "2026-08-31"}).json()
    plan = body["health"]["plan"]

    assert plan["days_out"] == 170
    assert plan["week"] == 3
    assert plan["weeks"] == 28
    assert plan["week_planned"] is True
    assert plan["ends"] == "2026-10-03"

    assert [b["token"] for b in plan["today"]] == ["LFC", "N", "A3"]
    assert [b["label"] for b in plan["today"]] == ["strength training", "neck", "cardio"]
    assert [b["slot"] for b in plan["today"]] == [0, 0, 1]

    cardio = plan["today"][-1]
    assert cardio["kind"] is None
    assert cardio["choose"] == ["roadwork", "boxing", "conditioning"]
    assert cardio["asks"] == "30 min"


def test_an_unwritten_week_reaches_the_browser_as_such(client) -> None:
    """The state the dashboard is actually in today, and the one that would do
    damage if it arrived looking like a rest day."""
    plan = client.get("/api/state", params={"date": "2026-08-26"}).json()["health"]["plan"]
    assert plan["today"] == []
    assert plan["week_planned"] is False
    assert plan["days_out"] == 175
