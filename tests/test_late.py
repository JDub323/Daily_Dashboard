"""Late submission: a lapsed day you actually went back and did.

The rule in one line — being late costs what two hints cost, and the hints you
had already taken come off that rather than off the full score. The interesting
half is not the arithmetic but what happens to the zero the lapse already
charged: it is cancelled, not stacked on, because otherwise the advertised
penalty would be a lie.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import scoring
from dashboard.models.common import MathOutcome, MathStatus, Provenance, Track

from .conftest import TODAY

YESTERDAY = TODAY - dt.timedelta(days=1)
LONG_AGO = TODAY - dt.timedelta(days=scoring.LATE_WINDOW_DAYS + 1)


def lapse(repo, problem, date):
    """Draw a problem on `date` and let it lapse — what late acts on."""
    repo.open_math_attempt(date, problem.id)
    return repo.lapse_math(date)[1]


# --- the ladder ------------------------------------------------------------


@pytest.mark.parametrize(
    ("hints", "expected"),
    [(0, 0.40), (1, 0.28), (2, 0.16), (3, 0.04)],
)
def test_the_late_ladder_is_the_normal_one_scaled(hints, expected):
    assert scoring.late_math_score(MathOutcome.solved, hints) == expected


def test_being_late_costs_exactly_what_two_hints_cost():
    """The rule, stated as a test rather than as the constant 0.4."""
    assert scoring.late_math_score(MathOutcome.solved, 0) == scoring.math_score(
        MathOutcome.solved, 2
    )


def test_a_hint_costs_a_share_of_the_late_score_not_its_full_price():
    """The whole point of scaling.

    Subtracting 0.30 in full would run a three-hint late solve to -0.5, and
    `math_score` deliberately has no clamp — the score column's CHECK would
    have rejected the row.
    """
    one_hint = scoring.late_math_score(MathOutcome.solved, 1)
    assert one_hint == pytest.approx(0.40 - 0.30 * 0.40)
    assert scoring.late_math_score(MathOutcome.solved, 3) > 0


def test_a_late_failure_scores_what_the_lapse_already_charged():
    """So admitting you tried and could not do it costs nothing extra."""
    assert scoring.late_math_score(MathOutcome.failed, 0) == scoring.LAPSE_SCORE


# --- replacing the lapse ---------------------------------------------------


def test_a_late_solve_replaces_the_lapse_rather_than_stacking_on_it(repo, problem):
    before = repo.current_rating(Track.math)
    lapse(repo, problem, YESTERDAY)
    lapsed_rating = repo.current_rating(Track.math)
    assert lapsed_rating < before

    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)

    # What the day is worth now is the late score against the same anchor,
    # applied from where the rating stood before the lapse — not from the
    # depressed figure the lapse left behind.
    expected = scoring.elo_update(
        before, problem.anchor, scoring.late_math_score(MathOutcome.solved, 0), 0
    )
    assert repo.current_rating(Track.math) == expected
    assert repo.current_rating(Track.math) > lapsed_rating


def test_the_lapse_row_is_cancelled_not_deleted(repo, problem):
    """Append-only: the log records that it happened and then that it was undone."""
    lapse(repo, problem, YESTERDAY)
    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)

    kinds = [e.provenance for e in repo.rating_history(Track.math)]
    assert kinds == [Provenance.lapsed, Provenance.unlapsed, Provenance.late]


def test_the_cancellation_returns_exactly_the_lapse_delta(repo, problem):
    event = lapse(repo, problem, YESTERDAY)
    taken = event.rating_after - event.rating_before

    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    _, undo, _ = repo.rating_history(Track.math)
    assert undo.rating_after - undo.rating_before == -taken


def test_the_attempt_stops_being_a_lapse(repo, problem):
    lapse(repo, problem, YESTERDAY)
    attempt, _ = repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    assert attempt.status is MathStatus.solved
    assert attempt.score == scoring.late_math_score(MathOutcome.solved, 0)


def test_hints_taken_before_ghosting_still_count(repo, problem):
    repo.open_math_attempt(YESTERDAY, problem.id)
    repo.reveal_hint(YESTERDAY)
    repo.lapse_math(YESTERDAY)

    _, event = repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    assert event.score == scoring.late_math_score(MathOutcome.solved, 1)


# --- what a late row means to the other statistics -------------------------


def test_a_late_result_is_evidence_about_skill(repo, problem):
    """Unlike a lapse: you did the problem, so it counts toward the K-factor."""
    lapse(repo, problem, YESTERDAY)
    assert repo.event_count(Track.math) == 0  # the lapse alone is not evidence

    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    assert repo.event_count(Track.math) == 1


def test_a_late_solve_does_not_read_as_a_failure_in_the_success_rate(repo, problem):
    """It tops out at 0.40 against a threshold of 0.5, so counting it would
    make a week of catching up look like a week of getting worse."""
    lapse(repo, problem, YESTERDAY)
    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    assert repo.success_rate(Track.math) is None  # nothing comparable yet

    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.solved)
    assert repo.success_rate(Track.math) == 100


# --- the guards ------------------------------------------------------------


def test_a_day_outside_the_window_is_refused(repo, problem):
    lapse(repo, problem, LONG_AGO)
    with pytest.raises(ValueError, match="final"):
        repo.finish_math_late(TODAY, LONG_AGO, MathOutcome.solved)


def test_the_window_edge_is_still_open(repo, problem):
    edge = TODAY - dt.timedelta(days=scoring.LATE_WINDOW_DAYS)
    lapse(repo, problem, edge)
    attempt, _ = repo.finish_math_late(TODAY, edge, MathOutcome.solved)
    assert attempt.status is MathStatus.solved


def test_today_cannot_be_submitted_late(repo, problem):
    """It is not late; it is the thing the panel is for."""
    repo.open_math_attempt(TODAY, problem.id)
    with pytest.raises(ValueError, match="not a past day"):
        repo.finish_math_late(TODAY, TODAY, MathOutcome.solved)


def test_a_day_that_was_actually_marked_is_not_a_lapse(repo, problem):
    repo.open_math_attempt(YESTERDAY, problem.id)
    repo.finish_math(YESTERDAY, MathOutcome.failed)
    with pytest.raises(ValueError, match="not lapsed"):
        repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)


def test_submitting_the_same_day_twice_is_refused(repo, problem):
    """The double-submit guard: the second tab finds no lapse to replace."""
    lapse(repo, problem, YESTERDAY)
    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)
    with pytest.raises(ValueError, match="not lapsed"):
        repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)


def test_a_day_with_no_attempt_is_a_lookup_error(repo):
    with pytest.raises(LookupError):
        repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)


# --- the archive -----------------------------------------------------------


def test_the_archive_lists_past_problems_newest_first(repo, problem, client):
    for n in (1, 2, 3):
        lapse(repo, problem, TODAY - dt.timedelta(days=n))

    body = client.get("/api/math/archive", params={"date": TODAY.isoformat()}).json()
    dates = [e["date"] for e in body["entries"]]
    assert dates == sorted(dates, reverse=True)
    assert body["late_window_days"] == scoring.LATE_WINDOW_DAYS


def test_the_archive_says_which_days_can_still_be_submitted(repo, problem, client):
    lapse(repo, problem, YESTERDAY)
    lapse(repo, problem, LONG_AGO)

    entries = {
        e["date"]: e
        for e in client.get("/api/math/archive", params={"date": TODAY.isoformat()}).json()[
            "entries"
        ]
    }
    assert entries[YESTERDAY.isoformat()]["late_eligible"] is True
    assert entries[LONG_AGO.isoformat()]["late_eligible"] is False


def test_the_archive_quotes_the_price_before_you_pay_it(repo, problem, client):
    """The offer has to say what it scores, and the number comes from the
    server so the modal cannot hold a drifting copy of the ladder."""
    repo.open_math_attempt(YESTERDAY, problem.id)
    repo.reveal_hint(YESTERDAY)
    repo.lapse_math(YESTERDAY)

    (entry,) = client.get("/api/math/archive", params={"date": TODAY.isoformat()}).json()["entries"]
    assert entry["late_score_if_solved"] == scoring.late_math_score(MathOutcome.solved, 1)
    assert entry["hints_revealed"] == 1


def test_a_closed_day_quotes_no_price(repo, problem, client):
    lapse(repo, problem, LONG_AGO)
    (entry,) = client.get("/api/math/archive", params={"date": TODAY.isoformat()}).json()["entries"]
    assert entry["late_score_if_solved"] is None


def test_the_archive_reports_what_each_day_did_to_the_rating(repo, problem, client):
    """Summed across the day's rows: a lapse later replaced leaves three."""
    lapse(repo, problem, YESTERDAY)
    repo.finish_math_late(TODAY, YESTERDAY, MathOutcome.solved)

    (entry,) = client.get("/api/math/archive", params={"date": TODAY.isoformat()}).json()["entries"]
    net = repo.current_rating(Track.math) - scoring.MATH_SEED
    assert entry["delta"] == net


def test_submitting_late_over_http_returns_both_halves(repo, problem, client):
    lapse(repo, problem, YESTERDAY)
    body = client.post(
        "/api/math/late",
        json={"date": TODAY.isoformat(), "target": YESTERDAY.isoformat(), "outcome": "solved"},
    ).json()

    assert body["panel"]["summary"]["rating"] == repo.current_rating(Track.math)
    (entry,) = body["archive"]["entries"]
    assert entry["status"] == "solved"
    assert entry["late_eligible"] is False


def test_a_refused_late_submission_is_a_409(repo, problem, client):
    lapse(repo, problem, LONG_AGO)
    response = client.post(
        "/api/math/late",
        json={"date": TODAY.isoformat(), "target": LONG_AGO.isoformat(), "outcome": "solved"},
    )
    assert response.status_code == 409
    assert "final" in response.json()["error"]
