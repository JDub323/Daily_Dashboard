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
import random
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .. import scoring
from ..models import domain as d
from ..models.common import (
    BathroomKind,
    ChessOutcome,
    ChessStatus,
    Deck,
    DrinkName,
    DrinkTag,
    EntrySource,
    Game,
    HealthMetric,
    Low,
    MathOutcome,
    MathStatus,
    MealFlag,
    MealSize,
    PaperStatus,
    ProblemKind,
    Provenance,
    SessionKind,
    Stool,
    Symptom,
    Tired,
    Track,
    UrineColor,
    Volume,
    canonical_feel,
    canonical_flags,
    canonical_tags,
)
from .connection import transaction

SEEDS = {
    Track.math: scoring.MATH_SEED,
    Track.chess: scoring.CHESS_SEED,
    Track.spelling: scoring.SPELLING_SEED,
    Track.investigate: scoring.INVESTIGATE_SEED,
}

ARGUMENT_AREA = "argument-patterns"
"""The bucket an argument-pattern recall rates into, on the investigate track.

Deliberately not one of the subject folders, and that is the whole design. A
pattern like principal-agent is not *about* immigration or Taiwan -- it applies
everywhere -- so filing it under whichever folder the day's topic happened to
land in would be an arbitrary attribution that then shows up in the per-folder
breakdown as evidence about that subject.

Being outside the folder list also keeps it out of the panel's rating strip,
which reads `investigation_folders()`. It is read in one place, the argument
deck's own panel, which is where "do I actually know my patterns" is the
question being asked.
"""

TRICK_AREA = "tricks"
"""The bucket a trick-of-the-day result rates into, on the math track.

`ARGUMENT_AREA`'s move, made for the same two reasons and one more. It is not
one of the `[math.areas]` buckets, so it stays out of the per-area strip and
the retrospective chart, both of which iterate `cfg.math.area_names`. And
because the pooled rating is `area IS NULL`, writing only an area row leaves
the headline math rating untouched -- which is the important one: this fires
every day, and a daily drill on an identity is not evidence about solving
competition problems under time pressure. Confidence in that number has to be
bought with problems.
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

PROOF_AREA = "proofs"
"""The bucket a proof result rates into, on the math track.

`TRICK_AREA`'s move, for the third time and the sharpest reason yet. A proof is
scored on a three-way self-report against a rubric, and honestly scored proofs
sit well below honestly scored numeric answers -- so letting them into the
`[math.areas]` buckets would drag the very numbers that pitch Monday's and
Tuesday's difficulty. Four hard days a week would make the two calibrated days
easier, which is the system undoing the change it was given.

Not one of the `[math.areas]` names, so it stays out of the per-area strip that
iterates `cfg.math.area_names`, and -- because the pooled rating is
`area IS NULL` -- writing only this row leaves the headline math rating alone.

The cost of that is worth stating: the proofs rating and the compute rating are
not comparable numbers, and the headline barely moves Wednesday to Saturday.
"""

_ATTEMPT_ONLY = frozenset(
    {"a_date", "problem_id", "day", "hints_revealed", "status", "score", "minutes", "rubric_hit"}
)
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


def _todo(row: sqlite3.Row) -> d.TodoItem:
    """A `todo` row -> the model. The two clock columns are the only rename.

    They are `start_time`/`end_time` in SQLite because `end` is a keyword there
    and a quoted column name is a trap laid for whoever writes the next query.
    """
    data = dict(row)
    data["start"] = data.pop("start_time")
    data["end"] = data.pop("end_time")
    data.pop("created_at", None)  # written for forensics, not for the panel
    return d.TodoItem(**data)


def _unpack(row: sqlite3.Row, *json_fields: str) -> dict[str, Any]:
    out = dict(row)
    for f in json_fields:
        if out.get(f) is not None:
            out[f] = json.loads(out[f])
    return out


def _problem(row: sqlite3.Row) -> d.MathProblem:
    """One `math_problem` row, with both of its JSON columns read.

    A named helper rather than the `_unpack(row, "hints")` that used to be
    written out at each of the three call sites: `rubric` joined `hints` as a
    JSON column, and three places each remembering to list both is three places
    that can forget one. Forgetting would not raise -- pydantic would see a
    string where a list belongs and reject it a layer later, naming the field
    but not the reason.
    """
    data = _unpack(row, "hints", "rubric")
    # NULL rather than '[]' on every problem written before the column existed.
    data["rubric"] = data.get("rubric") or []
    return d.MathProblem(**data)


def _attempt(row: sqlite3.Row) -> d.MathAttempt:
    """One `math_attempt` row. `rubric_hit` is NULL on every compute day."""
    data = _unpack(row, "rubric_hit")
    data["rubric_hit"] = data.get("rubric_hit") or []
    return d.MathAttempt(**data)


def _plan(row: sqlite3.Row) -> d.StudyPlan:
    """One `study_plan` row: three JSON columns, all of them lists."""
    return d.StudyPlan(**_unpack(row, "segments", "done", "skipped"))


_STATUS_FOR = {
    MathOutcome.solved: MathStatus.solved,
    MathOutcome.partial: MathStatus.partial,
    MathOutcome.failed: MathStatus.failed,
}
"""What the client reports -> what the row records.

