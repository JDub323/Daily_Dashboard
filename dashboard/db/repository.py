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
    DebateStatus,
    Deck,
    Game,
    HealthMetric,
    MathOutcome,
    MathStatus,
    PaperStatus,
    Provenance,
    Track,
)
from .connection import transaction

SEEDS = {
    Track.math: scoring.MATH_SEED,
    Track.chess: scoring.CHESS_SEED,
    Track.debate: scoring.DEBATE_SEED,
    Track.spelling: scoring.SPELLING_SEED,
}

ARGUMENT_AREA = "argument-patterns"
"""The bucket an argument-pattern recall rates into, on the debate track.

Deliberately not one of the configured issue slugs, and that is the whole
design. A pattern like principal-agent is not *about* immigration or Taiwan --
it applies everywhere -- so filing it under whichever issue the week happened
to be showing would be an arbitrary attribution that then shows up in the
per-issue breakdown as evidence about that issue.

Being outside the issue list also keeps it out of the panel strip and the
trends chart, both of which iterate `cfg.debate.issue_slugs`. It is read in one
place, the argument deck's own panel, which is where "do I actually know my
patterns" is the question being asked.
"""

NOT_ATTEMPTS = (Provenance.lapsed, Provenance.unlapsed, Provenance.theorem)
"""Rows that move the rating without anybody having attempted a *problem*.

Excluded from the K-factor's `n`, because `n` measures how much evidence
exists about problem solving. A lapse is a day nobody did; an unlapse is the
bookkeeping that cancels one; a theorem recall is evidence about something
else. Left in, a daily recall would take K from 43 to its floor of 24 in about
two months and keep it there -- so the rating would stop responding to the
problems, which are the only thing it is meant to measure.
"""

_ATTEMPT_ONLY = frozenset({"a_date", "problem_id", "hints_revealed", "status", "score", "minutes"})
"""Columns the archive join adds for the attempt side. Everything else in the
row belongs to `math_problem` -- `id` included, which is why the attempt's own
date is aliased rather than selected twice."""

