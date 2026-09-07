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


def test_a_token_can_name_the_leaf_of_the_session_tree_it_is() -> None:
    """What lets the phone prefill the whole path. `LFC` is
    `strength.vjp.lower_french_contrast`, so answering that block asks nothing
    at all about what it was -- three rows of buttons collapse to none, because
    a question with one legal answer is not a question."""
    exercise = trainplan.Exercise(
        name="lower french contrast",
        label="strength training",
        kind="strength",
        focus="strength.vjp.lower_french_contrast",
    )
    assert exercise.focus == "strength.vjp.lower_french_contrast"


def test_a_focus_that_is_not_a_leaf_refuses_to_load() -> None:
    """Caught here rather than at the request boundary, and that is the whole
    value of checking it twice: the plan is read once at startup, so a typo
    found here is a message on a laptop, and the same typo found there is a
    block that cannot be logged, discovered in a gym."""
    with pytest.raises(ValidationError, match="not a session focus"):
        trainplan.Exercise(name="x", label="x", kind="strength", focus="strength.vjp.squats")


def test_a_focus_that_contradicts_its_kind_refuses_to_load() -> None:
    with pytest.raises(ValidationError, match="is roadwork, not strength"):
        trainplan.Exercise(name="x", label="x", kind="strength", focus="cardio.steady.run")


def test_a_choose_token_cannot_name_a_focus() -> None:
    """`library.A` is the one token whose whole point is that what you did is a
    real question. A focus on it would be the plan answering the question it
    exists to ask."""
    with pytest.raises(ValidationError, match="cannot name a focus"):
        trainplan.Exercise(name="x", label="x", choose=["roadwork"], focus="cardio.steady.run")


def test_a_token_without_a_focus_still_loads() -> None:
    """Optional, and blank for most tokens. A block with a kind and no focus
    logs exactly as it did before the field existed; the tree just opens at the
    level the kind fixes."""
    assert trainplan.Exercise(name="x", label="x", kind="strength").focus is None


def test_the_focus_reaches_the_prescribed_block() -> None:
    """It has to cross into `PrescribedBlock` for the phone to ever see it."""
    library = {**LIB["library"]}
    library["LFC"] = {**library["LFC"], "focus": "strength.vjp.lower_french_contrast"}
    plan = trainplan.Program(
        name="t",
        start=SUNDAY,
        weeks=4,
        library=library,
        block=[{"weeks": "0", "sun": "LFC"}],
    )
    blocks = trainplan.prescription(plan, SUNDAY)
    assert [b.focus for b in blocks] == ["strength.vjp.lower_french_contrast"]


def test_the_real_plan_names_a_focus_wherever_it_knows_one() -> None:
    """The plan on disk, not a fixture. Every library token that names a kind
    should also name the leaf, because the whole benefit is a prescribed block
    that asks nothing -- and a token quietly missing one is a block that starts
    asking three questions again with no error anywhere."""
    plan = trainplan.load_program(project_root() / "training.toml")
    assert plan is not None
    silent = [key for key, ex in plan.library.items() if ex.kind and not ex.focus]
    assert silent == []


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


# --- standing commitments --------------------------------------------------

PRACTICE = {
    "recurring": [
        {
            "name": "practice",
            "do": "HIIT+TECH",
            "days": ["mon"],
            "optional_days": ["sat"],
            "extras": ["SPAR", "N"],
            "points": 3,
            "starts": "2026-08-10",
        }
    ]
}
CLUB = {
    "library": {
        **LIB["library"],
        "HIIT": {"name": "conditioning", "label": "HIIT", "kind": "conditioning"},
        "TECH": {"name": "technique", "label": "technique", "kind": "boxing"},
        "SPAR": {"name": "sparring", "label": "sparring", "kind": "sparring"},
    }
}
MONDAY = dt.date(2026, 8, 10)
SATURDAY = dt.date(2026, 8, 15)


def club(**over) -> trainplan.Program:
    return trainplan.Program(name="t", start=SUNDAY, weeks=4, **CLUB, **{**PRACTICE, **over})


def test_a_standing_commitment_runs_through_a_week_with_no_block() -> None:
    """The whole reason `[[recurring]]` is a separate layer.

    Weeks 8-18 of the real camp have no block at all, and practice is the only
    training in them. Written inside a block it would vanish exactly there.
    """
    p = club()
    assert trainplan.week_planned(p, MONDAY) is False
    assert [b.token for b in trainplan.prescription(p, MONDAY)] == ["HIIT", "TECH", "SPAR", "N"]


def test_a_points_bearing_commitment_never_joins_the_required_layer() -> None:
    """`required` is still "worth no points", and practice is worth three. So it
    is offered, nudged and scored, and it cannot break a perfect day."""
    assert trainplan.required(trainplan.prescription(club(), MONDAY)) == []


def test_extras_are_offered_but_kept_out_of_the_completion_keys() -> None:
    """A practice bundle that only closed on the days you sparred would be
    unfinishable four days out of five."""
    p = club()
    blocks = trainplan.prescription(p, MONDAY)
    (group,) = trainplan.groups(p, MONDAY, blocks)

    assert [b.token for b in blocks if b.extra] == ["SPAR", "N"]
    assert group.keys == [f"{MONDAY}:HIIT", f"{MONDAY}:TECH"]
    assert group.points == 3


