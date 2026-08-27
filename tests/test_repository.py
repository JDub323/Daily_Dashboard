"""The storage layer.

The guards tested here are the point of moving to SQLite at all: the lost-elo
bug was a read-modify-write spread across a GET and a POST with the arithmetic
done in the browser, and two tabs could both read the same rating.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from dashboard import scoring
from dashboard.db import Database, Repository
from dashboard.db.schema import SCHEMA_VERSION, migrate
from dashboard.models import domain as d
from dashboard.models.common import (
    ChessOutcome,
    EntrySource,
    Game,
    HealthMetric,
    MathOutcome,
    PaperStatus,
    Provenance,
    SessionKind,
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


# --- per-area ratings ------------------------------------------------------


def _area_problem(repo, source: str, area: str | None, anchor: int = 1500) -> d.MathProblem:
    return repo.add_problem(
        d.MathProblem(
            source=source,
            tier="aime-late",
            anchor=anchor,
            statement="s",
            answer="a",
            hints=["1", "2", "3"],
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 9, 0),
            topic="t" if area else None,
            area=area,
        )
    )


def test_an_area_with_no_history_reports_the_pooled_rating(repo, problem):
    """Not the 1500 seed: a new bucket means no evidence, and the best guess
    about someone with no evidence is what they score everywhere else."""
    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.solved)
    pooled = repo.current_rating(Track.math)
    assert pooled != scoring.MATH_SEED
    assert repo.current_rating(Track.math, "geometry") == pooled
    assert repo.event_count(Track.math, "geometry") == 0


def test_one_result_writes_a_pooled_row_and_an_area_row(repo):
    p = _area_problem(repo, "AIME 1999 P1", "algebra")
    repo.open_math_attempt(TODAY, p.id)
    repo.finish_math(TODAY, MathOutcome.solved)

    assert repo.event_count(Track.math) == 1
    assert repo.event_count(Track.math, "algebra") == 1
    rows = repo.conn.execute("SELECT area FROM rating_event WHERE track='math'").fetchall()
    assert sorted(r[0] or "" for r in rows) == ["", "algebra"]


def test_an_area_result_does_not_disturb_a_different_area(repo):
    p = _area_problem(repo, "AIME 1999 P2", "algebra")
    repo.open_math_attempt(TODAY, p.id)
    repo.finish_math(TODAY, MathOutcome.solved)
    pooled = repo.current_rating(Track.math)

    assert repo.current_rating(Track.math, "algebra") > scoring.MATH_SEED
    # geometry has no rows of its own, so it tracks the pooled rating -- which
    # did move, because the pooled rating rates every result.
    assert repo.current_rating(Track.math, "geometry") == pooled
    assert repo.event_count(Track.math, "geometry") == 0


def test_the_pooled_rating_is_unchanged_by_the_area_row(repo):
    """The guarantee the whole design rests on: `area IS NULL` means the
    pooled lineage evolves exactly as it did before areas existed."""
    with_area = _area_problem(repo, "AIME 1999 P3", "algebra")
    repo.open_math_attempt(TODAY, with_area.id)
    _, event = repo.finish_math(TODAY, MathOutcome.solved)

    assert event.area is None
    assert event.rating_before == scoring.MATH_SEED
    assert event.rating_after == scoring.elo_update(scoring.MATH_SEED, 1500, 1.0, 0)


def test_a_problem_with_no_area_rates_pooled_only(repo, problem):
    """Every problem generated before the column. It must still rate."""
    assert problem.area is None
    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.solved)
    assert repo.event_count(Track.math) == 1
    rows = repo.conn.execute("SELECT COUNT(*) FROM rating_event WHERE area IS NOT NULL").fetchone()
    assert rows[0] == 0


def test_area_k_falls_independently_of_the_pooled_k(repo):
    """An area's K reflects evidence about that area, so it stays high while
    the pooled K comes down -- which is the honest reading, not a bug."""
    for i in range(4):
        p = _area_problem(repo, f"AIME 2001 P{i}", "algebra" if i == 0 else "geometry")
        day = TODAY + dt.timedelta(days=i)
        repo.open_math_attempt(day, p.id)
        repo.finish_math(day, MathOutcome.solved)

    assert repo.event_count(Track.math) == 4
    assert repo.event_count(Track.math, "algebra") == 1
    assert scoring.k_factor(repo.event_count(Track.math, "algebra")) > scoring.k_factor(
        repo.event_count(Track.math)
    )


def test_a_lapse_rates_the_area_too(repo):
    """A ghosted day costs the area as well as the pool; otherwise skipping a
    week of your weakest area would protect that area's rating."""
    p = _area_problem(repo, "AIME 2002 P1", "geometry")
    repo.open_math_attempt(TODAY, p.id)
    repo.lapse_math(TODAY)
    assert repo.current_rating(Track.math, "geometry") < scoring.MATH_SEED


