"""The HTTP layer. Routing and nothing else.

Every handler is thin on purpose: validate (FastAPI does it from the models),
call one function in `content`/`feeds`/`state`, return a model. The business
logic that used to live inside `do_POST` is in modules that can be called
without a socket.

Three deletions worth naming:

* `POST /api/state` is gone. It full-replaced the entire state from whatever
  a browser tab happened to be holding, and was already commented "legacy,
  avoid" while still being live.
* `POST /api/state/merge` is gone with it. One endpoint per write plus
  per-column UPDATE leaves whole-subtree merging nothing to do.
* The hand-written static map is gone. The front end is ES modules now, so
  it is a directory of files rather than three named ones, and `/chess.js`
  simply does not exist any more.

Handlers are sync `def`, so FastAPI runs them on the threadpool. That is
deliberate -- everything underneath (urllib, sqlite3, subprocess) blocks, and
pretending otherwise on an async handler would stall the event loop.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import random
import socket
import subprocess
import threading
import time
import webbrowser
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import pydantic
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import content, debate, feeds, interests, logs, scoring, spend, state
from .config import Settings, load_env, settings
from .content import ContentError
from .db import Database, Repository
from .debate import DebateError
from .feeds import FeedError, Feeds
from .llm import LlmError
from .models import api
from .models import domain as dom
from .models.common import PaperStatus, Track
from .models.domain import PokedokuResult, WordleResult

log = logs.get(__name__)

VENDOR_PREFIX = "/vendor/"
ONE_YEAR = 60 * 60 * 24 * 365


class StaticAssets(StaticFiles):
    """StaticFiles that says what the browser may cache.

    Starlette sends `ETag` and `Last-Modified` but no `Cache-Control`, so
    browsers fall back to *heuristic* freshness and will serve a stored copy
    without revalidating at all. Editing `app/main.js` and reloading then
    shows the old module -- which is indistinguishable from the edit not
    having worked, and cost an hour of exactly that confusion.

    `no-cache` does not mean "do not store"; it means "revalidate first". The
    ETag turns that into a 304 against a server on loopback.
    """

    def file_response(
        self,
        full_path: os.PathLike[str],
        stat_result: os.stat_result,
        scope: Any,
        status_code: int = 200,
    ) -> Any:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(scope.get("path", "")).startswith(VENDOR_PREFIX):
            # Version-pinned and never edited in place, fonts included.
            response.headers["cache-control"] = f"public, max-age={ONE_YEAR}, immutable"
        else:
            response.headers["cache-control"] = "no-cache"
        return response


# --- app wiring ------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg: Settings = app.state.settings
    logs.setup(
        cfg.server.log_level, cfg.root / cfg.server.log_file if cfg.server.log_file else None
    )
    load_env(cfg.root / ".env")
    app.state.db = Database(cfg.path("db"))
    app.state.feeds = Feeds(cfg)
    log.info("dashboard ready on http://%s:%s", cfg.server.host, cfg.server.port)
    try:
        yield
    finally:
        app.state.db.close()


def create_app(cfg: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Daily Dashboard", lifespan=lifespan, docs_url="/api/docs")
    app.state.settings = cfg or settings()
    _register(app)
    return app


# --- dependencies ----------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repo(request: Request) -> Repository:
    return Repository(request.app.state.db.conn)


def get_feeds(request: Request) -> Feeds:
    return request.app.state.feeds


Cfg = Annotated[Settings, Depends(get_settings)]
Repo = Annotated[Repository, Depends(get_repo)]
Fetch = Annotated[Feeds, Depends(get_feeds)]

# The browser supplies its own local date on every dated request. On GETs it
# is a query parameter for the same reason it is a body field on POSTs.
Today = Annotated[dt.date, Query(description="the browser's local date")]


# --- error handling --------------------------------------------------------


def _register_errors(app: FastAPI) -> None:
    """Map domain failures onto status codes, and log the traceback.

    The panel still gets a one-line message -- but it is no longer the *only*
    place the failure exists. `logger.exception` keeps the stack.
    """

    @app.exception_handler(FeedError)
    def _feed(request: Request, exc: FeedError) -> JSONResponse:
        log.warning("%s: feed unavailable: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(LlmError)
    def _llm(request: Request, exc: LlmError) -> JSONResponse:
        log.exception("%s: LLM call failed", request.url.path)
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(ContentError)
    def _content(request: Request, exc: ContentError) -> JSONResponse:
        log.exception("%s: unusable generated content", request.url.path)
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(DebateError)
    def _ballot(request: Request, exc: DebateError) -> JSONResponse:
        # 400, not 502: nothing upstream failed. A ballot that will not parse is
        # a paste that went wrong, and the message says which way -- so it is
        # logged at info without a traceback, like the other client mistakes.
        log.info("%s: unusable ballot: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(LookupError)
    def _missing(request: Request, exc: LookupError) -> JSONResponse:
        log.info("%s: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(pydantic.ValidationError)
    def _invalid(request: Request, exc: pydantic.ValidationError) -> JSONResponse:
        # Registered *before* the ValueError handler matters: pydantic's
        # ValidationError is a ValueError subclass, and Starlette resolves
        # handlers by walking the MRO, so without this entry a model that
        # failed to build inside a handler would be reported as a conflict.
        log.exception("%s: model validation failed", request.url.path)
        return JSONResponse({"error": "invalid data — see the server log"}, status_code=500)

    @app.exception_handler(ValueError)
    def _conflict(request: Request, exc: ValueError) -> JSONResponse:
        # Raised by the repository's double-submit guards: a second tab
        # finishing the same attempt. A conflict, not a server fault.
        log.info("%s: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("%s: unhandled error", request.url.path)
        return JSONResponse({"error": "internal error — see the server log"}, status_code=500)


# --- routes ----------------------------------------------------------------


def _register(app: FastAPI) -> None:
    _register_errors(app)

    # -- config / boot state -----------------------------------------------

    @app.get("/api/config")
    def config(cfg: Cfg, date: Today) -> api.ConfigResponse:
        # Dated because the topic rotates weekly and the browser owns the
        # calendar: the topic must turn over at the user's midnight, not at
        # whatever midnight the server's timezone believes in.
        return api.ConfigResponse(topic=cfg.math.topic_for(date), tickers=cfg.markets.tickers)

    @app.get("/api/state")
    def dashboard_state(cfg: Cfg, repo: Repo, date: Today) -> api.DashboardState:
        # A GET with a side effect, deliberately. Closing out yesterday's
        # unmarked problem needs to know what "yesterday" is, and [Invariant 1]
        # says only the browser knows that -- so the reconciliation happens on
        # the first dated request after the day turns over, not on a server
        # clock. It is idempotent and a no-op on all but the first load of the
        # day, and doing it here means the rating in the payload already
        # accounts for it rather than being a figure the next reload contradicts.
        lapses = repo.reconcile_math_lapses(date)
        lapses += repo.reconcile_debate_lapses(date, cfg.debate.grace_days)
        for event in lapses:
            log.info(
                "lapsed %s %s (%s): unmarked, %s -> %s",
                event.track.value,
                event.date,
                event.ref,
                event.rating_before,
                event.rating_after,
            )
        return state.dashboard_state(cfg, repo, date)

    @app.get("/api/topic")
    def topic(cfg: Cfg, repo: Repo, date: Today) -> api.TopicResponse:
        return content.topic_info(cfg, repo, date)

    @app.get("/api/spend")
    def spend_summary(cfg: Cfg, repo: Repo, date: Today) -> api.SpendResponse:
        """Estimated LLM spend over today / 7 / 30 days, by call kind.

        Dated because the windows are counted back from today and, as
        everywhere else, only the browser knows which day that is -- a server
        in WSL's timezone would roll "today" over at the wrong hour and the
        strip would disagree with the chips beside it.

        Reads the log rather than the database, and counts problems from the
        database rather than the log. That split is the point: a problem served
        from the batch or the pool appears in one and not the other, so the two
        numbers together say what the batch saved.
        """
        entries, bad = spend.load(cfg.path("metrics"))
        window = max(days for days, _ in spend.WINDOWS)
        served = repo.math_attempt_count(date - dt.timedelta(days=window - 1), date)
        return spend.summarise(entries, bad, date, served)

    @app.get("/api/history")
    def history(cfg: Cfg, repo: Repo) -> api.HistoryResponse:
        """Per-area elo trajectories and the day-log archive.

        The one GET in this app with no `date` parameter. Nothing it returns is
        relative to today, so asking the browser for a day it would not use
        would be cargo-culting the invariant rather than honouring it.
        """
        return state.history(cfg, repo)

    # -- feeds -------------------------------------------------------------

    @app.get("/api/news")
    def news(cfg: Cfg, repo: Repo, fetch: Fetch, date: Today) -> api.NewsResponse:
        """Headlines you have not already been shown.

        Dated because the seen-window is measured in days and only the
        browser knows which day it is -- the same rule every other dated
        endpoint follows.

        Order matters: filter, *then* quota. Slicing to two politics
        headlines first and dropping the read ones second leaves one.
        """
        window = cfg.news.repeat_window_days
        # `before=date`: today's own picks are excluded from today's filter.
        # Showing a headline records it, so without that bound the second
        # request of the day filters out what the first just displayed and a
        # refresh swaps every story out from under you.
        seen = repo.seen_links(date - dt.timedelta(days=window), date) if window else set()
        got = fetch.news()
        candidates = got.value
        picks = feeds.apply_quotas([i for i in candidates if i.key not in seen], cfg.news.quotas)

        if not picks:
            # Every candidate is one you have seen. That means the feeds have
            # gone stale, not that you have read the internet, and an empty
            # panel would read as a fetch failure. Show the normal selection.
            log.info("every news candidate was already seen; showing them anyway")
            picks = feeds.apply_quotas(candidates, cfg.news.quotas)

        repo.record_seen([i.key for i in picks], date)
        if window:
            repo.prune_seen(date - dt.timedelta(days=window))
        return api.NewsResponse(items=picks, feed=_feed_meta(got))

    @app.get("/api/markets")
    def markets(fetch: Fetch) -> api.MarketsResponse:
        got = fetch.markets()
        return api.MarketsResponse(rows=got.value, feed=_feed_meta(got))

    @app.get("/api/calendar")
    def calendar(fetch: Fetch, date: Today) -> api.CalendarResponse:
        got = fetch.calendar(date)
        return got.value.model_copy(update={"feed": _feed_meta(got)})

    @app.post("/api/calendar/add")
    def calendar_add(fetch: Fetch, body: api.CalendarAddRequest) -> dict[str, str]:
        return {"out": fetch.add_calendar_event(body.text)}

    # -- math --------------------------------------------------------------

    @app.post("/api/math/new")
    def math_new(request: Request, body: api.MathNewRequest) -> StreamingResponse:
        """ndjson: `{"status":…}` lines while it runs, then the problem.

        The generation is a multi-minute agentic run with web search; the
        status lines are what keep the panel from looking hung.
        """
        cfg: Settings = request.app.state.settings
        db: Database = request.app.state.db
        return StreamingResponse(
            _ndjson(_generate(cfg, db, body.date)),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/math/hint")
    def math_hint(cfg: Cfg, repo: Repo, body: api.MathHintRequest) -> api.MathPanel:
        repo.reveal_hint(body.date)
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/finish")
    def math_finish(cfg: Cfg, repo: Repo, body: api.MathFinishRequest) -> api.MathPanel:
        # Scoring and the elo update happen in one transaction inside the
        # repository. The browser no longer does the arithmetic -- including
        # the speed bonus, which is why the minutes cross the wire raw and the
        # multiplier is never sent by the client.
        repo.finish_math(body.date, body.outcome, body.minutes, cfg.math.par_minutes)
        return state.math_panel(cfg, repo, body.date)

    @app.get("/api/math/archive")
    def math_archive(cfg: Cfg, repo: Repo, date: Today) -> api.MathArchiveResponse:
        """Past problems, and which of them can still be submitted.

        Dated for the eligibility window, which is counted in days from today
        -- and only the browser knows which day that is.
        """
        return state.math_archive(cfg, repo, date)

    @app.post("/api/math/late")
    def math_late(cfg: Cfg, repo: Repo, body: api.MathLateRequest) -> api.MathLateResponse:
        """Submit a lapsed day. Cancels the lapse and rates the result.

        A 409 for a day that is not a lapse, out of the window, or already
        submitted -- all three are `ValueError` from the repository, which is
        the conflict the client should be told about rather than a fault.
        """
        repo.finish_math_late(
            body.date, body.target, body.outcome, body.minutes, cfg.math.par_minutes
        )
        return api.MathLateResponse(
            panel=state.math_panel(cfg, repo, body.date),
            archive=state.math_archive(cfg, repo, body.date),
        )

    # -- debate ------------------------------------------------------------
    #
    # Every route here is free. The argument runs in the Claude app on a
    # subscription; the server assembles a prompt and parses a ballot, and
    # nothing in this section can reach `llm`.

    @app.get("/api/debate")
    def debate_today(cfg: Cfg, repo: Repo, date: Today) -> api.DebatePanel:
        return state.debate_panel(cfg, repo, date)

    @app.post("/api/debate/open")
    def debate_open(cfg: Cfg, repo: Repo, fetch: Fetch, body: api.DebateOpenRequest):
        """Open the day's session, freezing what it is about.

        Idempotent: `open_debate` claims the day or hands back the session that
        already has it, so a reload cannot reroll the angle or re-freeze a
        different reading list.

        The readings are frozen here rather than ranked on every render because
        the headline pool turns over through the day, and a prep list that
        changed while you were working through it is one you can never finish.
        A feed outage is not fatal -- the session opens with no readings and the
        panel says so, because the facts box and the debate are both still
        usable without them.
        """
        cfg_debate = cfg.debate
        week = cfg_debate.week_for(body.date)
        if week is None:
            raise LookupError("no debate issues are configured")
        slug, subarea = week
        issue = cfg_debate.issue(slug)
        assert issue is not None  # the rotation is built from the issue list
        angle = cfg_debate.angle_for(body.date)

        readings: list[dom.Reading] = []
        try:
            readings = debate.pick_readings(fetch.issue_news(issue).value, issue)
        except (FeedError, *feeds.TRANSPORT_ERRORS):
            log.warning("no readings for %s; opening without them", slug, exc_info=True)

        if angle is None:
            # A reading day. There is no debate to open, so the session exists
            # only to hold the readings and give the facts somewhere to hang.
            # It takes the first angle's format purely so the row's NOT NULLs
            # are satisfiable; nothing rates, so the anchor is never used.
            angle = cfg_debate.angles[0]

        side = debate.side_for(cfg_debate, body.date, angle)
        contra = side is dom.DebateSide.con
        repo.open_debate(
            dom.DebateSession(
                date=body.date,
                issue=slug,
                subarea=subarea,
                angle=angle.key,
                motion=debate.motion_for(issue, subarea),
                format=angle.format,
                side=side,
                anchor=cfg_debate.anchor_for(angle, contra=contra),
                contra=contra,
                readings=readings,
            )
        )
        return state.debate_panel(cfg, repo, body.date)

    @app.post("/api/debate/fact")
    def debate_fact(cfg: Cfg, repo: Repo, body: api.DebateFactRequest) -> api.DebatePanel:
        session = repo.debate_session(body.date)
        if session is None:
            raise LookupError(f"no debate session on {body.date}")
        repo.add_fact(
            dom.DebateFact(
                issue=session.issue,
                date=body.date,
                claim=body.claim,
                source=body.source,
                stance=body.stance,
            )
        )
        _render_dossier(cfg, repo, session.issue)
        return state.debate_panel(cfg, repo, body.date)

    @app.post("/api/debate/fact/delete")
    def debate_fact_delete(
        cfg: Cfg, repo: Repo, body: api.DebateFactDeleteRequest
    ) -> api.DebatePanel:
        removed = repo.delete_fact(body.fact_id)
        _render_dossier(cfg, repo, removed.issue)
        return state.debate_panel(cfg, repo, body.date)

    @app.post("/api/debate/position")
    def debate_position(cfg: Cfg, repo: Repo, body: api.DebatePositionRequest) -> api.DebatePanel:
        session = repo.set_debate_position(
            body.date, body.position, body.confidence, after=body.after
        )
        _render_dossier(cfg, repo, session.issue)
        return state.debate_panel(cfg, repo, body.date)

    @app.post("/api/debate/brief")
    def debate_brief(cfg: Cfg, repo: Repo, body: api.DebateBriefRequest) -> api.DebatePanel:
        """Mark the prompt as taken, which starts the grace period.

        Separate from copying the text -- the prompt is already in the panel
        payload, so this is not fetching anything. It is the point at which the
        day starts owing a ballot, and that is worth being a deliberate press
        rather than a side effect of the clipboard.
        """
        repo.brief_debate(body.date)
        return state.debate_panel(cfg, repo, body.date)

    @app.post("/api/debate/verdict")
    def debate_verdict(cfg: Cfg, repo: Repo, body: api.DebateVerdictRequest) -> api.DebatePanel:
        """Parse a pasted ballot, re-score it, and let it move the rating.

        Three steps, in this order and none of them the judge's: parse what was
        pasted, void every line that claimed a point without quoting for it,
        then score what survives out of the *full* rubric. The judge reports
        observations; what they are worth is decided here.
        """
        card = debate.apply_quote_rule(debate.parse_scorecard(body.text))
        session, event = repo.score_debate(body.date, card, debate.score_card(card))
        log.info(
            "debate %s (%s · %s): scored %.3f, %s -> %s%s",
            session.date,
            session.issue,
            session.angle,
            session.score,
            event.rating_before,
            event.rating_after,
            f", voided {card.voided}" if card.voided else "",
        )
        _render_dossier(cfg, repo, session.issue)
        return state.debate_panel(cfg, repo, body.date)

    # -- chess -------------------------------------------------------------

    @app.post("/api/chess/new")
    def chess_new(repo: Repo, fetch: Fetch, body: api.ChessNewRequest) -> api.ChessPanel:
        puzzle = fetch.chess_puzzle(repo.current_rating(Track.chess))
        repo.upsert_puzzle(puzzle)
        repo.open_chess_attempt(body.date, puzzle.id)
        return state.chess_panel(repo, body.date)

    @app.post("/api/chess/finish")
    def chess_finish(repo: Repo, body: api.ChessFinishRequest) -> api.ChessPanel:
        repo.finish_chess(body.date, body.outcome)
        return state.chess_panel(repo, body.date)

    # -- word --------------------------------------------------------------

    @app.post("/api/word/today")
    def word_today(cfg: Cfg, repo: Repo, body: api.WordTodayRequest) -> api.WordOfDay:
        existing = repo.word(body.date)
        if existing:  # already paid for today
            return existing
        return repo.upsert_word(content.word_of_day(cfg, body.date))

    @app.post("/api/word/sentences")
    def word_sentences(repo: Repo, body: api.WordSentencesRequest) -> api.WordOfDay:
        repo.set_sentences(body.date, body.sentences)
        word = repo.word(body.date)
        if word is None:
            raise LookupError(f"no word of the day on {body.date}")
        return word

    @app.post("/api/word/check")
    def word_check(cfg: Cfg, repo: Repo, body: api.WordCheckRequest) -> api.WordCheckResponse:
        grade = content.grade_sentences(cfg, body.date, body.word, body.sentences)
        repo.set_sentences(body.date, body.sentences)
        repo.set_grade(body.date, grade)
        return api.WordCheckResponse(grade=grade)

    # -- games -------------------------------------------------------------

    @app.post("/api/games/wordle")
    def games_wordle(repo: Repo, body: api.WordleLogRequest) -> api.GamesPanel:
        repo.upsert_wordle(_domain_wordle(body))
        return state.games_panel(repo, body.date)

    @app.post("/api/games/pokedoku")
    def games_pokedoku(repo: Repo, body: api.PokedokuLogRequest) -> api.GamesPanel:
        repo.upsert_pokedoku(_domain_pokedoku(body))
        return state.games_panel(repo, body.date)

    # -- theorems ----------------------------------------------------------

    @app.get("/api/theorem/today")
    def theorem_today(cfg: Cfg, repo: Repo, date: Today) -> api.TheoremsPanel:
        """Today's theorem, drawn on first ask and fixed from then on.

        A GET that writes, like `/api/papers/today` and for the same reason:
        the draw is the act of surfacing, and surfacing has to be idempotent
        per day or a reload rerolls it.

        It is also why the draw does not happen at boot. A day you never
        opened the dashboard is a day no theorem appeared -- so it does not
        age the deck, and the card that would have come up is still waiting.
        The lapse rule in `scoring` makes the same call about math problems.
        """
        _draw_theorem(cfg, repo, date)
        return state.theorems_panel(repo, date)

    @app.post("/api/theorem/add")
    def theorem_add(cfg: Cfg, repo: Repo, body: api.TheoremAddRequest) -> api.TheoremsPanel:
        repo.add_card(content.theorem_from_prompt(cfg, body.date, body.prompt))
        return state.theorems_panel(repo, body.date)

    @app.post("/api/theorem/new")
    def theorem_new(cfg: Cfg, repo: Repo, body: api.TheoremNewRequest) -> api.TheoremsPanel:
        repo.add_card(content.theorem_card(cfg, body.date))
        return state.theorems_panel(repo, body.date)

    @app.post("/api/theorem/recall")
    def theorem_recall(cfg: Cfg, repo: Repo, body: api.TheoremRecallRequest) -> api.TheoremsPanel:
        """Grade a written recall, and let it move the area rating.

        The grading happens before the transaction rather than inside it: it is
        a network call of a few seconds, and holding SQLite's write lock across
        one would block every other write in the app for its duration. The
        double-submit guard is in the UPDATE's WHERE clause, so the worst a
        concurrent second post can do is pay for a grading whose result is then
        rejected -- which costs a fraction of a cent and cannot corrupt
        anything.
        """
        drawn = repo.draw(body.date)
        if drawn is None:
            raise LookupError(f"no theorem drawn on {body.date}")
        if drawn.explained is not None:
            raise ValueError(f"the theorem drawn on {body.date} was already graded")

        card = repo.card(drawn.card_id)
        if card is None:
            raise LookupError(f"theorem card {drawn.card_id} is missing")

        grade = content.grade_theorem(cfg, body.date, card, body.explanation, body.application)

        # The grader is asked which area the theorem belongs to, but its answer
        # is checked rather than trusted -- it is a string from a model, and an
        # invented area would open a rating bucket nothing else ever writes to.
        # Falling back to matching the card's own topic, then to pooled.
        area = grade.area if grade.area in cfg.math.area_names else None
        if area is None:
            area = cfg.math.area_for_topic(card.topic)

        repo.record_recall(
            body.date,
            explanation=body.explanation,
            application=body.application,
            grade=grade,
            area=area,
        )
        return state.theorems_panel(repo, body.date)

    @app.post("/api/theorem/archive")
    def theorem_archive(repo: Repo, body: api.TheoremArchiveRequest) -> api.TheoremsPanel:
        repo.set_archived(body.card_id, body.archived)
        return state.theorems_panel(repo, body.date)

    # -- papers ------------------------------------------------------------

    @app.get("/api/papers/today")
    def papers_today(cfg: Cfg, repo: Repo, fetch: Fetch, date: Today) -> api.PapersPanel:
        """Surface the day's arXiv candidate, once per day and never twice.

        Two changes from taking `candidates[0]`. Papers already in the table
        are skipped, which is what stops a quiet day on arXiv re-serving
        yesterday's top entry -- it was surfaced twice and marked read twice
        before this. And the remainder is ranked by `interests.md`, so the
        pick is the most wanted unseen candidate rather than merely the
        newest one.
        """
        if repo.surfaced_paper(date) is None:
            seen = repo.paper_links()
            # `.value`: the reading panel shows a stored row, so it has no use
            # for the fetch's freshness — the candidate list is upstream of
            # what is on screen, not the thing on screen.
            candidates = fetch.papers(date).value
            unseen = [p for p in candidates if p.link not in seen]
            if not unseen:
                # Every candidate already triaged. Surfacing nothing is the
                # honest answer and the one that was asked for -- re-showing a
                # paper you have read is the whole complaint. The panel says
                # so; the usual cause is an over-narrow [arxiv].query.
                log.warning(
                    "all %d arXiv candidates have been surfaced before; nothing new today",
                    len(candidates),
                )
                return state.papers_panel(repo, date)

            profile = interests.load(cfg.path("interests"))
            entry = interests.rank(unseen, profile, text_of=lambda p: (p.title, p.summary or ""))[0]
            if not profile.empty:
                log.info("arXiv pick %r scored %.1f", entry.title[:60], profile.score(entry.title))

            repo.add_paper(
                api.Paper(
                    title=entry.title,
                    link=entry.link,
                    summary=entry.summary,
                    published=entry.published,
                    status=PaperStatus.surfaced,
                    surfaced_on=date,
                    added=date,
                )
            )
        return state.papers_panel(repo, date)

    @app.post("/api/paper/triage")
    def paper_triage(repo: Repo, body: api.PaperTriageRequest) -> api.PapersPanel:
        paper = repo.surfaced_paper(body.date)
        if paper is None or paper.id is None:
            raise LookupError(f"no paper surfaced on {body.date}")
        status = PaperStatus(body.mark)
        pos = repo.next_queue_pos() if status is PaperStatus.queued else None
        repo.set_paper_status(paper.id, status, pos)
        return state.papers_panel(repo, body.date)

    @app.post("/api/paper/queue")
    def paper_queue(repo: Repo, body: api.PaperQueueRequest) -> api.PapersPanel:
        # Normalised on the way in so the paste box and the arXiv feed agree
        # on what "the same paper" means, and checked before the insert so a
        # re-paste is a 409 naming the paper rather than a 500 from the new
        # UNIQUE index.
        url = feeds.normalize_link(str(body.url))
        existing = repo.paper_by_link(url)
        if existing is not None:
            raise ValueError(f"already in the list as {existing.status.value}: {existing.title}")
        repo.add_paper(
            api.Paper(
                title=body.title or url.removeprefix("https://").removeprefix("http://"),
                link=url,
                status=PaperStatus.queued,
                added=body.date,
                queue_pos=repo.next_queue_pos(),
            )
        )
        return state.papers_panel(repo, body.date)

    @app.post("/api/paper/queue/action")
    def paper_queue_action(repo: Repo, body: api.PaperQueueActionRequest) -> api.PapersPanel:
        if body.action == "up":
            repo.promote_in_queue(body.paper_id)
        elif body.action == "read":
            repo.set_paper_status(body.paper_id, PaperStatus.cleared)
        else:
            repo.set_paper_status(body.paper_id, PaperStatus.skipped)
        return state.papers_panel(repo, body.date)

    # -- health / log / completion -----------------------------------------

    @app.post("/api/health")
    def health(repo: Repo, body: api.HealthLogRequest) -> list[api.HealthSeries]:
        repo.log_health(body.metric, body.date, body.value)
        return state.health_series(repo)

    @app.post("/api/daylog")
    def daylog(repo: Repo, body: api.DayLogRequest) -> dict[str, bool]:
        repo.set_daylog(body.date, body.text)
        return {"ok": True}

    @app.post("/api/completion")
    def completion(repo: Repo, body: api.CompletionRequest) -> api.DayCompletion:
        return repo.set_completion(body.date, body.done, body.total)

    # Anything left under /api is a client bug, and it should say so in JSON.
    # Without this the catch-all mount below swallows it and StaticFiles
    # answers "405 Method Not Allowed" for a POST to a route that does not
    # exist — which reads as "wrong verb" rather than "wrong path".
    @app.api_route("/api/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"error": f"no such endpoint: /api/{rest}"}, status_code=404)

    # -- static ------------------------------------------------------------
    # Mounted last, at "/": routes match in registration order, so every
    # /api/* route above wins and this catches the rest. `html=True` serves
    # index.html for the bare "/".
    app.mount(
        "/",
        StaticAssets(directory=app.state.settings.path("static"), html=True),
        name="web",
    )


# --- helpers ---------------------------------------------------------------


def _feed_meta(got: feeds.Cached[Any]) -> api.FeedMeta:
    """The cache entry's freshness, in the shape the browser reads.

    UTC and tz-aware on purpose. A naive timestamp would be interpreted by
    `new Date(...)` as local time, which on this machine means WSL's clock
    against the browser's -- the same skew [Invariant 1] exists to keep out of
    everything else that is dated.
    """
    return api.FeedMeta(
        fetched_at=dt.datetime.fromtimestamp(got.fetched_at, dt.UTC),
        ttl_seconds=got.ttl,
        stale=got.stale,
    )


def _render_dossier(cfg: Settings, repo: Repository, slug: str) -> None:
    """Re-render one issue's markdown after anything that changed it.

    Called from four write paths rather than on a timer, so `rg` over
    `dossiers/` is never behind the database. An issue that has left the config
    is skipped: its rows stay and its rating stays, but there is no longer a
    name or a file for it, and inventing one would put a dossier on disk that
    nothing will ever update again.
    """
    issue = cfg.debate.issue(slug)
    if issue is None:
        return
    debate.write_dossier(
        cfg.path("dossiers"),
        issue,
        debate.render_dossier(issue, repo.facts(issue=slug), repo.debate_history(issue=slug)),
    )


def _domain_wordle(body: api.WordleLogRequest) -> WordleResult:
    return WordleResult(**body.model_dump())


def _domain_pokedoku(body: api.PokedokuLogRequest) -> PokedokuResult:
    return PokedokuResult(**body.model_dump())


def _draw_theorem(
    cfg: Settings, repo: Repository, date: dt.date, rng: random.Random | None = None
) -> None:
    """Pick the day's theorem, unless the day already has one.

    Two rules sit in front of the weighted sample:

    * A day that already drew keeps its card. This is the first check because
      it is the one that makes reloading safe.
    * A card that has never appeared goes first, oldest addition first. You
      added it because you just met it and want it in rotation, so making it
      wait on a coin flip is the wrong answer -- and "days since last
      appeared" has no value for a card that never has, so the alternative is
      inventing one. New cards drain in the order you added them, one a day.

    Everything after that is `scoring.draw_weight` over the active deck.
    """
    if repo.draw(date) is not None:
        return
    deck = repo.deck()
    if not deck:
        return
    last = repo.last_drawn()

    unseen = [c for c in deck if c.id not in last]
    if unseen:
        repo.record_draw(date, unseen[0].id)
        return

    weights = [
        scoring.draw_weight(
            (date - last[c.id]).days,
            cooldown=cfg.theorems.cooldown_days,
            exponent=cfg.theorems.recency_exponent,
        )
        for c in deck
    ]
    repo.record_draw(date, deck[scoring.weighted_draw(weights, rng)].id)


def _generate(cfg: Settings, db: Database, date: dt.date) -> Iterator[dict[str, Any]]:
    """Run the generation on a worker thread, yielding status events.

    The worker gets its own repository -- and so its own connection, via
    `Database` -- because it is not the request's thread.
    """
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    result: dict[str, Any] = {}

    def work() -> None:
        try:
            repo = Repository(db.conn)
            problem = content.next_problem(
                cfg, repo, date, on_status=lambda s: events.put({"status": s})
            )
            result["problem"] = problem
        except Exception as e:
            log.exception("math generation failed")
            result["error"] = str(e)
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    while (event := events.get()) is not None:
        yield event

    if "problem" in result:
        yield api.MathDoneEvent(problem=result["problem"]).model_dump(mode="json")
    else:
        yield {"error": result.get("error", "unknown error")}


def _ndjson(events: Iterator[dict[str, Any]]) -> Iterator[bytes]:
    for event in events:
        yield (json.dumps(event, default=str) + "\n").encode()


# --- entry point -----------------------------------------------------------


class PortInUse(RuntimeError):
    """The configured port already has something listening on it."""


def port_is_free(host: str, port: int) -> bool:
    """Can we bind host:port right now?

    Checked up front rather than letting uvicorn discover it, because the
    failure needs to be a sentence about the port -- not a stack trace buried
    under whatever else is on the console.

    Deliberately no SO_REUSEADDR: the question being asked is "is someone
    already listening here", and SO_REUSEADDR would let the bind succeed
    against a socket in TIME_WAIT and answer the wrong question.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def main() -> None:
    import uvicorn

    cfg = settings()
    logs.setup(
        cfg.server.log_level, cfg.root / cfg.server.log_file if cfg.server.log_file else None
    )
    host, port = cfg.server.host, cfg.server.port
    url = f"http://{host}:{port}"

    if not port_is_free(host, port):
        # Historically this was a stale pre-rewrite `python3 dashboard.py`
        # still holding the port from days earlier. The browser then opened
        # onto *that* server, so the symptom looked like a broken page rather
        # than a failed start.
        log.error(
            "%s is already in use — something else is serving this port.\n"
            "  Find it with:  ss -ltnp | grep %s\n"
            "  Stop it, or change [server].port in config.toml.",
            url,
            port,
        )
        raise SystemExit(1)

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(cfg),
            host=host,
            port=port,
            log_config=None,  # logs.setup owns the handlers
        )
    )
    if cfg.server.open_browser:
        threading.Thread(target=_open_when_ready, args=(server, url), daemon=True).start()
    server.run()


def _open_when_ready(server: Any, url: str, timeout: float = 90.0) -> None:
    """Open the tab once the server is actually accepting connections.

    This was a flat `threading.Timer(0.8, ...)`, which assumed the server
    would be up within 0.8s of `main()` starting. Importing FastAPI from a
    venv on /mnt/c takes well over ten times that, so the tab reliably opened
    before there was anything to answer it -- and the browser cached a failure
    page for a server that came up fine a moment later.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            _open(url)
            return
        if getattr(server, "should_exit", False):
            return  # startup failed; do not open a tab onto nothing
        time.sleep(0.1)
    log.warning("server did not come up within %ss; not opening a tab. Visit %s", timeout, url)


def _open(url: str) -> None:
    """Open a tab. Inside WSL the Windows browser is reached through cmd.exe."""
    windows_cmd = "/mnt/c/Windows/System32/cmd.exe"
    log.info("opening %s", url)
    try:
        if os.name != "nt" and os.path.exists(windows_cmd):
            subprocess.run([windows_cmd, "/c", "start", url], cwd="/mnt/c", capture_output=True)
        else:
            webbrowser.open(url)
    except OSError:
        log.warning("could not open a browser tab; visit %s", url)


if __name__ == "__main__":
    main()
