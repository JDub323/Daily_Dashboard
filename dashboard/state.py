"""The read model: rows -> the payload the browser boots from.

`state.json` was both the storage format and the API payload, which is what
let a stale tab POST its whole world back and full-replace the file. Those are
now two different shapes, and this module is the one-way function between
them. Nothing here writes.

Every function takes `today` as an argument. The browser is the sole authority
on what day it is -- the server's `dt.date.today()` ran in WSL's timezone
while the client compared against its own, so any skew silently broke every
"done today" check.
"""

from __future__ import annotations

import datetime as dt

from . import scoring
from .db.repository import Repository
from .models import api
from .models.common import Game, HealthMetric, Track

HISTORY_LIMIT = 40
"""Rating events sent to the elo charts. The chart plots the last 40 anyway;
shipping the whole history was pure payload."""

HEALTH_LIMIT = 30


# --- shared ----------------------------------------------------------------


def track_summary(repo: Repository, track: Track) -> api.TrackSummary:
    n = repo.event_count(track)
    return api.TrackSummary(
        rating=repo.current_rating(track),
        k_next=scoring.k_factor(n),
        measured_count=n,
        success_rate=repo.success_rate(track),
        history=repo.rating_history(track, limit=HISTORY_LIMIT),
    )


# --- panels ----------------------------------------------------------------


def math_panel(repo: Repository, today: dt.date) -> api.MathPanel:
    summary = track_summary(repo, Track.math)
    attempt = repo.math_attempt(today)
    if attempt is None:
        return api.MathPanel(summary=summary)

    problem = repo.problem(attempt.problem_id)
    if problem is None:
        # The attempt's FK guarantees this cannot happen; treating it as "no
        # problem today" rather than asserting keeps a corrupted row from
        # taking the whole dashboard down.
        return api.MathPanel(summary=summary)

    return api.MathPanel(
        summary=summary,
        problem=problem,
        hints_revealed=attempt.hints_revealed,
        status=attempt.status.value,
        score=attempt.score,
        # Only what has actually been revealed. The old client received all
        # three hints and merely declined to render them.
        hints_visible=problem.hints[: attempt.hints_revealed],
    )


def chess_panel(repo: Repository, today: dt.date) -> api.ChessPanel:
    summary = track_summary(repo, Track.chess)
    attempt = repo.chess_attempt(today)
    if attempt is None:
        return api.ChessPanel(summary=summary)

    puzzle = repo.chess_puzzle(attempt.puzzle_id)
    if puzzle is None:
        return api.ChessPanel(summary=summary)

    return api.ChessPanel(
        summary=summary,
        puzzle_id=puzzle.id,
        puzzle_rating=puzzle.rating,
        puzzle_url=puzzle.url,
        themes=puzzle.themes,
        status=attempt.status.value,
        score=attempt.score,
    )


def games_panel(repo: Repository, today: dt.date) -> api.GamesPanel:
    wordle = repo.wordle(today)
    pokedoku = repo.pokedoku(today)
    return api.GamesPanel(
        wordle_today=api.WordleLogRequest(**wordle.model_dump()) if wordle else None,
        pokedoku_today=api.PokedokuLogRequest(**pokedoku.model_dump()) if pokedoku else None,
        wordle_streak=scoring.streak(repo.played_dates(Game.wordle), today),
        pokedoku_streak=scoring.streak(repo.played_dates(Game.pokedoku), today),
        wordle_average=repo.wordle_average(),
    )


def theorems_panel(repo: Repository, today: dt.date) -> api.TheoremsPanel:
    return api.TheoremsPanel(due=repo.due_cards(today), deck_size=repo.deck_size())


def papers_panel(repo: Repository, today: dt.date) -> api.PapersPanel:
    return api.PapersPanel(
        surfaced=repo.surfaced_paper(today),
        queue=repo.queue(),
        cleared_count=repo.cleared_count(),
    )


def health_series(repo: Repository) -> list[api.HealthSeries]:
    return [
        api.HealthSeries(
            metric=metric,
            points=[(e.date, e.value) for e in repo.health_series(metric, HEALTH_LIMIT)],
        )
        for metric in HealthMetric
    ]


# --- the boot payload ------------------------------------------------------


def dashboard_state(repo: Repository, today: dt.date) -> api.DashboardState:
    return api.DashboardState(
        date=today,
        math=math_panel(repo, today),
        chess=chess_panel(repo, today),
        word=repo.word(today),
        games=games_panel(repo, today),
        theorems=theorems_panel(repo, today),
        papers=papers_panel(repo, today),
        health=health_series(repo),
        daylog=repo.daylog(today),
        completion=repo.completions(),
        perfect_day_streak=scoring.streak(repo.perfect_days(), today),
    )