def test_the_chart_and_success_rate_read_the_pooled_series_only(repo):
    p = _area_problem(repo, "AIME 2003 P1", "algebra")
    repo.open_math_attempt(TODAY, p.id)
    repo.finish_math(TODAY, MathOutcome.solved)

    assert [e.area for e in repo.rating_history(Track.math)] == [None]
    assert repo.success_rate(Track.math) == 100


def test_area_ratings_reports_every_bucket_asked_for(repo):
    p = _area_problem(repo, "AIME 2004 P1", "algebra")
    repo.open_math_attempt(TODAY, p.id)
    repo.finish_math(TODAY, MathOutcome.solved)

    rows = dict((a, (r, n)) for a, r, n in repo.area_ratings(["algebra", "geometry"]))
    assert rows["algebra"][1] == 1
    assert rows["geometry"][1] == 0
    assert rows["geometry"][0] == repo.current_rating(Track.math)


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
    repo.log_health(HealthMetric.sleep_score, TODAY, 84.0)
    assert [e.value for e in repo.health_series(HealthMetric.weight)] == [171.5]
    assert [e.value for e in repo.health_series(HealthMetric.sleep_score)] == [84.0]


def test_a_sync_never_overwrites_a_hand_entry(repo):
    """The rule that keeps a correction from having to be made twice."""
    repo.log_health(HealthMetric.sleep_hours, TODAY, 7.5, EntrySource.manual)
    kept = repo.log_health(HealthMetric.sleep_hours, TODAY, 5.0, EntrySource.garmin)
    assert kept.value == 7.5
    assert kept.source is EntrySource.manual


def test_a_sync_may_replace_its_own_earlier_reading(repo):
    repo.log_health(HealthMetric.sleep_hours, TODAY, 5.0, EntrySource.garmin)
    updated = repo.log_health(HealthMetric.sleep_hours, TODAY, 6.25, EntrySource.garmin)
    assert updated.value == 6.25
    assert updated.source is EntrySource.garmin


def test_a_hand_entry_always_wins_over_what_is_there(repo):
    repo.log_health(HealthMetric.sleep_hours, TODAY, 5.0, EntrySource.garmin)
    fixed = repo.log_health(HealthMetric.sleep_hours, TODAY, 7.0, EntrySource.manual)
    assert fixed.value == 7.0
    assert fixed.source is EntrySource.manual


# --- readiness / sessions --------------------------------------------------


def test_readiness_is_one_row_a_day_and_scores_out_of_25(repo):
    repo.log_readiness(TODAY, 3, 3, 3, 3, 3)
    row = repo.log_readiness(TODAY, 5, 4, 4, 3, 5)
    assert row.score == 21
    assert repo.readiness(TODAY) == row