`MathOutcome.carried` is absent on purpose: ending day one is not a result, so
it goes through `carry_math` and writes no rating event at all. Leaving it out
of this table is what makes `finish_math` unable to be handed it by accident.
"""


def _rating_areas(problem: d.MathProblem) -> list[str | None]:
    """Which rating buckets one result writes a row into, in order.

    A compute problem writes two: the pooled rating (`None`) and its area. A
    proof writes one, into `PROOF_AREA`, and specifically *not* the pooled row
    -- see that constant for why letting proof scores near the numbers that
    pitch Monday's difficulty would have the system undo the change.

    One function rather than the condition written out at each closing path,
    because there are three of them -- the ordinary finish, the lapse, and the
    late submission -- and a rule about which ratings move that is stated three
    times is a rule that will eventually be stated two ways.
    """
    if problem.kind is ProblemKind.proof:
        return [PROOF_AREA]
    return [None] if problem.area is None else [None, problem.area]


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

        `track` because the investigation folders are the same idea in a second
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
            "(source, tier, anchor, statement, kind, answer, hints, rubric, location, "
            "verified_url, model, generated_at, topic, area) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                problem.source,
                problem.tier,
                problem.anchor,
                problem.statement,
                problem.kind.value,
                problem.answer,
                _json(problem.hints),
                _json(problem.rubric),
                problem.location,
                str(problem.verified_url) if problem.verified_url else None,
                problem.model,
                problem.generated_at.isoformat(),
                problem.topic,
                problem.area,
            ),
        ).fetchone()
        return _problem(row)

    def problem(self, problem_id: int) -> d.MathProblem | None:
        row = self.conn.execute("SELECT * FROM math_problem WHERE id = ?", (problem_id,)).fetchone()
        return _problem(row) if row else None

    def problem_by_source(self, source: str) -> d.MathProblem | None:
        row = self.conn.execute("SELECT * FROM math_problem WHERE source = ?", (source,)).fetchone()
        return _problem(row) if row else None

    def math_attempt(self, date: dt.date) -> d.MathAttempt | None:
        row = self.conn.execute(
            "SELECT * FROM math_attempt WHERE date = ?", (_iso(date),)
        ).fetchone()
        return _attempt(row) if row else None

    def carried_math_attempt(self, date: dt.date) -> d.MathAttempt | None:
        """Yesterday's two-day problem, if it was left open at the end of it.

        The previous day only, and deliberately not "the most recent carried
        attempt". A problem carried from Thursday is Friday's; if Friday is
        missed as well, Saturday is Saturday -- it has its own job, and picking
        up a two-day problem three days later would quietly make the day-two
        hint rules apply to a day that is not day two.
        """
        row = self.conn.execute(
            "SELECT * FROM math_attempt WHERE date = ? AND status = 'carried'",
            (_iso(date - dt.timedelta(days=1)),),
        ).fetchone()
        return _attempt(row) if row else None

    def open_math_attempt(self, date: dt.date, problem_id: int, day: int = 1) -> d.MathAttempt:
        row = self.conn.execute(
            "INSERT INTO math_attempt (date, problem_id, day) VALUES (?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET problem_id = excluded.problem_id, "
            "day = excluded.day RETURNING *",
            (_iso(date), problem_id, day),
        ).fetchone()
        return _attempt(row)

    def reveal_hint(self, date: dt.date, max_hints: int = 3) -> d.MathAttempt:
        """Reveal one more hint, up to `max_hints`.

        The cap is passed in rather than being the column's hard 3, because how
        many hints a day offers is a question about the day: three on a compute
        problem, two on a proof, and *none* on day one of the two-day problem,
        where the whole point is that you sit with it overnight before being
        told anything.

        Still clamps rather than raising, which is what `MIN(..., 3)` did. The
        cap moved; the contract did not. A request past it is a double-click on
        a button the panel has already disabled, not a claim worth a 409 -- and
        with the cap at zero on a Thursday, clamping is what makes "there are no
        hints today" a quiet fact rather than an error the client has to know
        how to render.
        """
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE math_attempt SET hints_revealed = MIN(hints_revealed + 1, ?) "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (max(0, max_hints), _iso(date)),
            ).fetchone()
            if row is None:
                raise LookupError(f"no open math attempt on {date}")
            return _attempt(row)

    def carry_math(self, date: dt.date) -> d.MathAttempt:
        """End day one of a two-day problem without a result.

        No rating event, and none is missing: the result lands once, on day
        two. `day` stays 1 -- this row *is* day one, and tomorrow's row is the
        one that says 2.
        """
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE math_attempt SET status = 'carried' "
                "WHERE date = ? AND status = 'open' AND day = 1 RETURNING *",
                (_iso(date),),
            ).fetchone()
            if row is None:
                raise ValueError(f"no open day-one math attempt on {date}")
            return _attempt(row)

    def _close_math(
        self,
        date: dt.date,
        *,
        status: MathStatus,
        provenance: Provenance,
        score_for: Callable[[d.MathAttempt], float],
        minutes: int | None = None,
        multiplier_for: Callable[[d.MathProblem], float] | None = None,
        rubric_hit: list[bool] | None = None,
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
                "UPDATE math_attempt SET status = ?, score = ?, minutes = ?, rubric_hit = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (
                    status.value,
                    score,
                    minutes,
                    _json(rubric_hit) if rubric_hit else None,
                    _iso(date),
                ),
            ).fetchone()
            if row is None:
                raise ValueError(f"math attempt on {date} was closed concurrently")

            # One row per bucket the result belongs in, first one returned.
            # The order matters and is `_rating_areas`': for a compute problem
            # the pooled row comes first because it is what the panel and the
            # chart read, and it must not be able to exist without its area
            # partner or vice versa -- which is what putting both inside this
            # transaction buys.
            events = [
                self._rate_math(date, problem, score, provenance, area=area, multiplier=multiplier)
                for area in _rating_areas(problem)
            ]
            return _attempt(row), events[0]

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
        *,
        rubric_hit: list[bool] | None = None,
        early_solve: bool = False,
    ) -> tuple[d.MathAttempt, d.RatingEvent]:
        """Score, close the attempt, and apply the elo update together.

        `par_minutes` is the tier->reference-time table, passed in as a plain
        dict rather than reached for through `Settings`: this module's rule is
        that it is the only one that knows SQL, and the price of that is that
        it is not allowed to know about config either.

        `early_solve` arrives the same way and for the same reason. Whether
        today is day one of a two-day problem is a question about `[math.week]`,
        which this module may not read -- so the caller decides, and what is
        checked *here* is the part the caller cannot be trusted on: the bonus
        applies only to a genuine day-one, unhinted solve, and the row is what
        says so.

        A failure discards `minutes` before it is stored. Time is only read on
        a solve, so keeping the number would leave a column on the row that
        nothing consults and that reads, later, as though it once mattered.
        """
        outcome = MathOutcome(outcome)
        if outcome is MathOutcome.carried:
            raise ValueError("a carried day is closed by `carry_math`, not by finishing it")
        gained = outcome is not MathOutcome.failed
        pars = par_minutes or {}

        def score_for(attempt: d.MathAttempt) -> float:
            problem = self.problem(attempt.problem_id)
            assert problem is not None
            if problem.kind is ProblemKind.proof:
                return scoring.proof_score(outcome, attempt.hints_revealed)
            if outcome is MathOutcome.partial:
                # A numeric answer that is half right is wrong. Rejected here
                # rather than quietly scored, because the client that sent it
                # has the wrong idea about what kind of day this is.
                raise ValueError("`partial` is a proof outcome; this problem has an answer")
            return scoring.math_score(outcome, attempt.hints_revealed)

        def multiplier_for(problem: d.MathProblem) -> float:
            if not gained:
                return 1.0
            if early_solve:
                attempt = self.math_attempt(date)
                assert attempt is not None
                if attempt.day == 1 and attempt.hints_revealed == 0:
                    return scoring.early_solve_multiplier(self.event_count(Track.math, PROOF_AREA))
                return 1.0
            return scoring.time_multiplier(minutes, pars.get(problem.tier))

        return self._close_math(
            date,
            status=_STATUS_FOR[outcome],
            provenance=Provenance.measured,
            score_for=score_for,
            minutes=minutes if gained else None,
            multiplier_for=multiplier_for,
            rubric_hit=rubric_hit,
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

            # Cancel then re-rate, bucket by bucket, in `_rating_areas`' order
            # -- for a compute problem the pooled row is what the panel and the
            # chart read, so it must not be able to exist without its partner
            # or vice versa. A proof has one bucket and cancels one row.
            events = []
            for area in _rating_areas(problem):
                self._unlapse(target, problem, area=area)
                events.append(
                    self._rate_math(
                        target, problem, score, Provenance.late, area=area, multiplier=multiplier
                    )
                )
            return _attempt(row), events[0]

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
            "SELECT a.date AS a_date, a.problem_id, a.day, a.hints_revealed, a.status, a.score, "
            "a.minutes, a.rubric_hit, p.* "
            "FROM math_attempt a JOIN math_problem p ON p.id = a.problem_id "
            "ORDER BY a.date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = _unpack(row, "hints", "rubric", "rubric_hit")
            attempt = d.MathAttempt(
                date=data["a_date"],
                problem_id=data["problem_id"],
                day=data["day"],
                hints_revealed=data["hints_revealed"],
                status=data["status"],
                score=data["score"],
                minutes=data["minutes"],
                rubric_hit=data["rubric_hit"] or [],
            )
            fields = {k: v for k, v in data.items() if k not in _ATTEMPT_ONLY}
            problem = d.MathProblem(**{**fields, "rubric": fields.get("rubric") or []})
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

        `track` because the investigation folders are the same idea in a second
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

    def reconcile_math_lapses(
        self, today: dt.date, should_carry: Callable[[dt.date], bool] | None = None
    ) -> list[d.RatingEvent]:
        """Close out every unmarked past day. Returns the events applied.

        Idempotent by construction: a lapsed attempt is no longer `open`, so
        the second call finds nothing. Each day is its own transaction and
        chains off the rating the previous one produced, so a three-day gap
        costs three successive results rather than one compounded guess.

        `should_carry` names the days that are day one of a two-day problem, and
        those are carried instead of lapsed -- no rating event, which is why
        such a day contributes nothing to the returned list. Forgetting to press
        "keep going tomorrow" should not cost a full result on a problem you
        were meant to still be holding, and the lapse rule cannot tell that day
        apart from an abandoned one without being told.

        A predicate rather than a set of dates because the answer comes from
        `[math.week]`, which this module may not read. Defaulting to None keeps
        the old behaviour exactly: every unmarked day lapses.
        """
        events = []
        for date in self.unmarked_math_dates(today):
            attempt = self.math_attempt(date)
            carries = should_carry is not None and attempt is not None and attempt.day == 1
            if carries and should_carry(date):
                self.carry_math(date)
                continue
            events.append(self.lapse_math(date)[1])
        return events

    # -- sunday's reading plan ----------------------------------------------

    def study_plan(self, date: dt.date) -> d.StudyPlan | None:
        row = self.conn.execute(
            "SELECT * FROM study_plan WHERE date = ?", (_iso(date),)
        ).fetchone()
        return _plan(row) if row else None

    def add_study_plan(self, plan: d.StudyPlan) -> d.StudyPlan:
        """Store one Sunday's plan. Replaces the day's plan if one exists.

        Replace rather than reject, unlike `math_problem.source`. A regenerated
        reading plan is a legitimate thing to want -- the week's topic did not
        change and neither did the book, so there is nothing here that a second
        generation could invalidate, and nothing is rated off it.
        """
        row = self.conn.execute(
            "INSERT INTO study_plan (date, topic, book, segments, model, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET topic = excluded.topic, book = excluded.book, "
            "segments = excluded.segments, model = excluded.model, "
            "generated_at = excluded.generated_at RETURNING *",
            (
                _iso(plan.date),
                plan.topic,
                plan.book,
                _json([s.model_dump() for s in plan.segments]),
                plan.model,
                plan.generated_at.isoformat(),
            ),
        ).fetchone()
        return _plan(row)

    def mark_segment(
        self, date: dt.date, index: int, *, done: bool, reason: str = ""
    ) -> d.StudyPlan:
        """Tick a segment, or record that it was skipped and why.

        The two lists are kept disjoint: marking a segment done removes it from
        `skipped` and vice versa, so a segment you skipped in the morning and
        came back to in the evening reads as done rather than as both.

        The reason is the half of this worth storing. Three segments are
        generated and one or two are expected, so a skip is the normal case and
        carries no information on its own -- "knew it" and "no time" are what
        the next generation needs to hear apart.
        """
        with transaction(self.conn):
            plan = self.study_plan(date)
            if plan is None:
                raise LookupError(f"no study plan on {date}")
            if not 0 <= index < len(plan.segments):
                raise ValueError(f"segment {index} is not in {date}'s plan")

            done_at = sorted({*plan.done, index} if done else set(plan.done) - {index})
            skipped = [s for s in plan.skipped if s.index != index]
            if not done:
                skipped.append(d.SkippedSegment(index=index, reason=reason))
            skipped.sort(key=lambda s: s.index)

            row = self.conn.execute(
                "UPDATE study_plan SET done = ?, skipped = ? WHERE date = ? RETURNING *",
                (
                    _json(done_at),
                    _json([s.model_dump() for s in skipped]),
                    _iso(date),
                ),
            ).fetchone()
            return _plan(row)

    def recent_study_plans(self, limit: int = 8) -> list[d.StudyPlan]:
        """The last few Sundays, newest first -- what the generator is shown.

        The feedback loop the day needs. Without it every month opens with the
        same introduction, because nothing tells the generator that the last
        three plans all led with a definition you already had.
        """
        rows = self.conn.execute(
            "SELECT * FROM study_plan ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_plan(r) for r in rows]

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
        is what makes the ladder mean anything: missing the `impossible` word costs
        little and getting it right pays, and the `easy` word is the other
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

    # `spelling_trouble` was here: `(word, missed, asked)` worst first, for a
    # "keeps catching you" line under the round. It went with the line. The
    # miss counts it read are still kept and still matter -- they are what
    # decides which word gets the day's one relearn slot -- they are just not
    # something the panel says out loud any more.

    # -- tricks -------------------------------------------------------------

    def add_trick(self, trick: d.Trick) -> d.Trick:
        """Insert one trick. Raises `sqlite3.IntegrityError` on a repeat --
        `name` is UNIQUE, case-insensitively, and that index *is* the
        no-repeats rule rather than a check somewhere above this."""
        row = self.conn.execute(
            "INSERT INTO trick (name, statement, idea, topic, kind, added) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING *",
            (trick.name, trick.statement, trick.idea, trick.topic, trick.kind, _iso(trick.added)),
        ).fetchone()
        return d.Trick(**dict(row))

    def trick(self, trick_id: int) -> d.Trick | None:
        row = self.conn.execute("SELECT * FROM trick WHERE id = ?", (trick_id,)).fetchone()
        return d.Trick(**dict(row)) if row else None

    def trick_named(self, name: str) -> d.Trick | None:
        row = self.conn.execute(
            "SELECT * FROM trick WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return d.Trick(**dict(row)) if row else None

    def tricks(self) -> list[d.Trick]:
        """Every trick ever taught, oldest first. What the callback draws from."""
        rows = self.conn.execute("SELECT * FROM trick ORDER BY id").fetchall()
        return [d.Trick(**dict(r)) for r in rows]

    def trick_names(self) -> list[str]:
        """The do-not-repeat list, oldest first. The counterpart of
        `seen_sources` for math and `recent_words` for the word of the day."""
        return [r[0] for r in self.conn.execute("SELECT name FROM trick ORDER BY id")]

    def callback_dates(self) -> dict[int, dt.date]:
        """`trick_id -> the last day it was called back`, for those that have.

        One aggregate for the whole list rather than a query per trick, exactly
        as `last_drawn` is: the weights are computed together and a per-row
        MAX() would make the draw's cost scale with how long you have been
        doing this.
        """
        rows = self.conn.execute(
            "SELECT callback_id, MAX(date) FROM trick_round "
            "WHERE callback_id IS NOT NULL GROUP BY callback_id"
        ).fetchall()
        return {r[0]: dt.date.fromisoformat(r[1]) for r in rows}

    def trick_round(self, date: dt.date) -> d.TrickRound | None:
        row = self.conn.execute(
            "SELECT * FROM trick_round WHERE date = ?", (_iso(date),)
        ).fetchone()
        return self._round(row) if row else None

    @staticmethod
    def _round(row: sqlite3.Row) -> d.TrickRound:
        """`t_`-prefixed columns are dropped: `trick_rounds` joins the trick in
        under aliases, and `TrickRound` forbids extras. Prefixing rather than
        listing the round's own columns keeps the two from having to be edited
        together every time the table gains one."""
        data = {k: v for k, v in _unpack(row, "problems", "answers").items()
                if not k.startswith("t_")}
        if data.get("challenge_ok") is not None:
            data["challenge_ok"] = bool(data["challenge_ok"])
        return d.TrickRound(**data)

    def open_trick_round(
        self,
        date: dt.date,
        trick_id: int,
        problems: list[d.TrickProblem],
        callback_id: int | None = None,
    ) -> d.TrickRound:
        """Claim the day for a set, or hand back the one it already has.

        `ON CONFLICT DO NOTHING` and a re-read, the shape `record_draw` and
        `open_spelling_round` both use. It matters more here than in either:
        the loser of a two-tab race would otherwise get an IntegrityError
        having *already paid* for the generation behind it.
        """
        self.conn.execute(
            "INSERT INTO trick_round (date, trick_id, callback_id, problems) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (date) DO NOTHING",
            (
                _iso(date),
                trick_id,
                callback_id,
                _json([p.model_dump(mode="json") for p in problems]),
            ),
        )
        opened = self.trick_round(date)
        assert opened is not None  # just inserted, or already there
        return opened

    def finish_trick(
        self, date: dt.date, answers: list[str], marks: list[bool], opponent: int
    ) -> tuple[d.TrickRound, d.RatingEvent]:
        """Close the marked set and rate it, atomically.

        One event for the whole set rather than one per problem, which is the
        opposite of what `finish_spelling` does, and the difference is what is
        being measured. Five spelling words are five independent trials of the
        same skill against five different anchors. Eight problems on one trick
        are eight readings of a single question -- do you have this trick --
        and rating them separately would move the number eight times a day on
        one morning's evidence, taking K to its floor inside a fortnight.

        `opponent` is the anchor, computed by the caller from the bands the set
        actually contained. Passed in rather than read here for this module's
        standing reason: it is the only one that knows SQL, and the price is
        that it is not allowed to know about config either.

        `WHERE status = 'open'` is the double-submit guard, as everywhere else.
        """
        with transaction(self.conn):
            existing = self.trick_round(date)
            if existing is None:
                raise LookupError(f"no trick round on {date}")
            if existing.status != "open":
                raise ValueError(f"the trick round on {date} is already done")

            row = self.conn.execute(
                "UPDATE trick_round SET status = 'done', answers = ?, correct = ? "
                "WHERE date = ? AND status = 'open' RETURNING *",
                (_json(answers), sum(marks), _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"the trick round on {date} was closed concurrently")

            trick = self.trick(existing.trick_id)
            assert trick is not None  # the round references it by foreign key
            score = round(sum(marks) / len(marks), 4) if marks else 0.0
            return self._round(row), self._rate_trick(date, trick.name, opponent, score)

    def record_challenge(
        self, date: dt.date, answer: str, grade: d.TrickGrade, opponent: int
    ) -> tuple[d.TrickRound, d.RatingEvent]:
        """Store the optional problem's answer and rate it. Atomic.

        Its own event against its own anchor, separate from the set above.
        Folding it in would mean either that skipping it capped the day below
        1.0 -- which is not what optional means -- or that attempting it could
        only help, which is not what a challenge means.

        `WHERE challenge_ok IS NULL` is the double-submit guard, and it is the
        guard rather than a check-then-write: two tabs posting the same answer
        would otherwise each write a rating event.
        """
        with transaction(self.conn):
            row = self.conn.execute(
                "UPDATE trick_round SET challenge = ?, challenge_ok = ?, challenge_note = ? "
                "WHERE date = ? AND challenge_ok IS NULL RETURNING *",
                (answer, int(grade.correct), grade.note, _iso(date)),
            ).fetchone()
            if row is None:
                if self.trick_round(date) is None:
                    raise LookupError(f"no trick round on {date}")
                raise ValueError(f"the challenge on {date} was already graded")

            closed = self._round(row)
            trick = self.trick(closed.trick_id)
            assert trick is not None
            event = self._rate_trick(
                date, f"{trick.name} · challenge", opponent, 1.0 if grade.correct else 0.0
            )
            return closed, event

    def _rate_trick(
        self, date: dt.date, ref: str, opponent: int, score: float
    ) -> d.RatingEvent:
        """One elo row in the `tricks` bucket. Caller holds the transaction.

        One row, against the area, where a solved math problem writes two
        (pooled and area). Deliberately not two, and for `record_recall`'s
        reason applied to a busier feature: this fires every single day, and
        letting it into the pooled math rating would mean the number that says
        "how well do you solve competition problems" was mostly measuring
        whether you had done your drills. `TRICK_AREA` is not one of the
        configured areas, so nothing that iterates those picks it up either.
        """
        n = self.event_count(Track.math, TRICK_AREA)
        before = self.current_rating(Track.math, TRICK_AREA)
        k = scoring.k_factor(n)
        after = scoring.elo_update(before, opponent, score, n)
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, "
            "provenance, area) "
            "VALUES ('math', ?, ?, ?, ?, ?, ?, ?, 'measured', ?) RETURNING *",
            (_iso(date), ref, opponent, score, k, before, after, TRICK_AREA),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def trick_delta(self, on: dt.date) -> int | None:
        """Net change to the trick rating on one day, or None if it did not move.

        Summed rather than read off one row: a day where the challenge was also
        answered carries two events, and what happened that day is their net.
        """
        row = self.conn.execute(
            "SELECT SUM(rating_after - rating_before) FROM rating_event "
            "WHERE track = 'math' AND area = ? AND date = ?",
            (TRICK_AREA, _iso(on)),
        ).fetchone()
        return row[0]

    def trick_rounds(self, limit: int = 60) -> list[tuple[d.TrickRound, d.Trick]]:
        """Past rounds with their tricks, newest first -- what the archive reads.

        Joined here rather than looped in the caller, on `math_history`'s
        reasoning: N+1 selects over sixty days is the shape that turns a modal
        into a spinner.
        """
        rows = self.conn.execute(
            "SELECT r.*, t.name AS t_name, t.statement AS t_statement, t.idea AS t_idea, "
            "t.topic AS t_topic, t.kind AS t_kind, t.added AS t_added "
            "FROM trick_round r JOIN trick t ON t.id = r.trick_id "
            "ORDER BY r.date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            trick = d.Trick(
                id=data["trick_id"],
                name=data["t_name"],
                statement=data["t_statement"],
                idea=data["t_idea"],
                topic=data["t_topic"],
                kind=data["t_kind"],
                added=data["t_added"],
            )
            out.append((self._round(row), trick))
        return out

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

    # -- investigations -----------------------------------------------------

    #: What "still in the pool" means, in the one place it is spelled.
    _OPEN = "used_on IS NULL AND cleared_on IS NULL"

    def pool(self) -> list[d.Investigation]:
        """Everything still open, oldest first.

        Insertion order rather than `added_on` order: a paste puts five topics
        on the same day and the order they were typed is the only thing that
        separates them, so ordering by the date would shuffle each day's list
        on every read.
        """
        rows = self.conn.execute(
            f"SELECT * FROM investigation WHERE {self._OPEN} ORDER BY id"
        ).fetchall()
        return [d.Investigation(**dict(r)) for r in rows]

    def investigation(self, investigation_id: int) -> d.Investigation | None:
        row = self.conn.execute(
            "SELECT * FROM investigation WHERE id = ?", (investigation_id,)
        ).fetchone()
        return d.Investigation(**dict(row)) if row else None

    def investigation_folders(self) -> list[str]:
        """Folders that actually hold a graded write-up, alphabetically.

        Read off the rows rather than off config, so the strip shows what you
        have written about rather than what you might. A seeded folder nothing
        has been filed under has no rating and would only be a line of 1500s.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT folder FROM investigation "
            "WHERE folder IS NOT NULL ORDER BY folder"
        ).fetchall()
        return [r["folder"] for r in rows]

    def investigated_count(self) -> int:
        """Topics carried to a grade. Struck-off ones are deliberately not
        counted -- clearing the pool is tidying, not work."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM investigation WHERE used_on IS NOT NULL"
        ).fetchone()[0]

    def _add_investigation(self, topic: str, on: dt.date) -> d.Investigation | None:
        """One topic, or None if an identical one is already open.

        None rather than an exception: the caller is reading a pasted box and a
        line already in the pool is the ordinary case, not a failure -- the
        same list gets pasted twice far more often than it gets typed twice.

        The case-folded uniqueness is the partial index's, not this method's.
        Spelling the rule again here would be a second copy that could disagree
        with the first under a race.
        """
        try:
            row = self.conn.execute(
                "INSERT INTO investigation (added_on, topic) VALUES (?, ?) RETURNING *",
                (_iso(on), topic),
            ).fetchone()
        except sqlite3.IntegrityError:
            return None
        return d.Investigation(**dict(row))

    def add_investigations(
        self, topics: Sequence[str], on: dt.date
    ) -> list[d.Investigation | None]:
        """A pasted list, added in one transaction, in order.

        One transaction for the paste rather than one per line: the insert is
        guarded by a partial unique index, so two tabs pasting the same handout
        at once would otherwise interleave into a pool half-filled from each.

        The result is positional -- None where the topic was already open -- so
        the caller can report per line without matching anything back up.
        """
        with transaction(self.conn):
            return [self._add_investigation(topic, on) for topic in topics]

    def clear_investigation(self, investigation_id: int, on: dt.date) -> d.Investigation:
        """Strike one topic off without spending a day on it.

        Guarded on `used_on IS NULL`: an investigated topic is history, and
        history does not get struck off. Clearing an already-cleared row is a
        no-op that hands it back unchanged.
        """
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE investigation SET cleared_on = ? "
                "WHERE id = ? AND used_on IS NULL AND cleared_on IS NULL",
                (_iso(on), investigation_id),
            )
            row = self.investigation(investigation_id)
        if row is None:
            raise LookupError(f"no investigation {investigation_id}")
        return row

    def set_investigation_path(self, investigation_id: int, path: str) -> d.Investigation:
        """Record where a topic's write-up lives, once.

        `WHERE path IS NULL` because the path is the handle on hours of typing:
        a second call is either a race between two tabs opening the editor or a
        bug, and in both cases the file that already exists is the right
        answer. The caller re-reads rather than trusting its own argument.
        """
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE investigation SET path = ? WHERE id = ? AND path IS NULL",
                (path, investigation_id),
            )
            row = self.investigation(investigation_id)
        if row is None:
            raise LookupError(f"no investigation {investigation_id}")
        return row

    def relocate_investigation(self, investigation_id: int, path: str) -> None:
        """Point a topic at where its file moved to.

        Unguarded, unlike `set_investigation_path`: that one refuses to
        overwrite because it is claiming a file for the first time, and this
        one is recording that a file the server just moved is somewhere else
        now. Refusing here would leave the row pointing at nothing.
        """
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE investigation SET path = ? WHERE id = ?", (path, investigation_id)
            )

    def record_write_up(self, date: dt.date, text: str) -> d.InvestigationDay:
        """Snapshot what the file said, as the day's submission.

        Overwrites a previous snapshot on purpose: reading the file back again
        after another half hour of editing is the ordinary way this is reached,
        and the last read before the ballot is what the ballot answers.
        """
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE investigation_day SET write_up = ?, "
                "written_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE date = ?",
                (text, _iso(date)),
            )
            row = self.investigation_day(date)
        if row is None:
            raise LookupError(f"nothing was drawn on {date}")
        return row

    def score_investigation(
        self, date: dt.date, ballot: d.InvestigationBallot, score: float, anchor: int, folder: str
    ) -> tuple[d.InvestigationDay, d.RatingEvent]:
        """Close the day on a pasted ballot, spend the topic, apply the elo.

        Three things in one transaction, and the ordering is the point. The
        `WHERE ballot IS NULL` is the double-submit guard -- two tabs pasting
        the same result update zero rows on the second and raise, rather than
        rating one afternoon twice.

        This is also the only place `used_on` is ever set: a topic leaves the
        pool when it has been graded and at no other moment, which is the whole
        of the "returns to the pool" rule stated as one UPDATE.
        """
        with transaction(self.conn):
            day = self.investigation_day(date)
            if day is None:
                raise LookupError(f"nothing was drawn on {date}")
            if day.ballot is not None:
                raise ValueError(f"the investigation on {date} is already graded")
            if day.write_up is None:
                raise ValueError("nothing has been read back to grade")

            row = self.conn.execute(
                "UPDATE investigation_day SET ballot = ?, score = ? "
                "WHERE date = ? AND ballot IS NULL RETURNING *",
                (_json(ballot), score, _iso(date)),
            ).fetchone()
            if row is None:
                raise ValueError(f"the investigation on {date} was graded concurrently")
            closed = self._investigation_day(row)

            self.conn.execute(
                "UPDATE investigation SET used_on = ?, folder = ? WHERE id = ?",
                (_iso(date), folder, day.investigation_id),
            )
            topic = self.investigation(day.investigation_id)
            assert topic is not None  # it is what the day points at

            event = self._rate_investigation(date, topic.topic, anchor, score, area=None)
            # The same result inside its folder, second and in the same
            # transaction -- `_close_math`'s rule, for its reason: the pooled
            # row is what the panel reads, so it must never be able to exist
            # without its partner.
            self._rate_investigation(date, topic.topic, anchor, score, area=folder)
            return closed, event

    def _rate_investigation(
        self, date: dt.date, topic: str, anchor: int, score: float, *, area: str | None
    ) -> d.RatingEvent:
        """One elo row against the fixed anchor. Caller holds the transaction;
        `area=None` is the pooled investigation rating."""
        n = self.event_count(Track.investigate, area)
        before = self.current_rating(Track.investigate, area)
        k = scoring.k_factor(n)
        after = scoring.elo_update(before, anchor, score, n)
        row = self.conn.execute(
            "INSERT INTO rating_event "
            "(track, date, ref, opponent, score, k, rating_before, rating_after, "
            "provenance, area) "
            "VALUES ('investigate', ?, ?, ?, ?, ?, ?, ?, 'measured', ?) RETURNING *",
            (_iso(date), topic[:300], anchor, score, k, before, after, area),
        ).fetchone()
        return d.RatingEvent(**dict(row))

    def last_investigation_ballot(self) -> d.InvestigationBallot | None:
        """The most recent graded ballot, whenever it was.

        Undated on purpose. The argument deck reads this for the reasoning
        moves it offers to add, and an offer is worth making until it is taken
        -- scoping it to today would mean a pattern named on Tuesday quietly
        stops being offered on Wednesday morning, which is exactly when you
        would have got round to adding it.
        """
        row = self.conn.execute(
            "SELECT ballot FROM investigation_day WHERE ballot IS NOT NULL "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return d.InvestigationBallot(**json.loads(row["ballot"]))

    @staticmethod
    def _investigation_day(row: sqlite3.Row) -> d.InvestigationDay:
        return d.InvestigationDay(**_unpack(row, "ballot"))

    def investigation_day(self, date: dt.date) -> d.InvestigationDay | None:
        row = self.conn.execute(
            "SELECT * FROM investigation_day WHERE date = ?", (_iso(date),)
        ).fetchone()
        return self._investigation_day(row) if row else None

    def draw_investigation(
        self, date: dt.date, rng: random.Random | None = None
    ) -> d.InvestigationDay | None:
        """Claim the day for a topic, or hand back the one that already has it.

        Uniform over the open pool. Not weighted by age the way `_draw_card` is
        -- that curve exists to space out *repeats*, and a topic here is drawn
        at most once, so there is nothing to space.

        `ON CONFLICT DO NOTHING` and a re-read rather than check-then-write,
        for the reason `record_draw` gives: two tabs loading at once both see
        no draw, and the loser of that race should get today's actual topic
        rather than an IntegrityError.

        None when the pool is empty, which is a state and not an error -- the
        panel says so and offers the box.
        """
        with transaction(self.conn):
            existing = self.investigation_day(date)
            if existing is not None:
                return existing

            rows = self.conn.execute(
                f"SELECT id FROM investigation WHERE {self._OPEN} ORDER BY id"
            ).fetchall()
            if not rows:
                return None
            chosen = (rng or random).choice([r["id"] for r in rows])

            self.conn.execute(
                "INSERT INTO investigation_day (date, investigation_id) VALUES (?, ?) "
                "ON CONFLICT (date) DO NOTHING",
                (_iso(date), chosen),
            )
            drawn = self.investigation_day(date)
        assert drawn is not None  # just inserted, or already there
        return drawn

    # -- word ---------------------------------------------------------------

    def word(self, date: dt.date) -> d.WordOfDay | None:
        row = self.conn.execute(
            "SELECT * FROM word_of_day WHERE date = ?", (_iso(date),)
        ).fetchone()
        return d.WordOfDay(**_unpack(row, "sentences", "grade")) if row else None

    def recent_words(self, before: dt.date, days: int) -> list[str]:
        """The words of the day in the `days` before `before`, oldest first.

        The do-not-repeat list, and the counterpart of `seen_sources` for math.
        Bounded by a window rather than "everything ever", because it is read
        into a prompt and the useful claim -- you have not had this word in a
        year -- is the one that stays a fixed size as the history grows.

        `before` is excluded, so asking on a day that somehow already has a row
        does not exclude that day's own word from itself.
        """
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT word FROM word_of_day WHERE date >= ? AND date < ? ORDER BY date",
                (_iso(before - dt.timedelta(days=days)), _iso(before)),
            )
        ]

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

        An argument-deck recall lands on the investigate track and on
        `ARGUMENT_AREA`, which is deliberately not one of the subject folders
        -- see that constant. Everything else about the write is identical,
        which is the whole point of the shared table.
        """
        track = Track.investigate if Deck(deck) is Deck.argument else Track.math
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
        track = Track.investigate if Deck(deck) is Deck.argument else Track.math
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

    def log_health(
        self,
        metric: HealthMetric,
        date: dt.date,
        value: float,
        source: EntrySource = EntrySource.manual,
    ) -> d.HealthEntry:
        """Upsert one daily scalar. **A sync never overwrites a hand entry.**

        That rule is enforced here, in the WHERE of the DO UPDATE, rather than
        in the importer -- there is one place to get it wrong instead of one
        place per caller. A manual write always lands; a `garmin` write lands
        only on a row that is absent or was itself written by a sync.

        The failure this prevents is small and infuriating: you notice the
        watch scored a restless night as five hours, you fix it, and the next
        morning's sync puts five hours back. After the third time you stop
        correcting anything, and the series quietly becomes the watch's opinion
        rather than the truth.
        """
        row = self.conn.execute(
            "INSERT INTO health_entry (metric, date, value, source) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (metric, date) DO UPDATE SET value = excluded.value, "
            "source = excluded.source "
            "WHERE excluded.source = 'manual' OR health_entry.source = 'garmin' "
            "RETURNING *",
            (HealthMetric(metric).value, _iso(date), value, EntrySource(source).value),
        ).fetchone()
        # The WHERE can suppress the write entirely, and RETURNING then yields
        # nothing. That is a no-op rather than an error -- the sync did its job
        # by declining -- so report the row that won.
        if row is None:
            return self.health_entry(metric, date)  # type: ignore[return-value]
        return d.HealthEntry(**dict(row))

    def health_entry(self, metric: HealthMetric, date: dt.date) -> d.HealthEntry | None:
        row = self.conn.execute(
            "SELECT * FROM health_entry WHERE metric = ? AND date = ?",
            (HealthMetric(metric).value, _iso(date)),
        ).fetchone()
        return d.HealthEntry(**dict(row)) if row else None

    def health_series(self, metric: HealthMetric, limit: int = 30) -> list[d.HealthEntry]:
        rows = self.conn.execute(
            "SELECT * FROM health_entry WHERE metric = ? ORDER BY date",
            (HealthMetric(metric).value,),
        ).fetchall()
        return [d.HealthEntry(**dict(r)) for r in rows[-limit:]]

    # -- readiness / sessions ----------------------------------------------

    def log_readiness(
        self,
        date: dt.date,
        sleep_quality: int,
        fatigue: int,
        soreness: int,
        stress: int,
        motivation: int,
        bed: str | None = None,
        woke: str | None = None,
    ) -> d.Readiness:
        """One morning, upserted.

        A re-save without the night keeps the night already on the row rather
        than erasing it -- `COALESCE`, and on the pair together, since the
        table refuses half of one. What that protects is a check-in queued by
        the build before migration 37 landing after one that had them."""
        row = self.conn.execute(
            "INSERT INTO readiness "
            "(date, sleep_quality, fatigue, soreness, stress, motivation, bed, woke) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET "
            "sleep_quality = excluded.sleep_quality, fatigue = excluded.fatigue, "
            "soreness = excluded.soreness, stress = excluded.stress, "
            "motivation = excluded.motivation, "
            "bed = CASE WHEN excluded.bed IS NULL THEN readiness.bed ELSE excluded.bed END, "
            "woke = CASE WHEN excluded.bed IS NULL THEN readiness.woke ELSE excluded.woke END "
            "RETURNING *",
            (_iso(date), sleep_quality, fatigue, soreness, stress, motivation, bed, woke),
        ).fetchone()
        return d.Readiness(**dict(row))

    def readiness(self, date: dt.date) -> d.Readiness | None:
        row = self.conn.execute("SELECT * FROM readiness WHERE date = ?", (_iso(date),)).fetchone()
        return d.Readiness(**dict(row)) if row else None

    def readiness_history(self, limit: int = 60) -> list[d.Readiness]:
        """The most recent `limit` answered mornings, oldest first. Two months
        covers the 30-day baseline with room for the gaps in it."""
        rows = self.conn.execute(
            "SELECT * FROM readiness ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [d.Readiness(**dict(r)) for r in reversed(rows)]

    def add_session(
        self,
        date: dt.date,
        kind: SessionKind,
        rpe: int,
        minutes: int,
        note: str | None = None,
        garmin_activity_id: int | None = None,
        plan_key: str | None = None,
        focus: str | None = None,
        quality: int | None = None,
        started: str | None = None,
    ) -> d.TrainingSession:
        """Append a session. Never an upsert -- two-a-days are the normal case,
        and a second session today is a second row, not a correction of the
        first.

        `focus` is trusted here. Whether it agrees with `kind` is settled at the
        request boundary, where the tree lives -- see
        `SessionRequest._focus_agrees_with_kind` -- because a Garmin import and
        a hand-written script both reach this method without one, and neither
        should have to carry the tree in order to log a run."""
        try:
            row = self.conn.execute(
                "INSERT INTO training_session "
                "(date, kind, rpe, minutes, note, garmin_activity_id, plan_key, focus, quality,"
                " started) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
                (
                    _iso(date),
                    SessionKind(kind).value,
                    rpe,
                    minutes,
                    note or None,
                    garmin_activity_id,
                    plan_key,
                    focus,
                    quality,
                    started,
                ),
            ).fetchone()
        except sqlite3.IntegrityError as error:
            # The partial unique index on plan_key. A conflict, not a fault:
            # a card double-clicked, or a second tab logging the same practice.
            # Reported the way every other double-submit guard here is, so the
            # panel gets a sentence and a 409 rather than a traceback and a 500.
            if plan_key and "plan_key" in str(error):
                raise ValueError(f"{plan_key} is already logged") from None
            raise
        return d.TrainingSession(**dict(row))

    def add_sessions(
        self, date: dt.date, entries: Sequence[tuple[SessionKind, int, int, str | None, str | None]]
    ) -> list[d.TrainingSession]:
        """A whole card at once: one practice, however many rows it splits into.

        One call rather than several because a practice is one thing that
        happened. Logging its conditioning half and then failing on its
        technique half would leave a day that is half-recorded and a card that
        cannot be resubmitted -- the unique index on `plan_key` would reject the
        replay of the half that landed. The connection is already in a
        transaction per request, so either all of them are there or none are.
        """
        return [
            self.add_session(date, kind, rpe, minutes, note, plan_key=key)
            for kind, rpe, minutes, note, key in entries
        ]

    def delete_session(self, session_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM training_session WHERE id = ?", (session_id,))
        return cur.rowcount > 0

    def sessions_on(self, date: dt.date) -> list[d.TrainingSession]:
        rows = self.conn.execute(
            "SELECT * FROM training_session WHERE date = ? ORDER BY id", (_iso(date),)
        ).fetchall()
        return [d.TrainingSession(**dict(r)) for r in rows]

    def sessions(self) -> list[d.TrainingSession]:
        """Every session ever logged, oldest first.

        Deliberately unwindowed. `health.py` gates its ratios on how much
        history exists, and it measures that from the oldest row it is handed
        -- so feeding it a trailing 28 days would make every third day of
        logging look like a full month of it, and the gates would pass
        immediately. The gates are the whole point, so the caller passes
        everything and lets `daily_load` do the windowing. A year of two-a-days
        is under a thousand rows; none of it crosses the wire.
        """
        rows = self.conn.execute("SELECT * FROM training_session ORDER BY date, id").fetchall()
        return [d.TrainingSession(**dict(r)) for r in rows]

    # -- the camp stopping and starting -------------------------------------
    #
    # Read whole, like `sessions` and for a smaller version of the same reason:
    # `trainplan.shift` sums every break that has already taken effect, so a
    # windowed read would quietly un-shift the camp the moment the first break
    # fell off the back of the window. There will be a handful of rows a year.

    def camp_breaks(self) -> list[d.CampBreak]:
        rows = self.conn.execute("SELECT * FROM camp_break ORDER BY started, id").fetchall()
        return [d.CampBreak(**dict(r)) for r in rows]

    def open_camp_break(self) -> d.CampBreak | None:
        """The break that has no resume date yet, if there is one.

        Distinct from "is the camp paused today", which is `trainplan.paused_on`
        and is a question about a date. A break with its Sunday still ahead of
        it is closed here and paused there, and both are right.
        """
        row = self.conn.execute(
            "SELECT * FROM camp_break WHERE resumed IS NULL"
        ).fetchone()
        return d.CampBreak(**dict(row)) if row else None

    def pause_camp(self, started: dt.date, reason: str) -> d.CampBreak:
        """Stop the camp from `started`.

        The partial unique index refuses a second open break, and that error is
        left to surface rather than being pre-checked: a pre-check is a race
        with itself, and the index is the thing that is actually true.
        """
        try:
            row = self.conn.execute(
                "INSERT INTO camp_break (started, reason) VALUES (?, ?) RETURNING *",
                (_iso(started), reason),
            ).fetchone()
        except sqlite3.IntegrityError as error:
            if "camp_break_open" in str(error):
                raise ValueError("the camp is already paused") from None
            raise
        return d.CampBreak(**dict(row))

    def resume_camp(self, resumed: dt.date) -> d.CampBreak | None:
        """Give the open break its Sunday. None when nothing was open."""
        row = self.conn.execute(
            "UPDATE camp_break SET resumed = ? WHERE resumed IS NULL RETURNING *",
            (_iso(resumed),),
        ).fetchone()
        return d.CampBreak(**dict(row)) if row else None

    def cancel_resume(self, today: dt.date) -> d.CampBreak | None:
        """Take back a resume date that has not arrived yet.

        The Friday decision reversed on the Saturday, which is a thing illness
        does. Bounded to `resumed > today` and that bound is the whole of the
        method: a break whose Sunday has passed has already shifted the grid,
        and reopening it would un-shift a camp that has been running on the new
        numbering for weeks. Those are not the same button -- one is an undo,
        the other is rewriting history -- and only the undo exists.
        """
        row = self.conn.execute(
            "UPDATE camp_break SET resumed = NULL WHERE resumed > ? RETURNING *",
            (_iso(today),),
        ).fetchone()
        return d.CampBreak(**dict(row)) if row else None

    # -- what goes in and what comes out ------------------------------------
    #
    # Both logs are append-only and many-per-day, like sessions and unlike
    # every scalar in `health_entry`. There is no upsert anywhere below: a
    # second lunch is a second row, and the way to fix a mistyped one is to
    # delete it, which costs one keystroke on the panel.
    #
    # Both are also read *whole* by the panel, for the reason `sessions`
    # explains at length: `health.py` gates its readings on how much history
    # it was handed, so handing it a trailing week would make three days of
    # logging look like a kept log and open every gate at once.

    def add_bathroom(
        self,
        date: dt.date,
        hour: int,
        kind: BathroomKind,
        volume: Volume,
        solidity: Stool | None = None,
        color: UrineColor | None = None,
        minute: int | None = None,
    ) -> d.BathroomLog:
        """Append one visit.

        The kind/field pairing is not checked here. The table's own CHECK does
        it, so a row that describes the colour of a stool is impossible for
        anything that opens this file rather than merely for callers who came
        through this method.
        """
        row = self.conn.execute(
            "INSERT INTO bathroom_log (date, hour, kind, solidity, color, volume, minute) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                _iso(date),
                hour,
                BathroomKind(kind).value,
                Stool(solidity).value if solidity else None,
                UrineColor(color).value if color else None,
                Volume(volume).value,
                minute,
            ),
        ).fetchone()
        return d.BathroomLog(**dict(row))

    def delete_bathroom(self, entry_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM bathroom_log WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def bathroom_on(self, date: dt.date) -> list[d.BathroomLog]:
        """One day, in time order -- which is the order the pattern is in."""
        rows = self.conn.execute(
            "SELECT * FROM bathroom_log WHERE date = ? ORDER BY hour, minute, id", (_iso(date),)
        ).fetchall()
        return [d.BathroomLog(**dict(r)) for r in rows]

    def bathroom_history(
        self, days: int = 60, before: dt.date | None = None
    ) -> list[d.BathroomLog]:
        """The trailing window, oldest first.

        Windowed where `sessions` is not, and the difference is volume: this
        table grows at four to eight rows a day rather than one, so a year of
        it is thousands of rows to answer a question whose longest window is
        twenty-eight days. `before` is the browser's today; without one the
        window is open-ended, which is what the tests and the CLI want.
        """
        if before is None:
            rows = self.conn.execute(
                "SELECT * FROM bathroom_log ORDER BY date DESC, hour DESC LIMIT ?", (days * 12,)
            ).fetchall()
            return [d.BathroomLog(**dict(r)) for r in reversed(rows)]
        start = before - dt.timedelta(days=days - 1)
        rows = self.conn.execute(
            "SELECT * FROM bathroom_log WHERE date BETWEEN ? AND ? ORDER BY date, hour, id",
            (_iso(start), _iso(before)),
        ).fetchall()
        return [d.BathroomLog(**dict(r)) for r in rows]

    def _meals(self, rows: Sequence[sqlite3.Row]) -> list[d.MealLog]:
        """Attach each meal's flags to it, in one more query rather than one
        per row. The twin of `_drinks` below, and the same reasoning: the flags
        are fetched flat and `canonical_flags` puts them in order, because
        `group_concat` in the main query would save the round trip and give no
        promise about the order they come back in."""
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        flags: dict[int, list[str]] = {entry_id: [] for entry_id in ids}
        holes = ",".join("?" * len(ids))
        for meal_id, flag in self.conn.execute(
            f"SELECT meal_id, flag FROM meal_flag WHERE meal_id IN ({holes})", ids
        ):
            flags[meal_id].append(flag)
        return [d.MealLog(**dict(r), flags=canonical_flags(flags[r["id"]])) for r in rows]

    def add_meal(
        self,
        date: dt.date,
        hour: int,
        size: MealSize,
        quality: int,
        note: str | None = None,
        preset: str | None = None,
        flags: Iterable[MealFlag] = (),
        minute: int | None = None,
        label: str | None = None,
    ) -> d.MealLog:
        """Append one meal.

        `preset` and `flags` are keyword-defaulted and sit after `note` so that
        every call written before they existed still means what it meant.

        A `preset` the menu has never seen is added to it here, in the same
        transaction as the meal -- see `_name_meal`. No flags writes no
        `meal_flag` rows at all, exactly as no tags writes no `drink_tag` rows:
        the absence is the answer, not a missing one."""
        if preset:
            self._name_meal(preset, label)
        row = self.conn.execute(
            "INSERT INTO meal_log (date, hour, size, quality, note, preset, minute) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                _iso(date),
                hour,
                MealSize(size).value,
                quality,
                note or None,
                preset or None,
                minute,
            ),
        ).fetchone()
        written = canonical_flags(flags)
        self.conn.executemany(
            "INSERT INTO meal_flag (meal_id, flag) VALUES (?, ?)",
            [(row["id"], flag.value) for flag in written],
        )
        return d.MealLog(**dict(row), flags=written)

    def _name_meal(self, slug: str, label: str | None) -> None:
        """Make sure `slug`, and its parent if it has one, is on `meal_name`.

        First writer wins: a slug already there keeps the words it came with,
        so a second phone spelling it differently cannot rename it under rows
        already printed. A missing label -- a body from before labels, or one a
        script wrote -- is the slug spelled out, because the meal is the thing
        worth keeping and the words can be fixed with one UPDATE.

        The parent is only ever missing when something other than the phone
        wrote the body (the phone names variations from a parent it is showing),
        but a variation whose parent has no row would be a branch with nothing
        to hang from."""
        parent, dot, _ = slug.partition(".")
        if dot:
            self._name_meal(parent, None)
        words = label or slug.rpartition(".")[2].replace("_", " ")
        self.conn.execute(
            "INSERT OR IGNORE INTO meal_name (slug, label) VALUES (?, ?)", (slug, words)
        )

    def meal_names(self) -> list[tuple[str, str, dt.date | None]]:
        """Every name ever given, as `(slug, label, given)` -- hidden ones too,
        because the phone's name box searches all of them."""
        return [
            (r["slug"], r["label"], dt.date.fromisoformat(r["given"]) if r["given"] else None)
            for r in self.conn.execute("SELECT slug, label, given FROM meal_name ORDER BY slug")
        ]

    def named_meals(self, before: dt.date) -> list[d.MealLog]:
        """Every meal that says what it was, oldest first, up to `before`.

        Whole history rather than a window, because whether a name ever earned
        its place is a question about all of it. It is only the rows with a
        name -- a few a day -- and nothing else in the panel reads this."""
        rows = self.conn.execute(
            "SELECT * FROM meal_log WHERE preset IS NOT NULL AND date <= ? "
            "ORDER BY date, hour, minute, id",
            (_iso(before),),
        ).fetchall()
        return self._meals(rows)

    def delete_meal(self, entry_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM meal_log WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def meals_on(self, date: dt.date) -> list[d.MealLog]:
        rows = self.conn.execute(
            "SELECT * FROM meal_log WHERE date = ? ORDER BY hour, minute, id", (_iso(date),)
        ).fetchall()
        return self._meals(rows)

    def meal_history(self, days: int = 60, before: dt.date | None = None) -> list[d.MealLog]:
        """As `bathroom_history`, and windowed for the same reason."""
        if before is None:
            rows = self.conn.execute(
                "SELECT * FROM meal_log ORDER BY date DESC, hour DESC LIMIT ?", (days * 6,)
            ).fetchall()
            return self._meals(list(reversed(rows)))
        start = before - dt.timedelta(days=days - 1)
        rows = self.conn.execute(
            "SELECT * FROM meal_log WHERE date BETWEEN ? AND ? ORDER BY date, hour, id",
            (_iso(start), _iso(before)),
        ).fetchall()
        return self._meals(rows)

    def _drinks(self, rows: Sequence[sqlite3.Row]) -> list[d.DrinkLog]:
        """Attach each drink's tags to it, in one more query rather than one
        per row.

        `group_concat` in the main query would save the round trip and give no
        promise about the order the tags come back in, which is the one thing
        this has to get right -- so the tags are fetched flat and
        `canonical_tags` puts them in order, the same call the boundary makes
        on the way in.
        """
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        tags: dict[int, list[str]] = {entry_id: [] for entry_id in ids}
        holes = ",".join("?" * len(ids))
        for drink_id, tag in self.conn.execute(
            f"SELECT drink_id, tag FROM drink_tag WHERE drink_id IN ({holes})", ids
        ):
            tags[drink_id].append(tag)
        return [d.DrinkLog(**dict(r), tags=canonical_tags(tags[r["id"]])) for r in rows]

    def add_drink(
        self,
        date: dt.date,
        hour: int,
        tags: Iterable[DrinkTag],
        amount: float,
        name: DrinkName | None = None,
        minute: int | None = None,
    ) -> d.DrinkLog:
        """Append one drink. `amount` is in drinks; see `DrinkLog`.

        No tags is a glass of water and writes no `drink_tag` rows at all --
        the absence is the answer, not a missing one. `name` is the same kind
        of absence one level up: no name reads as "some drink with those things
        in it", which is what most rows are and what declining to tap a name on
        the phone means.
        """
        row = self.conn.execute(
            "INSERT INTO drink_log (date, hour, amount, name, minute) "
            "VALUES (?, ?, ?, ?, ?) RETURNING *",
            (_iso(date), hour, float(amount), DrinkName(name).value if name else None, minute),
        ).fetchone()
        written = canonical_tags(tags)
        self.conn.executemany(
            "INSERT INTO drink_tag (drink_id, tag) VALUES (?, ?)",
            [(row["id"], tag.value) for tag in written],
        )
        return d.DrinkLog(**dict(row), tags=written)

    def delete_drink(self, entry_id: int) -> bool:
        # The tags go with it, through ON DELETE CASCADE -- which is why this is
        # still one statement. `PRAGMA foreign_keys` is ON in `connection.py`;
        # without it this would leave the tags behind to be inherited by
        # whatever row is next handed that id.
        cur = self.conn.execute("DELETE FROM drink_log WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def drinks_on(self, date: dt.date) -> list[d.DrinkLog]:
        rows = self.conn.execute(
            "SELECT * FROM drink_log WHERE date = ? ORDER BY hour, minute, id", (_iso(date),)
        ).fetchall()
        return self._drinks(rows)

    def drink_history(self, days: int = 60, before: dt.date | None = None) -> list[d.DrinkLog]:
        """As `bathroom_history`, and windowed for the same reason -- more so:
        this is the one of the three that runs to a dozen rows on a hot day."""
        if before is None:
            rows = self.conn.execute(
                "SELECT * FROM drink_log ORDER BY date DESC, hour DESC LIMIT ?", (days * 16,)
            ).fetchall()
            return self._drinks(list(reversed(rows)))
        start = before - dt.timedelta(days=days - 1)
        rows = self.conn.execute(
            "SELECT * FROM drink_log WHERE date BETWEEN ? AND ? ORDER BY date, hour, id",
            (_iso(start), _iso(before)),
        ).fetchall()
        return self._drinks(rows)

    # -- how you feel --------------------------------------------------------

    def _feels(self, rows: Sequence[sqlite3.Row]) -> list[d.FeelLog]:
        """Attach each row's words, in one more query -- `_drinks`'s pattern,
        and the same reason for fetching flat and ordering here: the two kinds
        of word and the symptoms each come back in their enum's order."""
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        tags: dict[int, list[tuple[str, int | None]]] = {entry_id: [] for entry_id in ids}
        holes = ",".join("?" * len(ids))
        for feel_id, tag, severity in self.conn.execute(
            f"SELECT feel_id, tag, severity FROM feel_tag WHERE feel_id IN ({holes})", ids
        ):
            tags[feel_id].append((tag, severity))
        out = []
        for r in rows:
            words = dict(tags[r["id"]])
            out.append(
                d.FeelLog(
                    **{**dict(r), "prompted": bool(r["prompted"])},
                    tired=canonical_feel([w for w in words if w in Tired.__members__], Tired),
                    low=canonical_feel([w for w in words if w in Low.__members__], Low),
                    symptoms={
                        s: words[s.value] for s in Symptom if words.get(s.value) is not None
                    },
                )
            )
        return out

    def add_feel(
        self,
        date: dt.date,
        hour: int,
        energy: int,
        mood: int,
        *,
        minute: int | None = None,
        tired: Iterable[Tired] = (),
        low: Iterable[Low] = (),
        symptoms: dict[Symptom, int] | None = None,
        note: str | None = None,
        prompted: bool = False,
    ) -> d.FeelLog:
        """Append one feel, with its words. The request model has already held
        the kinds to their scales; the table holds the severities to the
        symptoms.

        One transaction, which the drink log does without: a feel whose words
        failed to land would be a malaise filed as a fine afternoon, and that is
        the one row this log must not have."""
        with transaction(self.conn):
            row = self.conn.execute(
                "INSERT INTO feel_log (date, hour, minute, energy, mood, note, prompted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (_iso(date), hour, minute, energy, mood, note or None, int(prompted)),
            ).fetchone()
            words: list[tuple[int, str, int | None]] = [
                (row["id"], Tired(w).value, None) for w in tired
            ] + [(row["id"], Low(w).value, None) for w in low] + [
                (row["id"], Symptom(s).value, int(n)) for s, n in (symptoms or {}).items()
            ]
            self.conn.executemany(
                "INSERT INTO feel_tag (feel_id, tag, severity) VALUES (?, ?, ?)", words
            )
        return self._feels(
            self.conn.execute("SELECT * FROM feel_log WHERE id = ?", (row["id"],)).fetchall()
        )[0]

    def delete_feel(self, entry_id: int) -> bool:
        # The words go with it through ON DELETE CASCADE, as a drink's tags do.
        cur = self.conn.execute("DELETE FROM feel_log WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def feel_on(self, date: dt.date) -> list[d.FeelLog]:
        rows = self.conn.execute(
            "SELECT * FROM feel_log WHERE date = ? ORDER BY hour, minute, id", (_iso(date),)
        ).fetchall()
        return self._feels(rows)

    def feel_history(self, days: int, before: dt.date) -> list[d.FeelLog]:
        """The trailing window, oldest first -- windowed like the other logs."""
        start = before - dt.timedelta(days=days - 1)
        rows = self.conn.execute(
            "SELECT * FROM feel_log WHERE date BETWEEN ? AND ? ORDER BY date, hour, minute, id",
            (_iso(start), _iso(before)),
        ).fetchall()
        return self._feels(rows)

    def feel_pings(self, date: dt.date) -> dict[int, tuple[str, str | None]]:
        """`{slot: (due, sent)}` for one day -- empty until the scheduler has
        drawn it."""
        return {
            slot: (due, sent)
            for slot, due, sent in self.conn.execute(
                "SELECT slot, due, sent FROM feel_ping WHERE date = ? ORDER BY slot",
                (_iso(date),),
            )
        }

    def draw_pings(self, date: dt.date, dues: Sequence[str]) -> dict[int, tuple[str, str | None]]:
        """Put the day's drawn minutes down, unless they already are.

        `INSERT OR IGNORE`, so a second draw -- a restart halfway through the
        day, or two threads racing at midnight -- keeps the first one. The
        minute is only a secret if it is drawn once."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO feel_ping (date, slot, due) VALUES (?, ?, ?)",
            [(_iso(date), slot, due) for slot, due in enumerate(dues)],
        )
        return self.feel_pings(date)

    def mark_ping_sent(self, date: dt.date, slot: int, at: str) -> None:
        self.conn.execute(
            "UPDATE feel_ping SET sent = ? WHERE date = ? AND slot = ? AND sent IS NULL",
            (at, _iso(date), slot),
        )

    def add_push_subscription(
        self, endpoint: str, p256dh: str, auth: str, origin: str | None, added: dt.date
    ) -> int:
        """Keep one phone's subscription; the same endpoint again replaces its
        keys, which is what a browser re-subscribing hands over. Returns how
        many are kept."""
        self.conn.execute(
            "INSERT INTO push_subscription (endpoint, p256dh, auth, origin, added) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (endpoint) DO UPDATE SET "
            "p256dh = excluded.p256dh, auth = excluded.auth, origin = excluded.origin",
            (endpoint, p256dh, auth, origin, _iso(added)),
        )
        return len(self.push_subscriptions())

    def push_subscriptions(self) -> list[dict[str, str | None]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT endpoint, p256dh, auth, origin FROM push_subscription ORDER BY added"
            )
        ]

    def drop_push_subscription(self, endpoint: str) -> None:
        """A phone that has gone -- the push service answered 404 or 410."""
        self.conn.execute("DELETE FROM push_subscription WHERE endpoint = ?", (endpoint,))

    def daylog(self, date: dt.date) -> str | None:
        row = self.conn.execute("SELECT text FROM daylog WHERE date = ?", (_iso(date),)).fetchone()
        return row[0] if row else None

    def set_daylog(self, date: dt.date, text: str) -> None:
        self.conn.execute(
            "INSERT INTO daylog (date, text) VALUES (?, ?) "
            "ON CONFLICT (date) DO UPDATE SET text = excluded.text",
            (_iso(date), text),
        )

    def piano_practiced(self, date: dt.date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM piano_practice WHERE date = ?", (_iso(date),)
        ).fetchone()
        return row is not None

    def set_piano_practice(self, date: dt.date, practiced: bool) -> None:
        """Tick or untick the day. Absence is the false, so untick deletes.

        `DO NOTHING` rather than an upsert on the tick: a second press is the
        same claim as the first, and rewriting `logged_at` would move the time
        you sat down to the time you pressed the button twice.
        """
        if practiced:
            self.conn.execute(
                "INSERT INTO piano_practice (date) VALUES (?) ON CONFLICT (date) DO NOTHING",
                (_iso(date),),
            )
        else:
            self.conn.execute("DELETE FROM piano_practice WHERE date = ?", (_iso(date),))

    def set_completion(
        self, date: dt.date, done: int, total: int, bonus: int = 0
    ) -> d.DayCompletion:
        row = self.conn.execute(
            "INSERT INTO day_completion (date, done, total, bonus) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (date) DO UPDATE SET done = excluded.done, total = excluded.total, "
            "bonus = excluded.bonus "
            "RETURNING *",
            (_iso(date), done, total, bonus),
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

    # -- portfolio credentials ----------------------------------------------
    #
    # Returns tuples rather than a Pydantic model, breaking this file's own
    # rule that nothing above it sees a Row. The rule exists so that domain
    # objects are validated in one place; a credential has no domain -- it is
    # three opaque fields handed straight back to `webauthn` and never rendered,
    # summed or rated. A model here would be a name for a tuple.

    def credentials(self) -> list[tuple[str, bytes, int, int]]:
        """`(id, public_key, alg, sign_count)` for every enrolled authenticator."""
        return [
            (row["id"], row["public_key"], row["alg"], row["sign_count"])
            for row in self.conn.execute(
                "SELECT id, public_key, alg, sign_count FROM webauthn_credential ORDER BY created"
            )
        ]

    def credential(self, credential_id: str) -> tuple[str, bytes, int, int] | None:
        row = self.conn.execute(
            "SELECT id, public_key, alg, sign_count FROM webauthn_credential WHERE id = ?",
            (credential_id,),
        ).fetchone()
        return (row["id"], row["public_key"], row["alg"], row["sign_count"]) if row else None

    def credential_ids(self) -> list[str]:
        return [row["id"] for row in self.conn.execute("SELECT id FROM webauthn_credential")]

    def add_credential(
        self, credential_id: str, public_key: bytes, alg: int, date: dt.date, label: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO webauthn_credential (id, public_key, alg, label, created) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (credential_id, public_key, alg, label, _iso(date)),
        )

    def touch_credential(self, credential_id: str, sign_count: int, date: dt.date) -> None:
        """Record a successful unlock.

        `MAX` rather than assignment: Hello reports a constant 0, and letting
        that overwrite a real counter from another authenticator would disable
        the clone check for the credential that actually had one.
        """
        self.conn.execute(
            "UPDATE webauthn_credential "
            "SET sign_count = MAX(sign_count, ?), last_used = ? WHERE id = ?",
            (sign_count, _iso(date), credential_id),
        )

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

    # -- the calendar classifier --------------------------------------------

    def record_calendar_correction(
        self, *, text: str, predicted: str | None, chosen: str, source: str
    ) -> None:
        """Remember that the quick-add box was told it picked the wrong calendar."""
        self.conn.execute(
            "INSERT INTO calendar_correction (created, text, predicted, chosen, source) "
            "VALUES (strftime('%Y-%m-%dT%H:%M:%S', 'now'), ?, ?, ?, ?)",
            (text[:500], predicted, chosen[:200], source),
        )
        self.conn.commit()

    def calendar_corrections(self, limit: int = 2000) -> list[tuple[str, str]]:
        """`(text, chosen)` newest first -- the extra training rows."""
        rows = self.conn.execute(
            "SELECT text, chosen FROM calendar_correction ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(row["text"], row["chosen"]) for row in rows]

    def calendar_correction_rate(self, limit: int = 200) -> tuple[int, int]:
        """`(corrected, total)` over the recent window.

        Only meaningful beside the adds that were *not* corrected, which are
        not in this table -- so this counts corrections whose prediction stood
        against those where it did not, which is the accuracy of the guess at
        the moment someone looked at it.
        """
        rows = self.conn.execute(
            "SELECT predicted, chosen FROM calendar_correction "
            "ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return sum(1 for r in rows if r["predicted"] != r["chosen"]), len(rows)

    # --- the todo list ----------------------------------------------------

    def add_todo(
        self,
        *,
        raw: str,
        title: str,
        subject: str | None,
        calendar: str | None,
        due_date: dt.date | None,
        start: dt.time | None,
        end: dt.time | None,
        series: str | None,
        event_id: str | None,
        event_url: str | None,
    ) -> d.TodoItem:
        """Record one occurrence. Called after its Google event exists.

        After, not before, and not in the same breath: the row is the claim
        that the event was written, so a failed `gcalcli add` leaves no row and
        the line goes back in the box with its error. The alternative -- a row
        marked pending -- means a list that shows work Google has never heard
        of, which is the failure mode this whole feature exists to avoid.

        An undated (`todo`) item has no event, and so arrives here with no date and no
        calendar. `event_deleted` is set from the date rather than passed,
        because there is exactly one right answer and asking every caller for
        it is asking to eventually be told the wrong one -- the schema's last
        CHECK would then reject the row, which is a 500 in place of a todo.
        """
        row = self.conn.execute(
            "INSERT INTO todo (raw, title, subject, calendar, due_date, start_time,"
            " end_time, series, event_id, event_url, event_deleted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (
                raw[:500],
                title[:300],
                subject,
                calendar,
                due_date.isoformat() if due_date else None,
                start.strftime("%H:%M") if start else None,
                end.strftime("%H:%M") if end else None,
                series,
                event_id,
                event_url,
                1 if due_date is None else 0,
            ),
        ).fetchone()
        self.conn.commit()
        return _todo(row)

    def todo(self, todo_id: int) -> d.TodoItem:
        row = self.conn.execute("SELECT * FROM todo WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            raise LookupError(f"no todo {todo_id}")
        return _todo(row)

    def todos(
        self, *, through: dt.date | None = None, include_done_since: dt.date | None = None
    ) -> list[d.TodoItem]:
        """Everything outstanding, plus recently finished work.

        Two windows rather than one, because they are asked for different
        reasons. Open items are unbounded backwards -- an overdue reading from
        three weeks ago is still owed, and hiding it is how it stays owed.
        Finished ones are bounded, because their only remaining job is to show
        that the day had something in it.

        An undated item has no `due_date` to bound either window by, so the
        finished ones are windowed on `done_at` instead. Without that arm a
        ticked "fix the coffee table" would vanish the instant it was ticked --
        `NULL >= '2026-08-14'` is NULL, which is not true -- and the undo in
        the done box would have nothing to undo.

        `through` is deliberately left NULL-unsafe: an undated row is not due
        by any date, so a caller asking "what is owed by Friday" should not be
        handed one.
        """
        clauses = ["(done_at IS NULL"]
        params: list[Any] = []
        if include_done_since is not None:
            clauses.append(" OR due_date >= ? OR (due_date IS NULL AND done_at >= ?)")
            params += [include_done_since.isoformat()] * 2
        clauses.append(")")
        where = "".join(clauses)
        if through is not None:
            where += " AND due_date <= ?"
            params.append(through.isoformat())
        rows = self.conn.execute(
            # Undated last, and NULLs sort first in SQLite -- hence the flag
            # column rather than a bare `ORDER BY due_date`.
            f"SELECT * FROM todo WHERE {where} ORDER BY due_date IS NULL, due_date,"
            " start_time IS NULL, start_time, id",
            params,
        ).fetchall()
        return [_todo(row) for row in rows]

    def set_todo_done(self, todo_id: int, done: bool) -> d.TodoItem:
        """Tick or un-tick. Touches nothing in Google -- the caller does that.

        Split deliberately: the tick has to land whether or not gcalcli is
        reachable, and a route that deletes first would make a network failure
        into a checkbox that does not check.
        """
        self.conn.execute(
            "UPDATE todo SET done_at = "
            "CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%S', 'now') ELSE NULL END "
            "WHERE id = ?",
            (1 if done else 0, todo_id),
        )
        self.conn.commit()
        return self.todo(todo_id)

    def mark_todo_event_deleted(self, todo_id: int) -> d.TodoItem:
        self.conn.execute(
            "UPDATE todo SET event_deleted = 1 WHERE id = ?", (todo_id,)
        )
        self.conn.commit()
        return self.todo(todo_id)

    def drop_todo(self, todo_id: int) -> None:
        self.conn.execute("DELETE FROM todo WHERE id = ?", (todo_id,))
        self.conn.commit()

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM rating_event").fetchone()[0] == 0