NOT_SUCCESSES = (*NOT_ATTEMPTS, Provenance.late)
"""...and additionally the rows whose score cannot be read against the 0.5
threshold `success_rate` uses. A late result is a real attempt, so it counts
toward `n` -- but it is scored out of 0.40, so it can never clear a half."""


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
        placeholders = ", ".join("?" * len(NOT_ATTEMPTS))
        params: tuple[object, ...] = (Track(track).value, *[p.value for p in NOT_ATTEMPTS])
        return self.conn.execute(
            f"SELECT COUNT(*) FROM rating_event "
            f"WHERE track = ? AND provenance NOT IN ({placeholders}) "
            f"AND area {'IS NULL' if area is None else '= ?'}",
            params if area is None else (*params, area),
        ).fetchone()[0]

    def area_ratings(
        self, areas: list[str], track: Track = Track.math
    ) -> list[tuple[str, int, int]]:
        """`(area, rating, measured events)` for each, in the order given.

        Areas with no history report the pooled rating and a count of zero,
        which is what lets the panel show the whole breakdown from day one
        instead of hiding five of six buckets for six months.

        `track` because the debate's issues are the same idea in a second
        bucket space: one row per (track, area), read the same way.
        """
        return [
            (area, self.current_rating(track, area), self.event_count(track, area))
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

        Late results are excluded for the opposite reason. They *are* attempts
        -- that is why they count toward `n` -- but their score is deflated on
        purpose and tops out at 0.40, so against a threshold of 0.5 every late
        solve would read as a failure and a week of catching up would look
        like a week of getting worse.
        """
        placeholders = ", ".join("?" * len(NOT_SUCCESSES))
        rows = self.conn.execute(
            f"SELECT score FROM rating_event WHERE track = ? "
            f"AND provenance NOT IN ({placeholders}) "
            f"AND area IS NULL ORDER BY id DESC LIMIT ?",
            (Track(track).value, *[p.value for p in NOT_SUCCESSES], window),
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
        minutes: int | None = None,
        multiplier_for: Callable[[d.MathProblem], float] | None = None,
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Close one open attempt and apply its elo update, atomically.

        `score_for` is a callback rather than a number because the score of a
        real finish depends on `hints_revealed`, which is only trustworthy
        once read inside this transaction. `multiplier_for` is one for the
        matching reason: the speed bonus is measured against the problem's
        tier, and the problem is not known until the row has been read.

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
            multiplier = multiplier_for(problem) if multiplier_for else 1.0
            row = self.conn.execute(
                "UPDATE math_attempt SET status = ?, score = ?, minutes = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (status.value, score, minutes, _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"math attempt on {date} was closed concurrently")

            ev = self._rate_math(date, problem, score, provenance, area=None, multiplier=multiplier)
            # The same result, rated again inside its area. Second and inside
            # the same transaction: the pooled row is the one the panel and
            # the chart read, so it must not be able to exist without its
            # partner or vice versa. A problem carrying no area -- every one
            # generated before the column -- rates pooled only.
            if problem.area is not None:
                self._rate_math(
                    date, problem, score, provenance, area=problem.area, multiplier=multiplier
                )
            return d.MathAttempt(**dict(row)), ev

    def _rate_math(
        self,
        date: dt.date,
        problem: d.MathProblem,
        score: float,
        provenance: Provenance,
        *,
        area: str | None,
        multiplier: float = 1.0,
    ) -> d.RatingEvent:
        """One elo row against the problem's tier anchor. Caller holds the
        transaction; `area=None` is the pooled rating.

        The K written to the row is the one actually applied, speed bonus
        included -- so `rating_after - rating_before == k * (score -
        expected)` stays checkable by hand, which is the whole reason the
        column exists. Both rows for a result take the same multiplier: the
        pooled and area ratings are two readings of one event, and a bonus that
        applied to one but not the other would make them disagree about what
        happened.
        """
        n = self.event_count(Track.math, area)
        before = self.current_rating(Track.math, area)
        k, after = scoring.timed_elo(before, problem.anchor, score, n, multiplier)
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
        self,
        date: dt.date,
        outcome: MathOutcome,
        minutes: int | None = None,
        par_minutes: dict[str, int] | None = None,
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Score, close the attempt, and apply the elo update together.

        `par_minutes` is the tier->reference-time table, passed in as a plain
        dict rather than reached for through `Settings`: this module's rule is
        that it is the only one that knows SQL, and the price of that is that
        it is not allowed to know about config either.

        A failure discards `minutes` before it is stored. Time is only read on
        a solve, so keeping the number would leave a column on the row that
        nothing consults and that reads, later, as though it once mattered.
        """
        outcome = MathOutcome(outcome)
        solved = outcome is MathOutcome.solved
        pars = par_minutes or {}
        return self._close_math(
            date,
            status=(MathStatus.solved if solved else MathStatus.failed),
            provenance=Provenance.measured,
            score_for=lambda attempt: scoring.math_score(outcome, attempt.hints_revealed),
            minutes=minutes if solved else None,
            multiplier_for=(
                (lambda problem: scoring.time_multiplier(minutes, pars.get(problem.tier)))
                if solved
                else None
            ),
        )

    def finish_math_late(
        self,
        today: dt.date,
        target: dt.date,
        outcome: MathOutcome,
        minutes: int | None = None,
        par_minutes: dict[str, int] | None = None,
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Submit a lapsed day afterwards. Replaces the lapse; atomic.

        Three things happen in one transaction, in this order:

        1. every `lapsed` rating row for `target` is cancelled by an
           `unlapsed` row that hands back exactly its delta;
        2. the attempt flips from `lapsed` to solved/failed at
           `late_math_score`;
        3. the late result is rated normally, chaining off the rating the
           cancellations just restored.

        The cancellation is a new row rather than an edit because the log is
        append-only and its lineage is preserved, not rewritten -- the same
        rule that keeps the imported `reconstructed` rows in place. What it
        cannot be is exact in a stronger sense: results recorded *after* the
        lapse chained off the depressed rating, and no single row can unwind
        that. Handing back the delta puts the rating where it would be if the
        lapse had scored what the late submission scores, which is the
        question being asked, and is why this is a window rather than
        something you can do to a lapse from March.
        """
        outcome = MathOutcome(outcome)
        if target >= today:
            raise ValueError(f"{target} is not a past day")
        if (today - target).days > scoring.LATE_WINDOW_DAYS:
            raise ValueError(
                f"{target} is more than {scoring.LATE_WINDOW_DAYS} days ago; that lapse is final"
            )

        with transaction(self.conn):
            attempt = self.math_attempt(target)
            if attempt is None:
                raise LookupError(f"no math attempt on {target}")
            if attempt.status is not MathStatus.lapsed:
                # Guards the double-submit and the wrong-status case together:
                # an already-solved day is not a lapse to be replaced.
                raise ValueError(f"math attempt on {target} is {attempt.status.value}, not lapsed")
            problem = self.problem(attempt.problem_id)
            assert problem is not None

            solved = outcome is MathOutcome.solved
            score = scoring.late_math_score(outcome, attempt.hints_revealed)
            # Speed still counts on a day submitted late. The deflation for
            # lateness is already in `score`, and the two are answers to
            # different questions -- when you did it, and how long it took --
            # so charging the second one twice would be charging it for the
            # first.
            multiplier = (
                scoring.time_multiplier(minutes, (par_minutes or {}).get(problem.tier))
                if solved
                else 1.0
            )
            row = self.conn.execute(
                "UPDATE math_attempt SET status = ?, score = ?, minutes = ? "
                "WHERE date = ? AND status = 'lapsed' RETURNING *",
                (
                    (MathStatus.solved if solved else MathStatus.failed),
                    score,
                    minutes if solved else None,
                    _iso(target),
                ),
            ).fetchone()
            if row is None:
                raise ValueError(f"math attempt on {target} was changed concurrently")

            # Pooled first and area second, matching `_close_math` -- the
            # pooled row is what the panel and the chart read, so it must not
            # be able to exist without its partner or vice versa.
            self._unlapse(target, problem, area=None)
            ev = self._rate_math(
                target, problem, score, Provenance.late, area=None, multiplier=multiplier
            )
            if problem.area is not None:
                self._unlapse(target, problem, area=problem.area)
                self._rate_math(
                    target,
                    problem,
                    score,
                    Provenance.late,
                    area=problem.area,
                    multiplier=multiplier,
                )
            return d.MathAttempt(**dict(row)), ev

    def _unlapse(self, date: dt.date, problem: d.MathProblem, *, area: str | None) -> None:
        """Hand back exactly what the lapse on `date` took. Caller holds the
        transaction.

        A no-op when there is no lapse row for that day and area, which is the
        case for a problem carrying no area: those rated pooled only, so there
        is nothing to cancel in the area chain.

        `score` and `k` are copied off the lapse row. Neither means anything
        here -- nothing was scored and no K was applied -- but both columns are
        NOT NULL with CHECKs, and carrying the lapse's own values is the least
        inventive way to satisfy them. `NOT_ATTEMPTS` keeps every reader away
        from them.
        """
        lapse = self.conn.execute(
            f"SELECT * FROM rating_event WHERE track = 'math' AND date = ? "
            f"AND provenance = ? AND area {'IS NULL' if area is None else '= ?'} "
            f"ORDER BY id DESC LIMIT 1",
            (_iso(date), Provenance.lapsed.value)
            if area is None
            else (_iso(date), Provenance.lapsed.value, area),
        ).fetchone()
        if lapse is None:
            return

        delta = lapse["rating_after"] - lapse["rating_before"]
        before = self.current_rating(Track.math, area)
        self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, "
            "provenance, area) "
            "VALUES ('math', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _iso(date),
                problem.source,
                problem.anchor,
                lapse["score"],
                lapse["k"],
                before,
                before - delta,
                Provenance.unlapsed.value,
                area,
            ),
        )

    def late_eligible_dates(self, today: dt.date) -> list[dt.date]:
        """Lapsed days still inside the submission window."""
        earliest = today - dt.timedelta(days=scoring.LATE_WINDOW_DAYS)
        rows = self.conn.execute(
            "SELECT date FROM math_attempt WHERE status = 'lapsed' "
            "AND date >= ? AND date < ? ORDER BY date DESC",
            (_iso(earliest), _iso(today)),
        ).fetchall()
        return [dt.date.fromisoformat(r[0]) for r in rows]

    def math_history(self, limit: int = 60) -> list[tuple[d.MathAttempt, d.MathProblem]]:
        """Past attempts with their problems, newest first.

        Joined here rather than looped in the caller: the archive is the one
        read that wants every problem at once, and N+1 selects over sixty days
        is the shape that turns a modal into a spinner.
        """
        rows = self.conn.execute(
            "SELECT a.date AS a_date, a.problem_id, a.hints_revealed, a.status, a.score, "
            "a.minutes, p.* FROM math_attempt a JOIN math_problem p ON p.id = a.problem_id "
            "ORDER BY a.date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = _unpack(row, "hints")
            attempt = d.MathAttempt(
                date=data["a_date"],
                problem_id=data["problem_id"],
                hints_revealed=data["hints_revealed"],
                status=data["status"],
                score=data["score"],
                minutes=data["minutes"],
            )
            problem = d.MathProblem(**{k: v for k, v in data.items() if k not in _ATTEMPT_ONLY})
            out.append((attempt, problem))
        return out

    def rating_deltas(self, dates: list[dt.date]) -> dict[dt.date, int]:
        """Net pooled rating change per date, for the archive's margin.

        Summed rather than taken from one row because a day can carry more
        than one: a lapse later replaced by a late result leaves three rows on
        the same date, and their net is what actually happened to the rating.
        """
        if not dates:
            return {}
        placeholders = ", ".join("?" * len(dates))
        rows = self.conn.execute(
            f"SELECT date, SUM(rating_after - rating_before) FROM rating_event "
            f"WHERE track = 'math' AND area IS NULL AND rating_after IS NOT NULL "
            f"AND date IN ({placeholders}) GROUP BY date",
            [_iso(d) for d in dates],
        ).fetchall()
        return {dt.date.fromisoformat(r[0]): r[1] for r in rows}

    def area_rating_history(
        self, track: Track = Track.math
    ) -> dict[str, list[tuple[dt.date, int]]]:
        """`area -> [(date, rating_after)]`, oldest first, measured rows only.

        The per-area counterpart to `rating_history`, which is pooled-only
        because the pooled chart is one line. Here the areas are the point, so
        they come back grouped rather than interleaved -- one query for all of
        them, because the alternative is a query per area and the caller wants
        every one of them every time.

        `rating_after IS NOT NULL` drops the reconstructed rows, whose
        trajectories were interpolated by the old backfill and are flagged
        rather than trusted. Plotting them would draw a line through numbers
        nobody measured.

        `track` because the debate's issues are the same idea in a second
        bucket space, and its per-issue chart wants exactly this query.
        """
        rows = self.conn.execute(
            "SELECT area, date, rating_after FROM rating_event "
            "WHERE track = ? AND area IS NOT NULL AND rating_after IS NOT NULL "
            "ORDER BY id",
            (Track(track).value,),
        ).fetchall()
        out: dict[str, list[tuple[dt.date, int]]] = {}
        for row in rows:
            out.setdefault(row[0], []).append((dt.date.fromisoformat(row[1]), row[2]))
        return out

    def math_attempt_count(self, since: dt.date, until: dt.date) -> int:
        """Problems drawn in `[since, until]`, whatever became of them.

        Counted for the spend strip, against the number of *paid* generations
        in the metrics log. Every attempt row is a problem that was served;
        only some of them cost anything, and the difference is what the
        overnight batch and the curated pool covered.
        """
        return self.conn.execute(
            "SELECT COUNT(*) FROM math_attempt WHERE date BETWEEN ? AND ?",
            (_iso(since), _iso(until)),
        ).fetchone()[0]

    def daylogs(self, limit: int = 180) -> list[d.DayLog]:
        """Every day log, newest first.

        `daylog(today)` was the only read path in the app: months of writing
        into a box that nothing would ever show back. This is the other half.
        """
        rows = self.conn.execute(
            "SELECT date, text FROM daylog ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [d.DayLog(**dict(r)) for r in rows]

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

    # -- spelling -----------------------------------------------------------

    def spelling_round(self, date: dt.date) -> d.SpellingRound | None:
        row = self.conn.execute(
            "SELECT * FROM spelling_round WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.SpellingRound(**_unpack(row, "words", "answers")) if row else None

    def open_spelling_round(self, date: dt.date, words: list[d.SpellingWord]) -> d.SpellingRound:
        """Claim the day for a set of words, or hand back the ones it has.

        `ON CONFLICT DO NOTHING` and a re-read, the shape `record_draw` uses:
        two tabs opening at once both see no round and both insert, and the
        loser should get today's actual words rather than an IntegrityError.
        It is also what stops a reload redrawing -- which here would not merely
        reroll a puzzle but hand you a fresh set after you had heard the first.
        """
        self.conn.execute(
            "INSERT INTO spelling_round (date, words) VALUES (?, ?) ON CONFLICT (date) DO NOTHING",
            (_iso(date), _json([w.model_dump(mode="json") for w in words])),
        )
        opened = self.spelling_round(date)
        assert opened is not None  # just inserted, or already there
        return opened

    def finish_spelling(
        self, date: dt.date, answers: list[str], marks: list[bool]
    ) -> tuple[d.SpellingRound, list[d.RatingEvent]]:
        """Close the round and rate every word, atomically.

        One rating event *per word*, each against that word's own anchor, which
        is what makes the ladder mean anything: missing the `brutal` word costs
        little and getting it right pays, and the `everyday` word is the other
        way round. A single event against a mean anchor would score a day that
        got the hard one and missed the easy one identically to its reverse.

        `WHERE status = 'open'` is the double-submit guard, as everywhere else
        here: a second tab submitting the same round updates zero rows and
        raises rather than rating five words twice.
        """
        with transaction(self.conn):
            existing = self.spelling_round(date)
            if existing is None:
                raise LookupError(f"no spelling round on {date}")
            if existing.status != "open":
                raise ValueError(f"the spelling round on {date} is already done")

            row = self.conn.execute(
                "UPDATE spelling_round SET status = 'done', answers = ?, correct = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (_json(answers), sum(marks), _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"the spelling round on {date} was closed concurrently")

            events = [
                self._rate_word(date, word, ok)
                for word, ok in zip(existing.words, marks, strict=False)
            ]
            for word, ok in zip(existing.words, marks, strict=False):
                self._record_word_seen(date, word, ok)
            return d.SpellingRound(**_unpack(row, "words", "answers")), events

    def _rate_word(self, date: dt.date, word: d.SpellingWord, ok: bool) -> d.RatingEvent:
        """One elo row against the word's frozen anchor. Caller holds the
        transaction. Read inside the loop rather than batched, so each word
        chains off the rating the previous one produced -- five results in a
        row, exactly as five days of results would be."""
        n = self.event_count(Track.spelling)
        before = self.current_rating(Track.spelling)
        k = scoring.k_factor(n)
        after = scoring.elo_update(before, word.anchor, 1.0 if ok else 0.0, n)
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, provenance) "
            "VALUES ('spelling', ?, ?, ?, ?, ?, ?, ?, 'measured') RETURNING *",
            (_iso(date), word.word, word.anchor, 1.0 if ok else 0.0, k, before, after),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def _record_word_seen(self, date: dt.date, word: d.SpellingWord, ok: bool) -> None:
        """Add the word to the draw's memory. Caller holds the transaction.

        `first_seen` is never moved forward and `asked`/`missed` accumulate, so
        this is the whole basis for "prefer the ones I keep getting wrong".
        """
        self.conn.execute(
            "INSERT INTO spelling_seen (word, tier, first_seen, last_seen, asked, missed) "
            "VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT (word) DO UPDATE SET last_seen = excluded.last_seen, "
            "tier = excluded.tier, asked = asked + 1, missed = missed + excluded.missed",
            (word.word.casefold(), word.tier, _iso(date), _iso(date), 0 if ok else 1),
        )

    def spelling_seen(self) -> dict[str, tuple[dt.date, int]]:
        """`word -> (last seen, times missed)`, casefolded. What the draw reads."""
        return {
            r[0]: (dt.date.fromisoformat(r[1]), r[2])
            for r in self.conn.execute("SELECT word, last_seen, missed FROM spelling_seen")
        }

    def spelling_trouble(self, limit: int = 8) -> list[tuple[str, int, int]]:
        """`(word, missed, asked)` for the ones you get wrong most, worst first.

        The panel's one backward-looking line. A rating says whether you are
        improving; this says which words to look at, which is the thing the
        feature was actually asked for.
        """
        return [
            (r[0], r[1], r[2])
            for r in self.conn.execute(
                "SELECT word, missed, asked FROM spelling_seen WHERE missed > 0 "
                "ORDER BY missed DESC, asked DESC, word LIMIT ?",
                (limit,),
            )
        ]

    # -- debate -------------------------------------------------------------
    #
    # Shaped after the math half above rather than after the theorem deck: a
    # session is opened, worked on, and closed with an elo update against a
    # frozen anchor, and it can lapse. The one real difference is that closing
    # it consumes a document -- a pasted ballot -- instead of a button.

    def debate_session(self, date: dt.date) -> d.DebateSession | None:
        row = self.conn.execute(
            "SELECT * FROM debate_session WHERE date = ?", (_iso(date),)
        ).fetchone()
        return self._session(row) if row else None

    @staticmethod
    def _session(row: sqlite3.Row) -> d.DebateSession:
        data = _unpack(row, "readings", "scorecard")
        data["contra"] = bool(data["contra"])
        return d.DebateSession(**data)

    def open_debate(self, session: d.DebateSession) -> d.DebateSession:
        """Claim the day for a session, or hand back the one that already has it.

        `ON CONFLICT DO NOTHING` and a re-read, exactly as `record_draw` does:
        two tabs opening the dashboard at once both see no session and both
        insert, and the loser should get today's actual session rather than an
        IntegrityError. It is also what makes the endpoint idempotent, so a
        reload cannot reroll the angle or re-freeze a different reading list.
        """
        self.conn.execute(
            "INSERT INTO debate_session "
            "(date, issue, subarea, angle, motion, motion_source, motion_note, motion_url, "
            "format, side, anchor, contra, readings) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (date) DO NOTHING",
            (
                _iso(session.date),
                session.issue,
                session.subarea,
                session.angle,
                session.motion,
                session.motion_source,
                session.motion_note,
                str(session.motion_url) if session.motion_url else None,
                session.format,
                session.side.value,
                session.anchor,
                int(session.contra),
                _json([r.model_dump(mode="json") for r in session.readings]),
            ),
        )
        opened = self.debate_session(session.date)
        assert opened is not None  # just inserted, or already there
        return opened

    # -- the week's reading -------------------------------------------------

    def upsert_readings(self, items: list[d.DebateReading]) -> int:
        """Store one week's reading list, returning how many rows landed.

        `ON CONFLICT (week_start, kind, title)` rather than a delete-then-insert:
        re-running the 3am task must not clear the ticks off articles already
        read. Everything describing the item is refreshed; `read_on` is
        deliberately absent from the SET list, so it survives a regeneration.
        """
        for item in items:
            self.conn.execute(
                "INSERT INTO debate_reading "
                "(week_start, issue, subarea, kind, title, url, author, publisher, "
                "published, summary, lean, required) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (week_start, kind, title) DO UPDATE SET "
                "issue = excluded.issue, subarea = excluded.subarea, url = excluded.url, "
                "author = excluded.author, publisher = excluded.publisher, "
                "published = excluded.published, summary = excluded.summary, "
                "lean = excluded.lean, required = excluded.required",
                (
                    _iso(item.week_start),
                    item.issue,
                    item.subarea,
                    item.kind,
                    item.title,
                    str(item.url) if item.url else None,
                    item.author,
                    item.publisher,
                    _iso(item.published),
                    item.summary,
                    item.lean,
                    int(item.required),
                ),
            )
        return len(items)

    def readings(self, week_start: dt.date) -> list[d.DebateReading]:
        """The week's list: required articles first, then optional, then books.

        Ordered here rather than in the panel because the order is a claim
        about what the day is waiting on, and that claim belongs with the data.
        """
        rows = self.conn.execute(
            # `kind` ascending puts 'article' before 'book' alphabetically,
            # which is also the order of obligation -- articles are what the
            # day waits on and books are a standing offer.
            "SELECT * FROM debate_reading WHERE week_start = ? "
            "ORDER BY kind ASC, required DESC, id",
            (_iso(week_start),),
        ).fetchall()
        return [self._reading(r) for r in rows]

    @staticmethod
    def _reading(row: sqlite3.Row) -> d.DebateReading:
        data = dict(row)
        data["required"] = bool(data["required"])
        return d.DebateReading(**data)

    def mark_read(self, reading_id: int, on: dt.date | None) -> d.DebateReading:
        """Tick or untick one item. `on=None` unticks -- a box you cannot
        uncheck is one you hesitate to check."""
        row = self.conn.execute(
            "UPDATE debate_reading SET read_on = ? WHERE id = ? RETURNING *",
            (_iso(on), reading_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no reading {reading_id}")
        return self._reading(row)

    def readings_exist(self, issue: str, subarea: str) -> bool:
        """Has this sub-area ever had a reading list?

        Asked by the 3am task to decide whether to offer books. They are worth
        offering the first time a sub-area comes round and not the second: over
        a 26-week cycle the alternative is a hundred-odd recommendations, and
        the fifth list you have not read stops you reading the first.
        """
        return (
            self.conn.execute(
                "SELECT 1 FROM debate_reading WHERE issue = ? AND subarea = ? LIMIT 1",
                (issue, subarea),
            ).fetchone()
            is not None
        )

    def unread_required(self, week_start: dt.date) -> int:
        """Required articles still unticked -- what the reading day waits on."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM debate_reading "
            "WHERE week_start = ? AND required = 1 AND read_on IS NULL",
            (_iso(week_start),),
        ).fetchone()[0]

    def set_debate_position(
        self, date: dt.date, position: str, confidence: int | None, *, after: bool
    ) -> d.DebateSession:
        """Record what you believe, before or after arguing.

        Two columns rather than one row per statement. A position log with more
        than two points per session would be a different feature -- what is
        wanted is the pair, so that "did arguing it move me" is a subtraction.
        """
        column = "position_after" if after else "position"
        confidence_column = "confidence_after" if after else "confidence"
        row = self.conn.execute(
            f"UPDATE debate_session SET {column} = ?, {confidence_column} = ? "
            f"WHERE date = ? RETURNING *",
            (position, confidence, _iso(date)),
        ).fetchone()
        if row is None:
            raise LookupError(f"no debate session on {date}")
        return self._session(row)

    def brief_debate(self, date: dt.date) -> d.DebateSession:
        """Mark the prompt as taken. This is what starts the lapse clock.

        `WHERE status = 'prep'` rather than an unguarded update: re-copying the
        prompt on the second day of a two-day debate must not push `briefed_on`
        forward and quietly buy another grace period.
        """
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE debate_session SET status = 'briefed', briefed_on = ? "
                "WHERE date = ? AND status = 'prep' RETURNING *",
                (_iso(date), _iso(date)),
            ).fetchone()
            if row is not None:
                return self._session(row)
            existing = self.debate_session(date)
            if existing is None:
                raise LookupError(f"no debate session on {date}")
            return existing

    def score_debate(
        self, date: dt.date, card: d.Scorecard, score: float
    ) -> tuple[d.DebateSession, d.RatingEvent]:
        """Close the session on a pasted ballot and apply the elo. Atomic.

        `WHERE status IN ('prep','briefed')` is the double-submit guard, the
        same one `_close_math` uses: two tabs pasting the same ballot update
        zero rows on the second and raise, rather than rating one debate twice.

        `prep` is accepted as well as `briefed` because taking the prompt is a
        button you can forget to press, and refusing a real ballot on that
        ground would be the dashboard's bookkeeping outranking the debate that
        actually happened.
        """
        with transaction(self.conn):
            session = self.debate_session(date)
            if session is None:
                raise LookupError(f"no debate session on {date}")
            if session.status in (DebateStatus.scored, DebateStatus.lapsed):
                raise ValueError(f"the debate on {date} is already {session.status.value}")
            row = self.conn.execute(
                "UPDATE debate_session SET status = 'scored', scorecard = ?, score = ?, "
                "briefed_on = COALESCE(briefed_on, ?) "
                "WHERE date = ? AND status IN ('prep', 'briefed') RETURNING *",
                (_json(card), score, _iso(date), _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"the debate on {date} was closed concurrently")
            closed = self._session(row)
            event = self._rate_debate(closed, score, Provenance.measured, area=None)
            # The same result inside its issue, second and in the same
            # transaction -- `_close_math`'s rule, for its reason: the pooled
            # row is what the panel reads, so it must not be able to exist
            # without its partner.
            self._rate_debate(closed, score, Provenance.measured, area=closed.issue)
            return closed, event

    def _rate_debate(
        self,
        session: d.DebateSession,
        score: float,
        provenance: Provenance,
        *,
        area: str | None,
    ) -> d.RatingEvent:
        """One elo row against the session's frozen anchor. Caller holds the
        transaction; `area=None` is the pooled debate rating."""
        n = self.event_count(Track.debate, area)
        before = self.current_rating(Track.debate, area)
        k = scoring.k_factor(n)
        after = scoring.elo_update(before, session.anchor, score, n)
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, "
            "provenance, area) "
            "VALUES ('debate', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                _iso(session.date),
                # The sub-area and angle, not the motion: the motion is a frame
                # sharpened in the chat, so it is the same string every week,
                # and `ref` is meant to identify the round.
                f"{session.subarea} · {session.angle}",
                session.anchor,
                score,
                k,
                before,
                after,
                Provenance(provenance).value,
                area,
            ),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def unbriefed_debate_dates(self, before: dt.date) -> list[dt.date]:
        """Briefed sessions whose grace period is over and which never scored.

        `before` is today minus the grace, computed by the caller: this module
        does not know about config, and the grace is config. Strictly less than,
        so the boundary day is still yours -- the same courtesy
        `unmarked_math_dates` extends to today.

        Sessions still in `prep` are deliberately absent. Reading the articles
        and never taking the prompt promised nothing, so there is nothing to
        charge for; only carrying the prompt off does.
        """
        rows = self.conn.execute(
            "SELECT date FROM debate_session WHERE status = 'briefed' AND date < ? ORDER BY date",
            (_iso(before),),
        ).fetchall()
        return [dt.date.fromisoformat(r[0]) for r in rows]

    def lapse_debate(self, date: dt.date) -> tuple[d.DebateSession, d.RatingEvent]:
        """Auto-score one abandoned session as a loss against its own anchor."""
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE debate_session SET status = 'lapsed', score = ? "
                "WHERE date = ? AND status = 'briefed' RETURNING *",
                (scoring.DEBATE_LAPSE_SCORE, _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"the debate on {date} is not an unscored briefed session")
            session = self._session(row)
            event = self._rate_debate(
                session, scoring.DEBATE_LAPSE_SCORE, Provenance.lapsed, area=None
            )
            self._rate_debate(
                session, scoring.DEBATE_LAPSE_SCORE, Provenance.lapsed, area=session.issue
            )
            return session, event

    def reconcile_debate_lapses(self, today: dt.date, grace_days: int) -> list[d.RatingEvent]:
        """Close out every abandoned session past its grace. Idempotent, since
        a lapsed session is no longer `briefed`."""
        cutoff = today - dt.timedelta(days=max(0, grace_days))
        return [self.lapse_debate(date)[1] for date in self.unbriefed_debate_dates(cutoff)]

    def rating_delta(self, track: Track, on: dt.date) -> int | None:
        """Net rating change on one track on one day, or None if nothing moved.

        Summed rather than read off a single row, because a spelling round
        writes five events on one date and what happened that day is their net.
        Pooled rows only (`area IS NULL`), so a per-area chain cannot
        double-count into it.
        """
        row = self.conn.execute(
            "SELECT SUM(rating_after - rating_before) FROM rating_event "
            "WHERE track = ? AND date = ? AND area IS NULL AND rating_after IS NOT NULL",
            (Track(track).value, _iso(on)),
        ).fetchone()
        return row[0]

    def debate_delta(self, on: dt.date) -> int | None:
        """What that day's ballot did to the pooled debate rating, or None.

        Read back off the event rather than recomputed from the score, exactly
        as `theorem_delta` is: the row is what happened, and recomputing it here
        would be a second place the number could be decided.
        """
        row = self.conn.execute(
            "SELECT rating_after - rating_before FROM rating_event "
            "WHERE track = 'debate' AND date = ? AND area IS NULL ORDER BY id DESC LIMIT 1",
            (_iso(on),),
        ).fetchone()
        return row[0] if row else None

    def debate_history(self, issue: str | None = None, limit: int = 120) -> list[d.DebateSession]:
        """Past sessions, oldest first -- the order the dossier reads them in."""
        sql = "SELECT * FROM debate_session"
        params: tuple[object, ...] = ()
        if issue is not None:
            sql += " WHERE issue = ?"
            params = (issue,)
        sql += " ORDER BY date DESC LIMIT ?"
        rows = self.conn.execute(sql, (*params, limit)).fetchall()
        return [self._session(r) for r in reversed(rows)]

    # -- the dossier --------------------------------------------------------

    def add_fact(self, fact: d.DebateFact) -> d.DebateFact:
        row = self.conn.execute(
            "INSERT INTO debate_fact (issue, date, claim, source, stance) "
            "VALUES (?, ?, ?, ?, ?) RETURNING *",
            (fact.issue, _iso(fact.date), fact.claim, fact.source, fact.stance.value),
        ).fetchone()
        return d.DebateFact(**dict(row))

    def delete_fact(self, fact_id: int) -> d.DebateFact:
        """Remove one fact, returning it so the caller knows which dossier to
        re-render. A typo you cannot take back is a box you stop using."""
        row = self.conn.execute(
            "DELETE FROM debate_fact WHERE id = ? RETURNING *", (fact_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no fact {fact_id}")
        return d.DebateFact(**dict(row))

    def facts(self, issue: str | None = None, on: dt.date | None = None) -> list[d.DebateFact]:
        """Oldest first. Both filters optional and independent: the dossier
        wants an issue's whole history, the panel wants today's."""
        clauses, params = [], []
        if issue is not None:
            clauses.append("issue = ?")
            params.append(issue)
        if on is not None:
            clauses.append("date = ?")
            params.append(_iso(on))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM debate_fact{where} ORDER BY id", params).fetchall()
        return [d.DebateFact(**dict(r)) for r in rows]

    def fact_count(self, on: dt.date) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM debate_fact WHERE date = ?", (_iso(on),)
        ).fetchone()[0]

    # -- word ---------------------------------------------------------------

    def word(self, date: dt.date) -> d.WordOfDay | None:
        row = self.conn.execute(
            "SELECT * FROM word_of_day WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.WordOfDay(**_unpack(row, "sentences", "grade")) if row else None

    def upsert_word(self, word: d.WordOfDay) -> d.WordOfDay:
        row = self.conn.execute(
            "INSERT INTO word_of_day "
            "(date, word, pos, definition, pronunciation, respelling, sentences, grade) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET "
            "word = excluded.word, pos = excluded.pos, definition = excluded.definition, "
            "pronunciation = excluded.pronunciation, respelling = excluded.respelling, "
            "sentences = excluded.sentences, grade = excluded.grade RETURNING *",
            (
                _iso(word.date),
                word.word,
                word.pos,
                word.definition,
                word.pronunciation,
                word.respelling,
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
            "INSERT INTO theorem_card (name, statement, topic, added, archived, deck) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING *",
            (
                card.name,
                card.statement,
                card.topic,
                _iso(card.added),
                int(card.archived),
                Deck(card.deck).value,
            ),
        ).fetchone()
        return d.TheoremCard(**dict(row))

    def card(self, card_id: int) -> d.TheoremCard | None:
        row = self.conn.execute("SELECT * FROM theorem_card WHERE id = ?", (card_id,)).fetchone()
        return d.TheoremCard(**dict(row)) if row else None

    def card_named(self, name: str, deck: Deck = Deck.math) -> d.TheoremCard | None:
        """By name within a deck, case-insensitively.

        Used by seeding and by the add box: adding "tragedy of the commons"
        twice should find the card you already have rather than pay for a
        second one and put both in the rotation.
        """
        row = self.conn.execute(
            "SELECT * FROM theorem_card WHERE deck = ? AND name = ? COLLATE NOCASE",
            (Deck(deck).value, name),
        ).fetchone()
        return d.TheoremCard(**dict(row)) if row else None

    def cards(
        self, deck: Deck = Deck.math, *, include_archived: bool = False
    ) -> list[d.TheoremCard]:
        """One deck, oldest first. Archived cards are excluded by default --
        every caller that draws wants the active deck, and the one caller that
        renders the management list asks for the rest explicitly."""
        archived = "" if include_archived else " AND archived = 0"
        rows = self.conn.execute(
            f"SELECT * FROM theorem_card WHERE deck = ?{archived} ORDER BY id",
            (Deck(deck).value,),
        ).fetchall()
        return [d.TheoremCard(**dict(r)) for r in rows]

    def deck_size(self, deck: Deck = Deck.math) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM theorem_card WHERE deck = ? AND archived = 0",
            (Deck(deck).value,),
        ).fetchone()[0]

    def seed_cards(self, cards: list[d.TheoremCard]) -> int:
        """Insert any card whose name is not already in its deck. Returns how
        many landed.

        Matched on name rather than upserted on content, and that is the whole
        rule: editing a statement in config.toml must not rewrite a card you
        have already been graded on, because the archive would then show you
        recalling something you were never asked. Archive the card in the panel
        and it comes back with the new text.
        """
        added = 0
        for card in cards:
            if self.card_named(card.name, Deck(card.deck)) is None:
                self.add_card(card)
                added += 1
        return added

    def set_archived(self, card_id: int, archived: bool) -> d.TheoremCard:
        row = self.conn.execute(
            "UPDATE theorem_card SET archived = ? WHERE id = ? RETURNING *",
            (int(archived), card_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"no theorem card {card_id}")
        return d.TheoremCard(**dict(row))

    # -- the draw log -------------------------------------------------------

    def last_drawn(self, deck: Deck = Deck.math) -> dict[int, dt.date]:
        """`card_id -> the last date it appeared`, for cards that have.

        One aggregate for the whole deck rather than a query per card: the
        weights are computed together, and a per-card MAX() would make the
        draw's cost scale with the deck.
        """
        rows = self.conn.execute(
            "SELECT card_id, MAX(date) FROM theorem_draw WHERE deck = ? GROUP BY card_id",
            (Deck(deck).value,),
        ).fetchall()
        return {r[0]: dt.date.fromisoformat(r[1]) for r in rows}

    def draw(self, on: dt.date, deck: Deck = Deck.math) -> d.TheoremDraw | None:
        row = self.conn.execute(
            "SELECT * FROM theorem_draw WHERE date = ? AND deck = ?",
            (_iso(on), Deck(deck).value),
        ).fetchone()
        return d.TheoremDraw(**dict(row)) if row else None

    def record_draw(self, on: dt.date, card_id: int, deck: Deck = Deck.math) -> d.TheoremDraw:
        """Claim the day for a card, or return the card that already has it.

        `ON CONFLICT DO NOTHING` plus a re-read rather than a check-then-write:
        two tabs opening the dashboard at once would both see no draw and both
        insert, and the loser of that race should get today's actual card back,
        not an IntegrityError.

        The conflict target is `(date, deck)`, so the two decks claim the same
        morning independently -- which is the whole reason migration 12 widened
        the primary key.
        """
        self.conn.execute(
            "INSERT INTO theorem_draw (date, deck, card_id) VALUES (?, ?, ?) "
            "ON CONFLICT (date, deck) DO NOTHING",
            (_iso(on), Deck(deck).value, card_id),
        )
        drawn = self.draw(on, deck)
        assert drawn is not None  # just inserted, or already there
        return drawn

    def record_recall(
        self,
        on: dt.date,
        *,
        explanation: str,
        application: str,
        grade: d.TheoremGrade,
        area: str | None,
        deck: Deck = Deck.math,
    ) -> tuple[d.TheoremDraw, d.RatingEvent]:
        """Store a written recall and rate it, in one transaction.

        `WHERE ... AND explained IS NULL` is the double-submit guard, and it is
        the guard rather than a check-then-write: two tabs posting the same
        recall would otherwise each write a rating_event and the day would move
        the rating twice. Zero rows matched means somebody got there first.

        Rated against an opponent equal to the current rating, which is what
        makes the delta +/-3 -- see `scoring.THEOREM_K`.

        One row, against the area, where a math result writes two (pooled and
        area, "two readings of one event"). Deliberately not two here. Because
        the opponent is your own rating there is no equilibrium to pull the
        number back -- a run of good recalls walks it up +3 a day and nothing
        argues -- and a pooled row would apply that walk *every single day*, to
        the one figure that is supposed to mean "how well do you solve
        competition problems". Against an area it moves only on the days that
        area's theorem comes round, which is roughly weekly, and it moves the
        number that is actually asking the related question: how well do you
        know this topic.

        An argument-deck recall lands on the debate track and on
        `ARGUMENT_AREA`, which is deliberately not one of the configured issues
        -- see that constant. Everything else about the write is identical,
        which is the whole point of the shared table.
        """
        track = Track.debate if Deck(deck) is Deck.argument else Track.math
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE theorem_draw SET recalled = ?, explanation = ?, application = ?, "
                "explained = ?, applied = ?, note = ? "
                "WHERE date = ? AND deck = ? AND explained IS NULL RETURNING *",
                (
                    int(grade.explained),
                    explanation,
                    application,
                    int(grade.explained),
                    int(grade.applied),
                    grade.note,
                    _iso(on),
                    Deck(deck).value,
                ),
            ).fetchone()
            if row is None:
                if self.draw(on, deck) is None:
                    raise LookupError(f"no card drawn on {_iso(on)}")
                raise ValueError(f"the card drawn on {_iso(on)} was already graded")

            draw = d.TheoremDraw(**dict(row))
            card = self.card(draw.card_id)
            assert card is not None  # a draw references a card by foreign key

            score = scoring.theorem_score(grade.explained, grade.applied)
            before = self.current_rating(track, area)
            after = scoring.elo_update_with_k(before, before, score, scoring.THEOREM_K)
            event = self.conn.execute(
                "INSERT INTO rating_event "
                "(track, date, ref, opponent, score, k, rating_before, rating_after, "
                "provenance, area) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
                (
                    Track(track).value,
                    _iso(on),
                    card.name,
                    before,
                    score,
                    scoring.THEOREM_K,
                    before,
                    after,
                    Provenance.theorem.value,
                    area,
                ),
            ).fetchone()
            return draw, d.RatingEvent(**dict(event))

    def theorem_delta(self, on: dt.date, deck: Deck = Deck.math) -> int | None:
        """What that day's recall did to the rating, or None if it did not.

        Read back off the event rather than recomputed from the grade: the row
        is what happened, and a second calculation of it here would be a second
        place for the +/-3 to be decided.
        """
        track = Track.debate if Deck(deck) is Deck.argument else Track.math
        row = self.conn.execute(
            "SELECT rating_after - rating_before FROM rating_event "
            "WHERE track = ? AND date = ? AND provenance = ? "
            "ORDER BY id DESC LIMIT 1",
            (Track(track).value, _iso(on), Provenance.theorem.value),
        ).fetchone()
        return row[0] if row else None

    def recall_total(self, deck: Deck = Deck.math) -> int:
        """Graded recalls in one deck, ever.

        Deliberately not `event_count`, which is the K-factor's `n` and
        excludes `theorem` rows on purpose -- a recall is not evidence about
        problem solving. But the pattern rating is moved by *nothing else*, so
        "how much is behind this number" has to be counted from the draws.
        """
        return self.conn.execute(
            "SELECT COUNT(*) FROM theorem_draw WHERE deck = ? AND explained IS NOT NULL",
            (Deck(deck).value,),
        ).fetchone()[0]

    def recall_counts(self, deck: Deck = Deck.math) -> dict[int, tuple[int, int]]:
        """`card_id -> (times seen, times recalled)` across all history.

        An unanswered draw counts as seen and not as recalled, which is what
        `SUM(recalled)` over NULLs gives for free.
        """
        rows = self.conn.execute(
            "SELECT card_id, COUNT(*), COALESCE(SUM(recalled), 0) "
            "FROM theorem_draw WHERE deck = ? GROUP BY card_id",
            (Deck(deck).value,),
        ).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    # -- papers -------------------------------------------------------------

    # -- seen links ---------------------------------------------------------

    def seen_links(self, since: dt.date, before: dt.date | None = None) -> set[str]:
        """Normalised links last shown in `[since, before)`.

        `before` exists so today's own picks can be excluded from today's
        filter. Without it the panel is unreadable: showing a headline records
        it, so the next request filters out what the previous one just
        displayed and a refresh silently swaps every story. Suppression is a
        between-days rule, not a within-day one.

        Returned whole rather than queried per candidate: a fetch checks a
        couple of hundred items against a table growing by a handful of rows a
        day, so one query and a set beats N indexed lookups by enough that the
        difference is not worth thinking about again.
        """
        if before is None:
            return {
                r[0]
                for r in self.conn.execute(
                    "SELECT link FROM seen_link WHERE last_seen >= ?", (_iso(since),)
                )
            }
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT link FROM seen_link WHERE last_seen >= ? AND last_seen < ?",
                (_iso(since), _iso(before)),
            )
        }

    def record_seen(self, links: list[str], on: dt.date) -> None:
        """Mark links as shown today. `first_seen` is never moved forward --
        it is the answer to "how long have I been ignoring this"."""
        self.conn.executemany(
            "INSERT INTO seen_link (link, first_seen, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT (link) DO UPDATE SET last_seen = excluded.last_seen",
            [(link, _iso(on), _iso(on)) for link in links],
        )

    def prune_seen(self, before: dt.date) -> int:
        """Drop links outside the window. Bounded storage is the point: this
        table would otherwise grow forever to answer a 30-day question."""
        cur = self.conn.execute("DELETE FROM seen_link WHERE last_seen < ?", (_iso(before),))
        return cur.rowcount

    # -- papers -------------------------------------------------------------

    def paper_links(self) -> set[str]:
        """Every arXiv link ever surfaced, queued or triaged."""
        return {r[0] for r in self.conn.execute("SELECT link FROM paper")}

    def paper_by_link(self, link: str) -> d.Paper | None:
        row = self.conn.execute("SELECT * FROM paper WHERE link = ?", (link,)).fetchone()
        return d.Paper(**dict(row)) if row else None

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
