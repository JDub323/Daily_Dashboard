"""Unmarked days.

A problem you drew and never marked is auto-scored as a loss once the day is
over. The trigger is deliberately narrow: *drawn* and not marked. A day you
never opened the dashboard at all has no attempt row, costs nothing, and is
how a week of finals stays free.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from dashboard import scoring
from dashboard.db.schema import MIGRATIONS, SCHEMA_VERSION, migrate
from dashboard.models.common import MathOutcome, MathStatus, Provenance, Track

from .conftest import TODAY

YESTERDAY = TODAY - dt.timedelta(days=1)


def draw(repo, problem, date):
    """Draw today's problem without marking it -- the state a lapse acts on."""
    return repo.open_math_attempt(date, problem.id)


# --- the rule --------------------------------------------------------------


def test_an_unmarked_past_day_is_scored_as_a_loss(repo, problem):
    draw(repo, problem, YESTERDAY)
    before = repo.current_rating(Track.math)

    (event,) = repo.reconcile_math_lapses(TODAY)

    assert event.date == YESTERDAY
    assert event.score == scoring.LAPSE_SCORE
    assert event.opponent == problem.anchor  # the problem actually drawn
    assert event.provenance is Provenance.lapsed
    assert event.rating_after < before
    assert repo.math_attempt(YESTERDAY).status is MathStatus.lapsed


def test_today_is_never_lapsed(repo, problem):
    """The day is not over. Docking someone at breakfast for a problem they
    mean to do after dinner is the one behaviour this must not have."""
    draw(repo, problem, TODAY)

    assert repo.reconcile_math_lapses(TODAY) == []
    assert repo.math_attempt(TODAY).status is MathStatus.open
    assert repo.current_rating(Track.math) == scoring.MATH_SEED


def test_a_day_that_was_never_opened_costs_nothing(repo):
    """No attempt row, no penalty -- the finals-week escape hatch."""
    assert repo.reconcile_math_lapses(TODAY + dt.timedelta(days=30)) == []
    assert repo.current_rating(Track.math) == scoring.MATH_SEED


def test_a_marked_day_is_left_alone(repo, problem):
    draw(repo, problem, YESTERDAY)
    repo.finish_math(YESTERDAY, MathOutcome.solved)
    rating = repo.current_rating(Track.math)

    assert repo.reconcile_math_lapses(TODAY) == []
    assert repo.current_rating(Track.math) == rating
    assert repo.math_attempt(YESTERDAY).status is MathStatus.solved


def test_reconciling_twice_changes_nothing(repo, problem):
    draw(repo, problem, YESTERDAY)
    repo.reconcile_math_lapses(TODAY)
    rating = repo.current_rating(Track.math)

    assert repo.reconcile_math_lapses(TODAY) == []
    assert repo.current_rating(Track.math) == rating
    assert len(repo.rating_history(Track.math)) == 1


def test_a_lapsed_day_cannot_then_be_marked(repo, problem):
    """The WHERE status = 'open' guard, from the other side: coming back a
    week later must not rate the same problem twice."""
    draw(repo, problem, YESTERDAY)
    repo.reconcile_math_lapses(TODAY)

    with pytest.raises(ValueError, match="already lapsed"):
        repo.finish_math(YESTERDAY, MathOutcome.solved)


def test_a_gap_costs_one_result_per_drawn_day(repo, problem):
    """Three drawn-and-ignored days are three successive losses, each chaining
    off the rating the last one produced."""
    days = [TODAY - dt.timedelta(days=n) for n in (3, 2, 1)]
    for day in days:
        draw(repo, problem, day)

    events = repo.reconcile_math_lapses(TODAY)

    assert [e.date for e in events] == days
    for earlier, later in zip(events, events[1:]):
        assert later.rating_before == earlier.rating_after


# --- what a lapse costs ----------------------------------------------------


def test_ghosting_costs_exactly_what_admitting_failure_costs(repo, problem):
    """The incentive that matters: silence must never be cheaper than an
    honest "couldn't solve", or the app teaches you to stop answering."""
    draw(repo, problem, YESTERDAY)
    n = repo.event_count(Track.math)
    before = repo.current_rating(Track.math)

    (lapse,) = repo.reconcile_math_lapses(TODAY)

    # What pressing "couldn't solve" would have produced from the same state:
    # same anchor, same K, same zero.
    marked_failure = scoring.elo_update(
        before, problem.anchor, scoring.math_score(MathOutcome.failed, 0), n
    )
    assert lapse.rating_after == marked_failure