def test_a_second_session_today_is_a_second_row_not_a_correction(repo):
    """Two-a-days are ordinary in camp. An upsert here would silently eat the
    morning run when the evening's sparring was logged."""
    repo.add_session(TODAY, SessionKind.roadwork, 4, 30)
    repo.add_session(TODAY, SessionKind.sparring, 9, 45)
    sessions = repo.sessions_on(TODAY)
    assert [s.load for s in sessions] == [120, 405]


def test_deleting_a_session_reports_whether_it_existed(repo):
    session = repo.add_session(TODAY, SessionKind.boxing, 6, 60)
    assert repo.delete_session(session.id) is True
    assert repo.delete_session(session.id) is False
    assert repo.sessions_on(TODAY) == []


def test_completion_pct_is_computed_never_stored(repo):
    row = repo.set_completion(TODAY, 3, 8)
    assert row.pct == pytest.approx(0.375)
    stored = {c[1] for c in repo.conn.execute("SELECT * FROM day_completion").description}
    assert "pct" not in stored


def test_perfect_days_are_only_the_complete_ones(repo):
    repo.set_completion(TODAY, 8, 8)
    repo.set_completion(TODAY - dt.timedelta(days=1), 7, 8)
    assert repo.perfect_days() == [TODAY]


def test_points_from_the_optional_half_round_trip(repo):
    row = repo.set_completion(TODAY, 4, 4, 6)
    assert row.bonus == 6
    assert repo.completions()[0].bonus == 6


def test_points_default_to_zero_for_callers_that_have_none(repo):
    """The importer reads state.json, which predates the split entirely."""
    assert repo.set_completion(TODAY, 4, 4).bonus == 0


def test_points_are_no_part_of_a_perfect_day(repo):
    """The whole point of the split: the streak is counted off the required
    four, and a pile of optional work neither earns a perfect day nor spoils
    one."""
    repo.set_completion(TODAY, 4, 4, 0)
    repo.set_completion(TODAY - dt.timedelta(days=1), 3, 4, 11)
    assert repo.perfect_days() == [TODAY]


# --- the speed bonus, end to end -------------------------------------------

PARS = {"putnam-early": 40}


def test_the_minutes_are_stored_on_the_attempt(repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    attempt, _ = repo.finish_math(TODAY, MathOutcome.solved, 20, PARS)
    assert attempt.minutes == 20
    assert repo.math_attempt(TODAY).minutes == 20


def test_a_failure_does_not_keep_a_time(repo, problem):
    """Time is only read on a solve, so storing it on a failure would leave a
    number on the row that nothing consults and that reads as though it once
    mattered."""
    repo.open_math_attempt(TODAY, problem.id)
    attempt, event = repo.finish_math(TODAY, MathOutcome.failed, 5, PARS)
    assert attempt.minutes is None
    assert event.k == scoring.k_factor(0)


def test_a_quick_solve_beats_a_slow_one_on_the_same_problem(repo, problem, settings):
    """Two independent databases so the two solves start from the same rating.
    Same problem, same hints, same anchor -- only the clock differs."""
    repo.open_math_attempt(TODAY, problem.id)
    _, quick = repo.finish_math(TODAY, MathOutcome.solved, 20, PARS)

    slow_db = Database(settings.path("db").parent / "slow.db")
    slow_repo = Repository(slow_db.conn)
    slow_problem = slow_repo.add_problem(problem.model_copy(update={"id": None}))
    slow_repo.open_math_attempt(TODAY, slow_problem.id)
    _, slow = slow_repo.finish_math(TODAY, MathOutcome.solved, 180, PARS)
    slow_db.close()

    assert quick.rating_before == slow.rating_before
    assert quick.rating_after > slow.rating_after
    assert quick.k > slow.k


def test_the_pooled_and_area_rows_take_the_same_multiplier(repo, settings):
    """Two readings of one event. A bonus that applied to one but not the
    other would leave them disagreeing about what happened."""
    problem = repo.add_problem(
        d.MathProblem(
            source="Putnam 2001 A1",
            tier="putnam-early",
            anchor=1750,
            statement="s",
            answer="a",
            hints=["1", "2", "3"],
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 9, 0),
            topic="generating functions",
            area="combinatorics",
        )
    )
    repo.open_math_attempt(TODAY, problem.id)
    repo.finish_math(TODAY, MathOutcome.solved, 20, PARS)

    rows = repo.conn.execute(
        "SELECT area, k FROM rating_event WHERE track = 'math' ORDER BY id"
    ).fetchall()
    assert [r["area"] for r in rows] == [None, "combinatorics"]
    assert rows[0]["k"] == rows[1]["k"] > scoring.k_factor(0)


