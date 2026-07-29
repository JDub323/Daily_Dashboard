"""The only module that knows SQL exists.

Everything above this returns and accepts Pydantic models. Nothing above this
sees a Row, a cursor, or a JSON blob.

The single most important method here is `record_result`: it reads the current
rating and the event count, computes the update, and writes the event inside
one BEGIN IMMEDIATE. That read-modify-write, spread across a GET and a POST
with the arithmetic done in the browser, is what lost elo when two tabs were
open.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import scoring
from ..models import domain as d
from ..models.common import (
    ChessOutcome,
    ChessStatus,
    Game,
    HealthMetric,
    MathOutcome,
    MathStatus,
    PaperStatus,
    Provenance,
    Track,
)
from .connection import transaction

SEEDS = {Track.math: scoring.MATH_SEED, Track.chess: scoring.CHESS_SEED}


def _iso(value: dt.date | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, dt.date) else value


def _json(value: Any) -> str:
    """Serialise a list/dict for a JSON column, unwrapping Pydantic models."""
    if hasattr(value, "model_dump"):
        return value.model_dump_json()
    return json.dumps(value, default=str)


def _unpack(row: sqlite3.Row, *json_fields: str) -> dict[str, Any]:
    out = dict(row)
    for f in json_fields:
        if out.get(f) is not None:
            out[f] = json.loads(out[f])
    return out


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- ratings ------------------------------------------------------------

    def current_rating(self, track: Track, area: str | None = None) -> int:
        """The last *measured* rating_after, else the seed.

        Reconstructed rows carry NULL ratings, so they cannot contribute a
        rating here -- but a measured row that chained off one keeps whatever
        it recorded at the time. That is the PRESERVE rule: the imported
        trajectory is flagged, not rewritten.

        `area=None` is the pooled rating and matches only `area IS NULL`, so
        adding per-area rows cannot move the number the panel shows. An area
        with no history yet seeds from the *pooled* rating rather than from
        1500: a new bucket means "no evidence about this area", and the best
        available estimate of someone with no evidence is what they score
        everywhere else. Seeding at 1500 would instead assert they are average
        at it, which is a claim, and a wrong one for anyone not rated 1500.
        """
        row = self.conn.execute(
            f"SELECT rating_after FROM rating_event "
            f"WHERE track = ? AND rating_after IS NOT NULL "
            f"AND area {'IS NULL' if area is None else '= ?'} "
            f"ORDER BY id DESC LIMIT 1",
            (Track(track).value,) if area is None else (Track(track).value, area),
        ).fetchone()
        if row:
            return row[0]
        if area is None:
            return SEEDS[Track(track)]
        return self.current_rating(track)

    def event_count(self, track: Track, area: str | None = None) -> int:
        """Attempts on this track -- the `n` that drives the K-factor.

        `reconstructed` rows count: provenance says the recorded numbers are
        untrustworthy, not that the attempt never happened, and K measures how
        much evidence exists.

        `lapsed` rows do not, for exactly the same reason read the other way.
        Nobody attempted anything on a day that was never marked, so counting
        it would let a run of ghosted days shrink K -- growing confidence in a
        rating out of an absence of evidence.

        Counted per area when one is given, so an area's K reflects evidence
        about *that* area. It stays high for a long time, which is correct: an
        area comes round for one week per rotation, so there genuinely is very
        little evidence, and a rating that pretended otherwise would be a
        confident wrong answer rather than an uncertain right one.
        """
        return self.conn.execute(
            f"SELECT COUNT(*) FROM rating_event "
            f"WHERE track = ? AND provenance <> ? "
            f"AND area {'IS NULL' if area is None else '= ?'}",
            (Track(track).value, Provenance.lapsed.value)
            if area is None
            else (Track(track).value, Provenance.lapsed.value, area),
        ).fetchone()[0]

    def area_ratings(self, areas: list[str]) -> list[tuple[str, int, int]]:
        """`(area, rating, measured events)` for each, in the order given.

        Areas with no history report the pooled rating and a count of zero,
        which is what lets the panel show the whole breakdown from day one
        instead of hiding five of six buckets for six months.
        """
        return [
            (area, self.current_rating(Track.math, area), self.event_count(Track.math, area))
            for area in areas
        ]

    def rating_history(self, track: Track, limit: int | None = None) -> list[d.RatingEvent]:
        """The pooled trajectory -- what the elo chart plots.

        `area IS NULL` because the chart is one line. Interleaving six area
        series into it would draw a sawtooth that is not any rating's history.
        """
        sql = "SELECT * FROM rating_event WHERE track = ? AND area IS NULL ORDER BY id"
        rows = self.conn.execute(sql, (Track(track).value,)).fetchall()
        if limit is not None:
            rows = rows[-limit:]
        return [d.RatingEvent(**dict(r)) for r in rows]

    def record_result(
        self,
        track: Track,
        date: dt.date,
        ref: str,
        opponent: int,
        score: float,
    ) -> d.RatingEvent:
        """Apply one result and return the event. Atomic."""
        with transaction(self.conn):
            n = self.event_count(track)
            before = self.current_rating(track)
            k = scoring.k_factor(n)
            after = scoring.elo_update(before, opponent, score, n)
            cur = self.conn.execute(
                "INSERT INTO rating_event "
                "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'measured') RETURNING *",
                (Track(track).value, _iso(date), ref, opponent, score, k, before, after),
            ).fetchone()
            return d.RatingEvent(**dict(cur))

    def success_rate(self, track: Track, window: int = 10) -> int | None:
        """Share of the last `window` *attempts* that scored above a half.

        Lapses are excluded on the same ground as in `event_count`: this is a
        measure of how you do on problems, and a lapse is a problem you did
        not do. Their cost shows up in the rating, which is where it belongs.
        """
        rows = self.conn.execute(
            "SELECT score FROM rating_event WHERE track = ? AND provenance <> ? "
            "AND area IS NULL ORDER BY id DESC LIMIT ?",
            (Track(track).value, Provenance.lapsed.value, window),
        ).fetchall()
        if not rows:
            return None
        return round(100 * sum(1 for r in rows if r[0] > 0.5) / len(rows))

    # -- math ---------------------------------------------------------------

    def seen_sources(self) -> list[str]:
        """Replaces the unbounded `math.seen` list."""
        return [r[0] for r in self.conn.execute("SELECT source FROM math_problem ORDER BY id")]

    def add_problem(self, problem: d.MathProblem) -> d.MathProblem:
        row = self.conn.execute(
            "INSERT INTO math_problem "
            "(source, tier, anchor, statement, answer, hints, verified_url, model, "
            "generated_at, topic, area) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                problem.source,
                problem.tier,
                problem.anchor,
                problem.statement,
                problem.answer,
                _json(problem.hints),
                str(problem.verified_url) if problem.verified_url else None,
                problem.model,
                problem.generated_at.isoformat(),
                problem.topic,
                problem.area,
            ),
        ).fetchone()
        return d.MathProblem(**_unpack(row, "hints"))

    def problem(self, problem_id: int) -> d.MathProblem | None:
        row = self.conn.execute("SELECT * FROM math_problem WHERE id = ?", (problem_id,)).fetchone()
        return d.MathProblem(**_unpack(row, "hints")) if row else None

    def problem_by_source(self, source: str) -> d.MathProblem | None:
        row = self.conn.execute("SELECT * FROM math_problem WHERE source = ?", (source,)).fetchone()
        return d.MathProblem(**_unpack(row, "hints")) if row else None

    def math_attempt(self, date: dt.date) -> d.MathAttempt | None:
        row = self.conn.execute(
            "SELECT * FROM math_attempt WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.MathAttempt(**dict(row)) if row else None

    def open_math_attempt(self, date: dt.date, problem_id: int) -> d.MathAttempt:
        row = self.conn.execute(
            "INSERT INTO math_attempt (date, problem_id) VALUES (?, ?) "
            "ON CONFLICT (date) DO UPDATE SET problem_id = excluded.problem_id RETURNING *",
            (_iso(date), problem_id),
        ).fetchone()
        return d.MathAttempt(**dict(row))

    def reveal_hint(self, date: dt.date) -> d.MathAttempt:
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE math_attempt SET hints_revealed = MIN(hints_revealed + 1, 3) "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (_iso(date),),
            ).fetchone()
            if row is None:
                raise LookupError(f"no open math attempt on {date}")
            return d.MathAttempt(**dict(row))

    def _close_math(
        self,
        date: dt.date,
        *,
        status: MathStatus,
        provenance: Provenance,
        score_for: Callable[[d.MathAttempt], float],
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Close one open attempt and apply its elo update, atomically.

        `score_for` is a callback rather than a number because the score of a
        real finish depends on `hints_revealed`, which is only trustworthy
        once read inside this transaction.

        The guard is the WHERE status = 'open': a double-submit from a second
        tab -- or a lapse racing the finish button at midnight -- updates zero
        rows and raises, instead of rating the same problem twice.
        """
        with transaction(self.conn):
            attempt = self.math_attempt(date)
            if attempt is None:
                raise LookupError(f"no math attempt on {date}")
            if attempt.status is not MathStatus.open:
                raise ValueError(f"math attempt on {date} already {attempt.status.value}")
            problem = self.problem(attempt.problem_id)
            assert problem is not None
            score = score_for(attempt)
            row = self.conn.execute(
                "UPDATE math_attempt SET status = ?, score = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (status.value, score, _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"math attempt on {date} was closed concurrently")

            ev = self._rate_math(date, problem, score, provenance, area=None)
            # The same result, rated again inside its area. Second and inside
            # the same transaction: the pooled row is the one the panel and
            # the chart read, so it must not be able to exist without its
            # partner or vice versa. A problem carrying no area -- every one
            # generated before the column -- rates pooled only.
            if problem.area is not None:
                self._rate_math(date, problem, score, provenance, area=problem.area)
            return d.MathAttempt(**dict(row)), ev

    def _rate_math(
        self,
        date: dt.date,
        problem: d.MathProblem,
        score: float,
        provenance: Provenance,
        *,
        area: str | None,
    ) -> d.RatingEvent:
        """One elo row against the problem's tier anchor. Caller holds the
        transaction; `area=None` is the pooled rating."""
        n = self.event_count(Track.math, area)
        before = self.current_rating(Track.math, area)
        k = scoring.k_factor(n)
        after = scoring.elo_update(before, problem.anchor, score, n)
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, "
            "provenance, area) "
            "VALUES ('math', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                _iso(date),
                problem.source,
                problem.anchor,
                score,
                k,
                before,
                after,
                Provenance(provenance).value,
                area,
            ),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def finish_math(
        self, date: dt.date, outcome: MathOutcome
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Score, close the attempt, and apply the elo update together."""
        outcome = MathOutcome(outcome)
        return self._close_math(
            date,
            status=(MathStatus.solved if outcome is MathOutcome.solved else MathStatus.failed),
            provenance=Provenance.measured,
            score_for=lambda attempt: scoring.math_score(outcome, attempt.hints_revealed),
        )

    def unmarked_math_dates(self, before: dt.date) -> list[dt.date]:
        """Days whose problem was drawn, never marked, and are now over.

        `before` is the browser's today and the comparison is strict: the
        current day is still in progress, and docking someone at breakfast for
        a problem they mean to do after dinner is the one behaviour this
        feature must never have.
        """
        rows = self.conn.execute(
            "SELECT date FROM math_attempt WHERE status = 'open' AND date < ? ORDER BY date",
            (_iso(before),),
        ).fetchall()
        return [dt.date.fromisoformat(r[0]) for r in rows]

    def lapse_math(self, date: dt.date) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Auto-score one unmarked day as a loss on the problem it drew."""
        return self._close_math(
            date,
            status=MathStatus.lapsed,
            provenance=Provenance.lapsed,
            score_for=lambda _: scoring.LAPSE_SCORE,
        )

    def reconcile_math_lapses(self, today: dt.date) -> list[d.RatingEvent]:
        """Close out every unmarked past day. Returns the events applied.

        Idempotent by construction: a lapsed attempt is no longer `open`, so
        the second call finds nothing. Each day is its own transaction and
        chains off the rating the previous one produced, so a three-day gap
        costs three successive results rather than one compounded guess.
        """
        return [self.lapse_math(date)[1] for date in self.unmarked_math_dates(today)]

    # -- chess --------------------------------------------------------------

    def upsert_puzzle(self, puzzle: d.ChessPuzzle) -> d.ChessPuzzle:
        row = self.conn.execute(
            "INSERT INTO chess_puzzle (id, rating, themes, url) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET rating = excluded.rating, "
            "themes = excluded.themes, url = excluded.url RETURNING *",
            (puzzle.id, puzzle.rating, _json(puzzle.themes), str(puzzle.url)),
        ).fetchone()
        return d.ChessPuzzle(**_unpack(row, "themes"))

    def chess_puzzle(self, puzzle_id: str) -> d.ChessPuzzle | None:
        row = self.conn.execute("SELECT * FROM chess_puzzle WHERE id = ?", (puzzle_id,)).fetchone()
        return d.ChessPuzzle(**_unpack(row, "themes")) if row else None

    def chess_attempt(self, date: dt.date) -> d.ChessAttempt | None:
        row = self.conn.execute(
            "SELECT * FROM chess_attempt WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.ChessAttempt(**dict(row)) if row else None

    def open_chess_attempt(self, date: dt.date, puzzle_id: str) -> d.ChessAttempt:
        row = self.conn.execute(
            "INSERT INTO chess_attempt (date, puzzle_id) VALUES (?, ?) "
            "ON CONFLICT (date) DO UPDATE SET puzzle_id = excluded.puzzle_id RETURNING *",
            (_iso(date), puzzle_id),
        ).fetchone()
        return d.ChessAttempt(**dict(row))

    def finish_chess(
        self, date: dt.date, outcome: ChessOutcome
    ) -> tuple[d.ChessAttempt, d.RatingEvent]:
        with transaction(self.conn):
            attempt = self.chess_attempt(date)
            if attempt is None:
                raise LookupError(f"no chess attempt on {date}")
            if attempt.status is not ChessStatus.open:
                raise ValueError(f"chess attempt on {date} already done")
            puzzle = self.chess_puzzle(attempt.puzzle_id)
            assert puzzle is not None
            score = scoring.chess_score(outcome)
            row = self.conn.execute(
                "UPDATE chess_attempt SET status = 'done', score = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (score, _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"chess attempt on {date} was closed concurrently")

            n = self.event_count(Track.chess)
            before = self.current_rating(Track.chess)
            k = scoring.k_factor(n)
            after = scoring.elo_update(before, puzzle.rating, score, n)
            ev = self.conn.execute(
                "INSERT INTO rating_event "
                "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
                "VALUES ('chess', ?, ?, ?, ?, ?, ?, ?, 'measured') RETURNING *",
                (_iso(date), puzzle.id, puzzle.rating, score, k, before, after),
            ).fetchone()
            return d.ChessAttempt(**dict(row)), d.RatingEvent(**dict(ev))

    # -- word ---------------------------------------------------------------

    def word(self, date: dt.date) -> d.WordOfDay | None:
        row = self.conn.execute(
            "SELECT * FROM word_of_day WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.WordOfDay(**_unpack(row, "sentences", "grade")) if row else None

    def upsert_word(self, word: d.WordOfDay) -> d.WordOfDay:
        row = self.conn.execute(
            "INSERT INTO word_of_day (date, word, pos, definition, sentences, grade) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET "
            "word = excluded.word, pos = excluded.pos, definition = excluded.definition, "
            "sentences = excluded.sentences, grade = excluded.grade RETURNING *",
            (
                _iso(word.date),
                word.word,
                word.pos,
                word.definition,
                _json(word.sentences),
                _json(word.grade) if word.grade else None,
            ),
        ).fetchone()
        return d.WordOfDay(**_unpack(row, "sentences", "grade"))

    def set_sentences(self, date: dt.date, sentences: list[str]) -> None:
        self.conn.execute(
            "UPDATE word_of_day SET sentences = ? WHERE date = ?",
            (_json(sentences), _iso(date)),
        )

    def set_grade(self, date: dt.date, grade: d.WordGrade | None) -> None:
        self.conn.execute(
            "UPDATE word_of_day SET grade = ? WHERE date = ?",
            (_json(grade) if grade else None, _iso(date)),
        )

    # -- games --------------------------------------------------------------

    def upsert_wordle(self, result: d.WordleResult) -> d.WordleResult:
        row = self.conn.execute(
            "INSERT INTO wordle_result (date, puzzle_no, guesses, won, grid) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET "
            "puzzle_no = excluded.puzzle_no, guesses = excluded.guesses, "
            "won = excluded.won, grid = excluded.grid RETURNING *",
            (
                _iso(result.date),
                result.puzzle_no,
                result.guesses,
                int(result.won),
                _json(result.grid),
            ),
        ).fetchone()
        out = _unpack(row, "grid")
        out["won"] = bool(out["won"])
        return d.WordleResult(**out)

    def upsert_pokedoku(self, result: d.PokedokuResult) -> d.PokedokuResult:
        row = self.conn.execute(
            "INSERT INTO pokedoku_result (date, correct, score, grid) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET correct = excluded.correct, "
            "score = excluded.score, grid = excluded.grid RETURNING *",
            (_iso(result.date), result.correct, result.score, _json(result.grid)),
        ).fetchone()
        return d.PokedokuResult(**_unpack(row, "grid"))

    def wordle(self, date: dt.date) -> d.WordleResult | None:
        row = self.conn.execute(
            "SELECT * FROM wordle_result WHERE date = ?", (_iso(date),)
        ).fetchone()
        if not row:
            return None
        out = _unpack(row, "grid")
        out["won"] = bool(out["won"])
        return d.WordleResult(**out)

    def pokedoku(self, date: dt.date) -> d.PokedokuResult | None:
        row = self.conn.execute(
            "SELECT * FROM pokedoku_result WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.PokedokuResult(**_unpack(row, "grid")) if row else None

    def played_dates(self, game: Game) -> list[dt.date]:
        table = "wordle_result" if Game(game) is Game.wordle else "pokedoku_result"
        return [
            dt.date.fromisoformat(r[0])
            for r in self.conn.execute(f"SELECT date FROM {table} ORDER BY date")
        ]

    def wordle_average(self) -> float | None:
        row = self.conn.execute(
            "SELECT AVG(guesses) FROM wordle_result WHERE guesses IS NOT NULL"
        ).fetchone()
        return round(row[0], 2) if row[0] is not None else None

    # -- theorems -----------------------------------------------------------

    def add_card(self, card: d.TheoremCard) -> d.TheoremCard:
        row = self.conn.execute(
            "INSERT INTO theorem_card (name, statement, topic, added, archived) "
            "VALUES (?, ?, ?, ?, ?) RETURNING *",
            (card.name, card.statement, card.topic, _iso(card.added), int(card.archived)),
        ).fetchone()
        return d.TheoremCard(**dict(row))

    def card(self, card_id: int) -> d.TheoremCard | None:
        row = self.conn.execute("SELECT * FROM theorem_card WHERE id = ?", (card_id,)).fetchone()
        return d.TheoremCard(**dict(row)) if row else None

    def deck(self, *, include_archived: bool = False) -> list[d.TheoremCard]:
        """The deck, oldest first. Archived cards are excluded by default --
        every caller that draws wants the active deck, and the one caller that
        renders the management list asks for the rest explicitly."""
        where = "" if include_archived else "WHERE archived = 0"
        rows = self.conn.execute(f"SELECT * FROM theorem_card {where} ORDER BY id").fetchall()
        return [d.TheoremCard(**dict(r)) for r in rows]

    def deck_size(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM theorem_card WHERE archived = 0").fetchone()[
            0
        ]

    def set_archived(self, card_id: int, archived: bool) -> d.TheoremCard:
        row = self.conn.execute(
            "UPDATE theorem_card SET archived = ? WHERE id = ? RETURNING *",
            (int(archived), card_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no theorem card {card_id}")
        return d.TheoremCard(**dict(row))

    # -- the draw log -------------------------------------------------------

    def last_drawn(self) -> dict[int, dt.date]:
        """`card_id -> the last date it appeared`, for cards that have.

        One aggregate for the whole deck rather than a query per card: the
        weights are computed together, and a per-card MAX() would make the
        draw's cost scale with the deck.
        """
        rows = self.conn.execute(
            "SELECT card_id, MAX(date) FROM theorem_draw GROUP BY card_id"
        ).fetchall()
        return {r[0]: dt.date.fromisoformat(r[1]) for r in rows}

    def draw(self, on: dt.date) -> d.TheoremDraw | None:
        row = self.conn.execute("SELECT * FROM theorem_draw WHERE date = ?", (_iso(on),)).fetchone()
        return d.TheoremDraw(**dict(row)) if row else None

    def record_draw(self, on: dt.date, card_id: int) -> d.TheoremDraw:
        """Claim the day for a card, or return the card that already has it.

        `ON CONFLICT DO NOTHING` plus a re-read rather than a check-then-write:
        two tabs opening the dashboard at once would both see no draw and both
        insert, and the loser of that race should get today's actual theorem
        back, not an IntegrityError.
        """
        self.conn.execute(
            "INSERT INTO theorem_draw (date, card_id) VALUES (?, ?) ON CONFLICT (date) DO NOTHING",
            (_iso(on), card_id),
        )
        drawn = self.draw(on)
        assert drawn is not None  # just inserted, or already there
        return drawn

    def record_recall(self, on: dt.date, recalled: bool) -> d.TheoremDraw:
        row = self.conn.execute(
            "UPDATE theorem_draw SET recalled = ? WHERE date = ? RETURNING *",
            (int(recalled), _iso(on)),
        ).fetchone()
        if row is None:
            raise LookupError(f"no theorem drawn on {_iso(on)}")
        return d.TheoremDraw(**dict(row))

    def recall_counts(self) -> dict[int, tuple[int, int]]:
        """`card_id -> (times seen, times recalled)` across all history.

        An unanswered draw counts as seen and not as recalled, which is what
        `SUM(recalled)` over NULLs gives for free.
        """
        rows = self.conn.execute(
            "SELECT card_id, COUNT(*), COALESCE(SUM(recalled), 0) "
            "FROM theorem_draw GROUP BY card_id"
        ).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    # -- papers -------------------------------------------------------------

    def add_paper(self, paper: d.Paper) -> d.Paper:
        row = self.conn.execute(
            "INSERT INTO paper "
            "(title, link, summary, published, status, surfaced_on, added, queue_pos) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                paper.title,
                str(paper.link),
                paper.summary,
                _iso(paper.published),
                PaperStatus(paper.status).value,
                _iso(paper.surfaced_on),
                _iso(paper.added),
                paper.queue_pos,
            ),
        ).fetchone()
        return d.Paper(**dict(row))

    def surfaced_paper(self, on: dt.date) -> d.Paper | None:
        row = self.conn.execute(
            "SELECT * FROM paper WHERE surfaced_on = ? ORDER BY id DESC LIMIT 1", (_iso(on),)
        ).fetchone()
        return d.Paper(**dict(row)) if row else None

    def paper(self, paper_id: int) -> d.Paper | None:
        row = self.conn.execute("SELECT * FROM paper WHERE id = ?", (paper_id,)).fetchone()
        return d.Paper(**dict(row)) if row else None

    def queue(self) -> list[d.Paper]:
        rows = self.conn.execute(
            "SELECT * FROM paper WHERE status = 'queued' ORDER BY queue_pos, id"
        ).fetchall()
        return [d.Paper(**dict(r)) for r in rows]

    def cleared_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM paper WHERE status = 'cleared'").fetchone()[
            0
        ]

    def next_queue_pos(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(queue_pos), -1) + 1 FROM paper WHERE status = 'queued'"
        ).fetchone()
        return row[0]

    def set_paper_status(
        self, paper_id: int, status: PaperStatus, queue_pos: int | None = None
    ) -> d.Paper:
        row = self.conn.execute(
            "UPDATE paper SET status = ?, queue_pos = ? WHERE id = ? RETURNING *",
            (PaperStatus(status).value, queue_pos, paper_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no paper {paper_id}")
        return d.Paper(**dict(row))

    def promote_in_queue(self, paper_id: int) -> None:
        """Swap a queued paper with the one above it.

        Both writes in one transaction: the old client spliced a JS array and
        saved the whole subtree, so an interleaved save from a second tab
        could persist half a swap.
        """
        with transaction(self.conn):
            ids = [
                r[0]
                for r in self.conn.execute(
                    "SELECT id FROM paper WHERE status = 'queued' ORDER BY queue_pos, id"
                )
            ]
            if paper_id not in ids:
                raise LookupError(f"paper {paper_id} is not queued")
            i = ids.index(paper_id)
            if i == 0:
                return
            ids[i - 1], ids[i] = ids[i], ids[i - 1]
            # Renumber the whole queue rather than swapping two values:
            # queue_pos has no uniqueness constraint, so a queue that ever
            # acquired duplicate positions would make a pairwise swap a no-op.
            self.conn.executemany(
                "UPDATE paper SET queue_pos = ? WHERE id = ?",
                [(pos, pid) for pos, pid in enumerate(ids)],
            )

    # -- health / log / completion -----------------------------------------

    def log_health(self, metric: HealthMetric, date: dt.date, value: float) -> d.HealthEntry:
        row = self.conn.execute(
            "INSERT INTO health_entry (metric, date, value) VALUES (?, ?, ?) "
            "ON CONFLICT (metric, date) DO UPDATE SET value = excluded.value RETURNING *",
            (HealthMetric(metric).value, _iso(date), value),
        ).fetchone()
        return d.HealthEntry(**dict(row))

    def health_series(self, metric: HealthMetric, limit: int = 30) -> list[d.HealthEntry]:
        rows = self.conn.execute(
            "SELECT * FROM health_entry WHERE metric = ? ORDER BY date",
            (HealthMetric(metric).value,),
        ).fetchall()
        return [d.HealthEntry(**dict(r)) for r in rows[-limit:]]

    def daylog(self, date: dt.date) -> str | None:
        row = self.conn.execute("SELECT text FROM daylog WHERE date = ?", (_iso(date),)).fetchone()
        return row[0] if row else None

    def set_daylog(self, date: dt.date, text: str) -> None:
        self.conn.execute(
            "INSERT INTO daylog (date, text) VALUES (?, ?) "
            "ON CONFLICT (date) DO UPDATE SET text = excluded.text",
            (_iso(date), text),
        )

    def set_completion(self, date: dt.date, done: int, total: int) -> d.DayCompletion:
        row = self.conn.execute(
            "INSERT INTO day_completion (date, done, total) VALUES (?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET done = excluded.done, total = excluded.total "
            "RETURNING *",
            (_iso(date), done, total),
        ).fetchone()
        return d.DayCompletion(**dict(row))

    def completions(self) -> list[d.DayCompletion]:
        rows = self.conn.execute("SELECT * FROM day_completion ORDER BY date").fetchall()
        return [d.DayCompletion(**dict(r)) for r in rows]

    def perfect_days(self) -> list[dt.date]:
        return [
            dt.date.fromisoformat(r[0])
            for r in self.conn.execute(
                "SELECT date FROM day_completion WHERE done = total ORDER BY date"
            )
        ]

    # -- topics -------------------------------------------------------------

    def topic(self, topic: str) -> d.TopicInfo | None:
        row = self.conn.execute("SELECT * FROM topic_info WHERE topic = ?", (topic,)).fetchone()
        return d.TopicInfo(**_unpack(row, "textbook", "theorems")) if row else None

    def upsert_topic(self, info: d.TopicInfo) -> d.TopicInfo:
        row = self.conn.execute(
            "INSERT INTO topic_info (topic, textbook, theorems) VALUES (?, ?, ?) "
            "ON CONFLICT (topic) DO UPDATE SET textbook = excluded.textbook, "
            "theorems = excluded.theorems RETURNING *",
            (
                info.topic,
                _json(info.textbook),
                _json([t.model_dump(mode="json") for t in info.theorems]),
            ),
        ).fetchone()
        return d.TopicInfo(**_unpack(row, "textbook", "theorems"))

    # -- import support -----------------------------------------------------

    def insert_rating_event(self, event: d.RatingEvent) -> d.RatingEvent:
        """Verbatim insert, used only by the importer.

        Everything else must go through `record_result`, which derives the
        rating rather than accepting one. This exists because imported history
        carries ratings that were computed by the old client -- or, for
        reconstructed rows, never computed at all.
        """
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                Track(event.track).value,
                _iso(event.date),
                event.ref,
                event.opponent,
                event.score,
                event.k,
                event.rating_before,
                event.rating_after,
                Provenance(event.provenance).value,
            ),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM rating_event").fetchone()[0] == 0