def test_a_lapse_is_not_evidence_about_skill(repo, problem):
    """It moves the rating but must not move K: growing confidence out of an
    absence of evidence is exactly backwards."""
    k_before = scoring.k_factor(repo.event_count(Track.math))
    draw(repo, problem, YESTERDAY)
    repo.reconcile_math_lapses(TODAY)

    assert scoring.k_factor(repo.event_count(Track.math)) == k_before


def test_a_lapse_does_not_count_against_the_success_rate(repo, problem):
    """Success rate answers "how do you do on problems", and a lapse is a
    problem you did not do. The cost lives in the rating instead."""
    draw(repo, problem, TODAY - dt.timedelta(days=2))
    repo.finish_math(TODAY - dt.timedelta(days=2), MathOutcome.solved)
    assert repo.success_rate(Track.math) == 100

    draw(repo, problem, YESTERDAY)
    repo.reconcile_math_lapses(TODAY)

    assert repo.success_rate(Track.math) == 100


# --- the endpoint ----------------------------------------------------------


def test_loading_the_dashboard_closes_out_yesterday(client, repo, problem):
    """The browser supplies the date, so the reconciliation rides on the first
    dated request after midnight rather than on a server clock.

    `repo` and `client` are two connections over the one temp database, which
    is also the shape the real thing runs in.
    """
    draw(repo, problem, YESTERDAY)

    body = client.get("/api/state", params={"date": TODAY.isoformat()}).json()

    assert body["math"]["summary"]["rating"] < scoring.MATH_SEED
    assert body["math"]["summary"]["history"][-1]["provenance"] == "lapsed"
    # Today's panel is untouched: yesterday's lapse is not today's problem.
    assert body["math"]["problem"] is None


# --- the migration ---------------------------------------------------------


@pytest.fixture
def memdb():
    """A bare connection, closed on the way out so the suite stays clean of
    ResourceWarnings at interpreter shutdown."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def test_migration_two_preserves_the_existing_rating_lineage(memdb):
    """The rebuild is a CHECK-constraint change, and rating history is the one
    thing in this database that cannot be regenerated."""
    conn = memdb
    v1 = next(sql for version, sql in MIGRATIONS if version == 1)
    conn.executescript(f"BEGIN;\n{v1}\nPRAGMA user_version = 1;\nCOMMIT;")

    conn.execute(
        "INSERT INTO math_problem "
        "(id, source, tier, anchor, statement, answer, hints, model, generated_at) "
        "VALUES (1, 'USAMO 1988 Problem 5', 'olympiad-hard', 1900, 's', 'a', '[]', 'm', 'g')"
    )
    conn.execute(
        "INSERT INTO rating_event "
        "(id, track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
        "VALUES (8, 'math', '2026-07-24', 'USAMO 1988 Problem 5', 1900, 0.0, 51, "
        "1534, 1528, 'measured')"
    )
    conn.execute(
        "INSERT INTO rating_event (id, track, date, ref, opponent, score, k, provenance) "
        "VALUES (7, 'math', '2026-07-23', 'AIME 1993 Problem 9', 1500, 0.85, 55, "
        "'reconstructed')"
    )
    conn.execute(
        "INSERT INTO math_attempt (date, problem_id, hints_revealed, status) "
        "VALUES ('2026-07-26', 1, 2, 'open')"
    )

    # Every migration after 1, not a fixed count: this test is about what
    # survives the rebuild, and hardcoding the number made it fail the day a
    # third migration was appended for a reason it does not care about.
    assert migrate(conn) == SCHEMA_VERSION - 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM rating_event")}
    assert rows[8]["rating_before"] == 1534 and rows[8]["rating_after"] == 1528
    assert rows[8]["k"] == 51 and rows[8]["provenance"] == "measured"
    # The fabricated row keeps its NULL ratings and its flag.
    assert rows[7]["rating_after"] is None and rows[7]["provenance"] == "reconstructed"

    attempt = conn.execute("SELECT * FROM math_attempt").fetchone()
    assert attempt["hints_revealed"] == 2 and attempt["status"] == "open"

    # The index is dropped with the old table; it has to come back.
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'rating_event_track_id'"
    ).fetchone()


def test_migration_two_admits_the_new_states_and_still_rejects_junk(memdb):
    conn = memdb
    migrate(conn)
    conn.execute(
        "INSERT INTO rating_event "
        "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
        "VALUES ('math', '2026-07-25', 'x', 1500, 0.0, 48, 1528, 1504, 'lapsed')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
            "VALUES ('math', '2026-07-25', 'x', 1500, 0.0, 48, 1528, 1504, 'invented')"
        )