def test_an_untimed_solve_scores_exactly_what_it_always_did(repo, problem):
    """The compatibility guarantee: this feature must be invisible until you
    use it."""
    repo.open_math_attempt(TODAY, problem.id)
    _, event = repo.finish_math(TODAY, MathOutcome.solved, None, PARS)
    assert event.k == scoring.k_factor(0)
    assert event.rating_after == scoring.elo_update(1500, 1750, 1.0, 0)


# --- word pronunciation ----------------------------------------------------


def test_a_word_round_trips_its_pronunciation(repo):
    stored = repo.upsert_word(
        d.WordOfDay(
            date=TODAY,
            word="susurrus",
            pos="noun",
            definition="a whispering sound",
            pronunciation="/səˈsʌrəs/",
            respelling="suh-SUR-us",
        )
    )
    assert stored.pronunciation == "/səˈsʌrəs/"
    assert repo.word(TODAY).respelling == "suh-SUR-us"


def test_a_word_without_a_pronunciation_is_still_a_word(repo):
    """Every row written before the columns existed is this case, and the
    modal renders it with the line simply absent."""
    repo.upsert_word(d.WordOfDay(date=TODAY, word="x", pos="n", definition="d"))
    stored = repo.word(TODAY)
    assert stored.pronunciation is None and stored.respelling is None


def test_regrading_a_word_does_not_drop_its_pronunciation(repo):
    """`upsert_word` is an UPSERT over every column, so a second write that
    forgot these two would blank them."""
    repo.upsert_word(
        d.WordOfDay(
            date=TODAY,
            word="w",
            pos="n",
            definition="d",
            pronunciation="/w/",
            respelling="dubya",
        )
    )
    repo.set_sentences(TODAY, ["a", "b"])
    repo.set_grade(
        TODAY,
        d.WordGrade(
            distinct=True,
            verdict="ok",
            per_sentence=[
                d.SentenceGrade(ok=True, note="fine"),
                d.SentenceGrade(ok=True, note="fine"),
            ],
        ),
    )
    after = repo.word(TODAY)
    assert (after.pronunciation, after.respelling) == ("/w/", "dubya")
    assert after.grade is not None


# --- the do-not-repeat window ----------------------------------------------


def _word(repo, days_ago: int, word: str) -> None:
    repo.upsert_word(
        d.WordOfDay(date=TODAY - dt.timedelta(days=days_ago), word=word, pos="n", definition="d")
    )


def test_recent_words_are_oldest_first_and_stop_at_the_window(repo):
    _word(repo, 400, "ancient")
    _word(repo, 365, "the-edge")  # 365 days back is inside a 365-day window
    _word(repo, 366, "just-outside")
    _word(repo, 2, "recent")
    _word(repo, 0, "today")

    assert repo.recent_words(TODAY, 365) == ["the-edge", "recent"]


def test_recent_words_ignores_the_future(repo):
    """A day generated ahead of time — the batch runs at 3am and the clock can
    be anywhere — must not exclude itself from its own draw."""
    _word(repo, -1, "tomorrow")
    _word(repo, 1, "yesterday")
    assert repo.recent_words(TODAY, 365) == ["yesterday"]