def test_an_extra_already_prescribed_today_is_not_offered_twice() -> None:
    """Neck at practice on a day the plan also asks for neck.

    Offering both would mean either logging it twice or leaving the prescribed
    block permanently unticked, and the first one doubles a day's load.
    """
    p = trainplan.Program(
        name="t", start=SUNDAY, weeks=4, **CLUB, **PRACTICE, block=[{"weeks": "0", "mon": "N"}]
    )
    blocks = trainplan.prescription(p, MONDAY)
    assert [b.token for b in blocks].count("N") == 1
    # And the one that survives is the prescribed one, which is owed.
    (neck,) = [b for b in blocks if b.token == "N"]
    assert neck.extra is False
    assert neck.optional is False


def test_a_provisional_day_is_flagged_rather_than_dropped() -> None:
    """The Saturday practice that has not started running yet.

    In the denominator it would score every Saturday against three points that
    cannot be earned; absent entirely, a Saturday you did go would be worth
    nothing. So it is offered and marked.
    """
    p = club()
    (weekday,) = trainplan.groups(p, MONDAY, trainplan.prescription(p, MONDAY))
    (saturday,) = trainplan.groups(p, SATURDAY, trainplan.prescription(p, SATURDAY))
    assert weekday.provisional is False
    assert saturday.provisional is True


def test_a_commitment_does_not_run_before_it_starts() -> None:
    p = club(recurring=[{**PRACTICE["recurring"][0], "starts": "2026-09-01"}])
    assert trainplan.prescription(p, MONDAY) == []
    assert trainplan.groups(p, MONDAY, []) == []


def test_a_commitment_and_a_block_cannot_prescribe_the_same_token() -> None:
    """Both land on the same day and are merged into one list of keys, so a
    token in both would produce two blocks nothing could tell apart."""
    with pytest.raises(ValidationError, match="both prescribe"):
        trainplan.Program(
            name="t",
            start=SUNDAY,
            weeks=4,
            **CLUB,
            **PRACTICE,
            block=[{"weeks": "0", "mon": "HIIT"}],
        )


def test_a_recurring_day_must_be_a_real_day() -> None:
    with pytest.raises(ValidationError, match="not one of"):
        club(recurring=[{**PRACTICE["recurring"][0], "days": ["monday"]}])


# --- logging a card --------------------------------------------------------

CARD_DATE = "2026-10-05"


def card(**over) -> dict:
    return {
        "date": CARD_DATE,
        "parts": [
            {"kind": "conditioning", "rpe": 8, "minutes": 25, "plan_key": f"{CARD_DATE}:HIIT"},
            {"kind": "boxing", "rpe": 5, "minutes": 45, "plan_key": f"{CARD_DATE}:TECH"},
        ],
        **over,
    }


def test_a_card_logs_every_part_as_its_own_session(client) -> None:
    """The split John asked for, all the way through: conditioning volume and
    technique volume stay separable because they are separate rows."""
    panel = client.post("/api/session/card", json=card()).json()
    logged = panel["sessions_today"]

    assert [s["kind"] for s in logged] == ["conditioning", "boxing"]
    assert [s["plan_key"] for s in logged] == [f"{CARD_DATE}:HIIT", f"{CARD_DATE}:TECH"]
    assert panel["derived"]["week_load"] == 8 * 25 + 5 * 45


def test_sparring_logged_from_a_card_keeps_its_own_kind(client) -> None:
    """`SessionKind.sparring` exists because it is the session that costs the
    most to recover from at equal RPE. Folded into `boxing` by the card, the
    question of how much of it a camp contained becomes unanswerable."""
    body = card()
    body["parts"].append(
        {"kind": "sparring", "rpe": 9, "minutes": 12, "plan_key": f"{CARD_DATE}:SPAR"}
    )
    panel = client.post("/api/session/card", json=body).json()
    assert [s["kind"] for s in panel["sessions_today"]][-1] == "sparring"


def test_replaying_a_card_is_a_conflict_and_does_not_double_the_day(client) -> None:
    """A double-click, or a second tab. The partial unique index is the guard;
    this is the guard being reported as a conflict rather than a 500."""
    first = client.post("/api/session/card", json=card())
    assert first.status_code == 200
    load = first.json()["derived"]["week_load"]

    again = client.post("/api/session/card", json=card())
    assert again.status_code == 409
    assert "already logged" in again.json()["error"]

    after = client.get("/api/state", params={"date": CARD_DATE}).json()["health"]
    assert len(after["sessions_today"]) == 2
    assert after["derived"]["week_load"] == load


def test_a_card_that_partly_collides_logs_nothing(client) -> None:
    """The reason a card is one request. Landing its first row and rejecting its
    second would leave a half-recorded practice that cannot be resubmitted --
    the index would reject the replay of the half that got through."""
    body = card()
    body["parts"] = [body["parts"][0]]
    assert client.post("/api/session/card", json=body).status_code == 200

    assert client.post("/api/session/card", json=card()).status_code == 409
    after = client.get("/api/state", params={"date": CARD_DATE}).json()["health"]
    assert [s["plan_key"] for s in after["sessions_today"]] == [f"{CARD_DATE}:HIIT"]


def test_a_card_cannot_log_one_block_twice_in_a_single_request(client) -> None:
    body = card()
    body["parts"][1]["plan_key"] = f"{CARD_DATE}:HIIT"
    assert client.post("/api/session/card", json=body).status_code == 422


def test_freehand_logging_still_carries_no_plan_key(client) -> None:
    """The `session` panel is untouched and stays the way in for anything the
    plan never asked for. A freehand row answers no prescribed block, so it can
    never close a card."""
    panel = client.post(
        "/api/session",
        json={"date": CARD_DATE, "kind": "roadwork", "rpe": 4, "minutes": 30},
    ).json()
    assert panel["sessions_today"][-1]["plan_key"] is None
