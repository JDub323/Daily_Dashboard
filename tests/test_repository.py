"""The storage layer.

The guards tested here are the point of moving to SQLite at all: the lost-elo
bug was a read-modify-write spread across a GET and a POST with the arithmetic
done in the browser, and two tabs could both read the same rating.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import scoring
from dashboard.db.schema import SCHEMA_VERSION, migrate
from dashboard.models import domain as d
from dashboard.models.common import (
    ChessOutcome,
    Game,
    HealthMetric,
    MathOutcome,
    PaperStatus,
    Provenance,
    Track,
)

from .conftest import TODAY

# --- migrations ------------------------------------------------------------


def test_migrations_are_idempotent(db):
    assert migrate(db.conn) == 0  # already applied by connect()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_tables_are_strict(db):
    """STRICT is what stops SQLite storing "banana" in an INTEGER column."""
    with pytest.raises(Exception, match="cannot store TEXT value in INTEGER column"):
        db.conn.execute(
            "INSERT INTO day_completion (date, done, total) VALUES ('2026-07-26', 'banana', 8)"
        )


def test_a_date_column_rejects_a_non_date(db):
    with pytest.raises(Exception, match="CHECK constraint failed"):
        db.conn.execute("INSERT INTO daylog (date, text) VALUES ('not-a-date', 'x')")


# --- ratings ---------------------------------------------------------------


def test_an_empty_track_reports_the_seed(repo):
    assert repo.current_rating(Track.math) == scoring.MATH_SEED
    assert repo.current_rating(Track.chess) == scoring.CHESS_SEED


def test_recording_a_result_derives_the_new_rating(repo):
    event = repo.record_result(Track.math, TODAY, "Putnam 1998 B3", 1500, 1.0)
    assert event.rating_before == 1500
    assert event.rating_after == 1548  # K=96 at n=0, even opponent
    assert event.delta == 48
    assert repo.current_rating(Track.math) == 1548


def test_the_k_actually_applied_is_stored_on_the_row(repo):
    """Every row is self-auditing: the arithmetic can be re-checked years
    later even if the config that produced it has changed."""
    first = repo.record_result(Track.math, TODAY, "a", 1500, 1.0)
    for i in range(9):
        repo.record_result(Track.math, TODAY, f"b{i}", 1500, 0.5)
    last = repo.rating_history(Track.math)[-1]
    assert first.k == 96
    assert last.k == scoring.k_factor(9)
    assert last.k < first.k


def test_ratings_chain_through_the_history(repo):
    repo.record_result(Track.math, TODAY, "a", 1500, 1.0)
    second = repo.record_result(Track.math, TODAY, "b", 1500, 0.0)
    assert second.rating_before == 1548


def test_success_rate_counts_scores_above_a_half(repo):
    for i, score in enumerate([1.0, 1.0, 0.4, 0.0]):
        repo.record_result(Track.math, TODAY, f"p{i}", 1500, score)
    assert repo.success_rate(Track.math) == 50


def test_success_rate_is_none_with_no_history(repo):
    assert repo.success_rate(Track.chess) is None


def test_a_reconstructed_row_cannot_supply_the_current_rating(repo):
    """Imported rows whose ratings were interpolated by the old backfill carry
    NULL, so they are flagged rather than trusted."""
    repo.insert_rating_event(
        d.RatingEvent(
            track=Track.math,
            date=TODAY,
            ref="old",
            opponent=1500,
            score=1.0,
            k=96,
            provenance=Provenance.reconstructed,
        )
    )
    assert repo.current_rating(Track.math) == scoring.MATH_SEED


def test_a_reconstructed_row_still_counts_toward_k(repo):
    """Provenance says the recorded numbers are untrustworthy, not that the
    attempt never happened. K measures how much evidence exists."""
    repo.insert_rating_event(
        d.RatingEvent(
            track=Track.math,
            date=TODAY,
            ref="old",
            opponent=1500,
            score=1.0,
            k=96,
            provenance=Provenance.reconstructed,
        )
    )
    assert repo.event_count(Track.math) == 1


def test_the_schema_forbids_a_reconstructed_row_with_a_rating(db):
    with pytest.raises(Exception, match="CHECK constraint failed"):
        db.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
            "VALUES ('math', '2026-07-26', 'x', 1500, 1.0, 96, 1500, 1548, 'reconstructed')"
        )


# --- math attempts ---------------------------------------------------------


def test_finishing_scores_and_rates_in_one_step(repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    attempt, event = repo.finish_math(TODAY, MathOutcome.solved)
    assert attempt.score == 1.0
    assert event.opponent == problem.anchor
    assert event.ref == problem.source
    assert repo.current_rating(Track.math) == event.rating_after


def test_hints_reduce_the_score_that_gets_rated(repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    repo.reveal_hint(TODAY)
    repo.reveal_hint(TODAY)
    attempt, event = repo.finish_math(TODAY, MathOutcome.solved)
    assert attempt.hints_revealed == 2
    assert attempt.score == pytest.approx(0.4)
    assert event.score == pytest.approx(0.4)


def test_hints_cap_at_three(repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    for _ in range(6):
        repo.reveal_hint(TODAY)
    assert repo.math_attempt(TODAY).hints_revealed == 3


def test_a_second_finish_is_rejected_rather_than_rated_twice(repo, problem):
    """The double-submit guard: a second tab must not rate the same problem
    again. This is the bug class the whole rewrite exists to remove."""
    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.solved)
    rating_after_first = repo.current_rating(Track.math)
    with pytest.raises(ValueError, match="already solved"):
        repo.finish_math(TODAY, MathOutcome.solved)
    assert repo.current_rating(Track.math) == rating_after_first
    assert repo.event_count(Track.math) == 1


def test_finishing_a_day_with_no_attempt_is_a_lookup_error(repo):
    with pytest.raises(LookupError):
        repo.finish_math(TODAY, MathOutcome.solved)


def test_a_hint_on_a_closed_attempt_is_refused(repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.failed)
    with pytest.raises(LookupError):
        repo.reveal_hint(TODAY)


def test_seen_sources_replaces_the_unbounded_seen_list(repo, problem):
    assert repo.seen_sources() == [problem.source]


def test_a_duplicate_source_is_rejected(repo, problem):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repo.add_problem(problem.model_copy(update={"id": None}))


# --- chess -----------------------------------------------------------------


def test_chess_finish_rates_against_the_puzzle_rating(repo, puzzle):
    repo.open_chess_attempt(TODAY, puzzle.id)
    attempt, event = repo.finish_chess(TODAY, ChessOutcome.messy)
    assert attempt.score == 0.4
    assert event.opponent == puzzle.rating


def test_chess_double_submit_is_refused(repo, puzzle):
    repo.open_chess_attempt(TODAY, puzzle.id)
    repo.finish_chess(TODAY, ChessOutcome.clean)
    with pytest.raises(ValueError, match="already done"):
        repo.finish_chess(TODAY, ChessOutcome.clean)
    assert repo.event_count(Track.chess) == 1


# --- games -----------------------------------------------------------------


def test_logging_the_same_day_twice_upserts(repo):
    repo.upsert_wordle(d.WordleResult(date=TODAY, puzzle_no=1, guesses=5, won=True))
    repo.upsert_wordle(d.WordleResult(date=TODAY, puzzle_no=1, guesses=3, won=True))
    assert repo.wordle(TODAY).guesses == 3
    assert repo.played_dates(Game.wordle) == [TODAY]


def test_a_loss_has_no_guess_count(repo):
    repo.upsert_wordle(d.WordleResult(date=TODAY, puzzle_no=1, guesses=None, won=False))
    assert repo.wordle(TODAY).won is False
    assert repo.wordle(TODAY).guesses is None


def test_the_schema_rejects_a_win_with_no_guess_count(db):
    with pytest.raises(Exception, match="CHECK constraint failed"):
        db.conn.execute(
            "INSERT INTO wordle_result (date, puzzle_no, guesses, won) "
            "VALUES ('2026-07-26', 1, NULL, 1)"
        )


def test_the_average_ignores_losses(repo):
    repo.upsert_wordle(d.WordleResult(date=TODAY, guesses=4, won=True))
    repo.upsert_wordle(d.WordleResult(date=TODAY - dt.timedelta(days=1), guesses=None, won=False))
    repo.upsert_wordle(d.WordleResult(date=TODAY - dt.timedelta(days=2), guesses=2, won=True))
    assert repo.wordle_average() == 3.0


# --- papers ----------------------------------------------------------------


def paper(repo, title, status=PaperStatus.queued, pos=0):
    return repo.add_paper(
        d.Paper(
            title=title,
            link=f"https://arxiv.org/abs/{title}",
            status=status,
            added=TODAY,
            queue_pos=pos if status is PaperStatus.queued else None,
        )
    )


def test_promoting_swaps_with_the_item_above(repo):
    a = paper(repo, "a", pos=0)
    b = paper(repo, "b", pos=1)
    repo.promote_in_queue(b.id)
    assert [p.id for p in repo.queue()] == [b.id, a.id]


def test_promoting_the_top_item_is_a_no_op(repo):
    a = paper(repo, "a", pos=0)
    paper(repo, "b", pos=1)
    repo.promote_in_queue(a.id)
    assert [p.title for p in repo.queue()] == ["a", "b"]


def test_promoting_renumbers_a_queue_with_duplicate_positions(repo):
    """queue_pos has no uniqueness constraint, so a pairwise swap would be a
    no-op on a queue that ever acquired duplicates."""
    a = paper(repo, "a", pos=0)
    b = paper(repo, "b", pos=0)
    repo.promote_in_queue(b.id)
    assert [p.id for p in repo.queue()] == [b.id, a.id]
    assert [p.queue_pos for p in repo.queue()] == [0, 1]


def test_promoting_something_not_queued_raises(repo):
    read = paper(repo, "done", status=PaperStatus.read)
    with pytest.raises(LookupError):
        repo.promote_in_queue(read.id)


def test_the_schema_ties_queue_pos_to_the_queued_status(db):
    with pytest.raises(Exception, match="CHECK constraint failed"):
        db.conn.execute(
            "INSERT INTO paper (title, link, status, added, queue_pos) "
            "VALUES ('x', 'https://x', 'read', '2026-07-26', 3)"
        )


# --- health / completion ---------------------------------------------------


def test_health_is_an_upsert_per_metric_and_day(repo):
    repo.log_health(HealthMetric.weight, TODAY, 172.0)
    repo.log_health(HealthMetric.weight, TODAY, 171.5)
    repo.log_health(HealthMetric.sleep, TODAY, 84.0)
    assert [e.value for e in repo.health_series(HealthMetric.weight)] == [171.5]
    assert [e.value for e in repo.health_series(HealthMetric.sleep)] == [84.0]


def test_completion_pct_is_computed_never_stored(repo):
    row = repo.set_completion(TODAY, 3, 8)
    assert row.pct == pytest.approx(0.375)
    stored = {c[1] for c in repo.conn.execute("SELECT * FROM day_completion").description}
    assert "pct" not in stored


def test_perfect_days_are_only_the_complete_ones(repo):
    repo.set_completion(TODAY, 8, 8)
    repo.set_completion(TODAY - dt.timedelta(days=1), 7, 8)
    assert repo.perfect_days() == [TODAY]