# --- the two quick logs ----------------------------------------------------
#
# Append-only, many a day, and each kind carrying exactly its own field. That
# last one is a table CHECK rather than a validator on the model, and these
# tests go through `sqlite3` rather than through the repository for the two
# that prove it: the guarantee being claimed is about the *file*, not about
# callers who came through the front door.


def test_a_visit_is_appended_never_upserted(repo):
    """Four in an hour is a Tuesday. A PRIMARY KEY on (date, hour) would have
    the afternoon quietly replace the morning."""
    first = repo.add_bathroom(TODAY, 8, "urine", "medium", color="light")
    second = repo.add_bathroom(TODAY, 8, "urine", "small", color="yellow")
    assert first.id != second.id
    assert len(repo.bathroom_on(TODAY)) == 2


def test_a_visit_carries_only_its_own_kinds_field(repo):
    stool = repo.add_bathroom(TODAY, 9, "stool", "large", solidity="soft")
    assert stool.solidity == "soft"
    assert stool.color is None

    urine = repo.add_bathroom(TODAY, 10, "urine", "small", color="darker")
    assert urine.color == "darker"
    assert urine.solidity is None


def test_the_file_itself_refuses_a_stool_with_a_colour(db):
    """The invariant is on the table, so it holds for anything that opens this
    database -- including a hand at a `sqlite3` prompt six months from now."""
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO bathroom_log (date, hour, kind, solidity, color, volume) "
            "VALUES ('2026-07-26', 9, 'stool', 'soft', 'darker', 'medium')"
        )


def test_the_file_itself_refuses_a_kind_with_neither_field(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO bathroom_log (date, hour, kind, volume) "
            "VALUES ('2026-07-26', 9, 'urine', 'medium')"
        )


def test_an_hour_outside_the_day_is_refused(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO bathroom_log (date, hour, kind, color, volume) "
            "VALUES ('2026-07-26', 24, 'urine', 'light', 'medium')"
        )


def test_visits_come_back_in_time_order(repo):
    repo.add_bathroom(TODAY, 19, "urine", "medium", color="yellow")
    repo.add_bathroom(TODAY, 7, "urine", "large", color="darker")
    repo.add_bathroom(TODAY, 13, "stool", "medium", solidity="solid")
    assert [e.hour for e in repo.bathroom_on(TODAY)] == [7, 13, 19]


def test_a_visit_prints_its_hour_in_24h(repo):
    entry = repo.add_bathroom(TODAY, 7, "urine", "small", color="clear")
    assert entry.at == "07:00"


def test_the_bathroom_window_is_clipped_to_the_browsers_today(repo):
    """Windowed where `sessions` is not, and the window ends at the browser's
    date rather than at whatever is newest in the table -- a row written past
    midnight by a tab that had not rolled over must not stretch it."""
    for offset in (0, 3, 40):
        repo.add_bathroom(TODAY - dt.timedelta(days=offset), 8, "urine", "small", color="light")
    got = repo.bathroom_history(28, before=TODAY)
    assert [e.date for e in got] == [TODAY - dt.timedelta(days=3), TODAY]


def test_a_meal_is_appended_and_deleted_by_id(repo):
    lunch = repo.add_meal(TODAY, 12, "large", 4)
    repo.add_meal(TODAY, 19, "medium", 2, note="takeaway")
    assert len(repo.meals_on(TODAY)) == 2

    assert repo.delete_meal(lunch.id) is True
    assert repo.delete_meal(lunch.id) is False
    assert [m.hour for m in repo.meals_on(TODAY)] == [19]


def test_a_meal_quality_outside_one_to_five_is_refused(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO meal_log (date, hour, size, quality) "
            "VALUES ('2026-07-26', 12, 'large', 6)"
        )


def test_deleting_a_visit_reports_whether_there_was_one(repo):
    entry = repo.add_bathroom(TODAY, 8, "stool", "medium", solidity="liquid")
    assert repo.delete_bathroom(entry.id) is True
    assert repo.delete_bathroom(entry.id) is False
