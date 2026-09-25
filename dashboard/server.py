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

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import os
import random
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import pydantic
from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import (
    calclass,
    content,
    feeds,
    feel,
    garmin,
    interests,
    investigate,
    logs,
    portfolio,
    scoring,
    scratch,
    spelling,
    spend,
    state,
    term,
    todo,
    trainplan,
    tricks,
    usage,
    webauthn,
)
from .config import Settings, load_env, settings
from .content import ContentError
from .db import Database, Repository
from .db.repository import ARGUMENT_AREA
from .feeds import FeedError, Feeds
from .llm import LlmError
from .models import api
from .models import domain as dom
from .models.common import DayMode, Deck, PaperStatus, Track
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
    # Both stores are per-process and in memory, so a restart re-locks the
    # portfolio. That is the intent, not a limitation: nothing about a
    # five-minute unlock should outlive the process that granted it.
    app.state.rp = webauthn.for_settings(
        cfg.server.host, cfg.server.port, cfg.portfolio.rp_id
    )
    app.state.challenges = webauthn.Challenges()
    app.state.unlocks = webauthn.Unlocks(cfg.portfolio.unlock_seconds)
    app.state.calendar_clf = calclass.CalendarClassifier(cfg.calendar.default_calendar)
    app.state.calendar_fit_lock = threading.Lock()
    # Fitted off the request path, in the background.
    #
    # The training data is a single `gcalcli agenda` over several years, which
    # measures about 8 seconds on this account -- and the alternative to this
    # thread is that the *first thing typed* after a restart pays for all of
    # it before the preview line appears. Lazy would be the wrong kind of
    # thrifty for a box whose whole promise is that it answers as you type.
    #
    # Daemon, and every failure inside is logged rather than raised: a
    # classifier that could not be fitted degrades to the keyword rules, which
    # is not a reason to hold up or take down the server.
    if cfg.calendar.warm_on_start:
        threading.Thread(
            target=_warm_calendar_model, args=(app,), name="calendar-fit", daemon=True
        ).start()
    # The feel pings. The one thread here that runs on the laptop's clock rather
    # than on a request -- see the head of `dashboard/feel.py` for why that is
    # the one place the server may ask what time it is.
    pings = feel.start(cfg, app.state.db) if cfg.feel.pings else None
    log.info("dashboard ready on http://%s:%s", cfg.server.host, cfg.server.port)
    if cfg.portfolio.enabled:
        # Logged because the failure it prevents is silent and confusing: at
        # any other origin the browser refuses the ceremony before the server
        # is involved, so the panel's only symptom is a button that does
        # nothing.
        log.info("portfolio unlock requires %s", next(iter(app.state.rp.origins)))
    try:
        yield
    finally:
        if pings is not None:
            pings.set()
        app.state.db.close()


def _seed_patterns(cfg: Settings, repo: Repository, date: dt.date) -> None:
    """Put the hand-written argument cards in the deck, once.

    Matched on name, so this is idempotent across restarts and does not rewrite
    a card you have already been graded on -- see `Repository.seed_cards`. A
    card you archive stays archived, because the row still exists.

    `date` is the browser's, not the server's, and that is why this is called
    from `/api/state` rather than from `lifespan` where it started: [Invariant
    1] says the server never asks its own clock what day it is, and `added` is
    a date on a row. There is no browser at startup, so there is no honest
    answer to "what day is it" there.

    Failure is logged and swallowed. A starter deck is a convenience, and a
    convenience that can take the boot payload down is not one.
    """
    if not cfg.investigate.patterns:
        return
    cards = [
        dom.TheoremCard(
            name=pattern.name,
            statement=pattern.statement,
            topic=pattern.topic,
            added=date,
            deck=Deck.argument,
        )
        for pattern in cfg.investigate.patterns
    ]
    try:
        added = repo.seed_cards(cards)
    except Exception:
        log.exception("could not seed the argument deck; continuing without it")
        return
    if added:
        log.info("seeded %d argument pattern(s) into the deck", added)


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

# Which kind of look at the health panel this is, and so how stale a set of
# watch readings the caller will put up with. Absent means "just read the
# table" -- which is what the phone wants, since it is on a tailnet asking a
# laptop to do something the laptop is better placed to decide the timing of.
#
# `beat` is the pulse's unattended thirty-second poll and `focus` is the pulse
# after you turned back to the window; the two floors they map to live in
# `[garmin]`. Nothing here forces a sync: both are floors, and `_claim` is
# still the only thing that decides whether a trip actually happens.
Beat = Annotated[
    Literal["beat", "focus"] | None,
    Query(description="the pulse's reason for asking, which picks the sync floor"),
]


# --- the calendar quick-add ------------------------------------------------
#
# Three pieces: `calparse` turns the line into a title and a span, `calclass`
# picks the calendar, and the fit that connects them is lazy. Lazy because the
# training data comes out of gcalcli -- a few seconds, on an account with years
# of history -- and boot is not the moment to spend that. The first preview
# pays for it, every one after is a matrix multiply.


# What the picker sends to mean "let the classifier decide". Everything else it
# sends is a calendar name, and `None` -- the picker's own default, labelled
# `auto` -- means the deterministic policy in `_target`. The two are not the
# same thing and the naming has to keep saying so: `auto` is subject, rule,
# then Homework, and never consults the model.
MODEL_PICK = "model"

# `calendar_correction.source` is a closed set in the schema, written when the
# only stages were the classifier's. The two the todo layer adds are both
# fixed decisions rather than opinions, so they file as what they are: a
# subject's own calendar is a rule, and the Homework default is a fallback.
_CORRECTION_SOURCE = {
    "rule": "rule",
    "model": "model",
    "correction": "correction",
    "fallback": "fallback",
    "subject": "rule",
    "default": "fallback",
}


def _week_start(date: dt.date, starts_on: int) -> dt.date:
    """The Monday (or Sunday) of `date`'s week."""
    return date - dt.timedelta(days=(date.weekday() - starts_on) % 7)


def _describe_change(before: list[str], after: list[str]) -> str:
    """"+Thesis, -Meals" -- what moved, for the one line the refit logs."""
    gone = [c for c in before if c not in after]
    new = [c for c in after if c not in before]
    return ", ".join([*(f"+{c}" for c in new), *(f"-{c}" for c in gone)]) or "reordered"


def _fit_calendar_model(state: Any, fetch: Feeds, repo: Repository, *, force: bool = False) -> None:
    """Fit on the account's history plus every correction.

    Failures here are logged and swallowed. The classifier is an assistant to a
    text box: with no model the rules still fire and the default still applies,
    which is worse than a fit but far better than a quick-add box that 503s
    because gcalcli was briefly unreachable.
    """
    clf: calclass.CalendarClassifier = state.calendar_clf

    # The calendar list is read *before* deciding whether to refit, and that
    # ordering is the whole of this function's robustness to calendars being
    # created, renamed or deleted. Fitting once at boot froze the set of
    # calendars the box would ever offer: a calendar made afterwards was missing
    # from the Tab cycle until a restart, and one deleted afterwards stayed in
    # the model, could still be predicted, and failed at gcalcli with "No
    # available calendar to use".
    #
    # Read *outside* the fit lock, which matters more than it looks. Every
    # keystroke in the quick-add box comes through here, and while the list is
    # usually a cached dict lookup, it is a subprocess call whenever
    # `list_cache_seconds` has passed. Holding the lock across that made one
    # slow gcalcli stall every preview behind it -- and a gcalcli that hung
    # would freeze the box for the full timeout, which is indistinguishable
    # from the feature being broken.
    try:
        writable = [c.name for c in fetch.calendars() if c.writable]
    except (FeedError, OSError) as e:
        # Keep whatever model is already fitted -- a momentarily unreachable
        # gcalcli is not a reason to forget the classifier.
        if not clf.fitted:
            clf.unavailable = f"could not read the calendar list ({e})"
            log.warning("calendar classifier: %s", clf.unavailable)
        return

    # Compared as a set: what the model is fitted against is *which* calendars
    # exist, not the order gcalcli happened to print them in. gcalcli sorts by
    # access role and nothing promises a stable order within one, so comparing
    # sequences would refit -- an eight-second history fetch -- over a reshuffle
    # that changes nothing.
    if clf.fitted and not force and set(writable) == set(clf.targets):
        return

    with state.calendar_fit_lock:
        # Re-checked now the lock is held: several previews can arrive at once
        # and only the first of them should pay for the fit.
        if clf.fitted and not force and set(writable) == set(clf.targets):
            return
        if clf.fitted and set(writable) != set(clf.targets):
            log.info(
                "calendar set changed (%s); refitting",
                _describe_change(clf.targets, writable),
            )

        try:
            samples = calclass.samples_from_events(
                fetch.calendar_history(dt.date.today()), writable
            )
        except (FeedError, OSError) as e:
            clf.unavailable = f"could not read the calendar history ({e})"
            log.warning("calendar classifier: %s", clf.unavailable)
            return

        # Corrections last so they win the dedupe inside `fit`, and heavier:
        # they are the only labels anyone gave on purpose.
        for text, chosen in repo.calendar_corrections():
            if chosen in writable:
                samples.append(calclass.Sample(text, chosen, calclass.CORRECTION_WEIGHT))
        try:
            clf.fit(samples, writable)
        except Exception:
            log.exception("calendar classifier failed to fit")
            clf.unavailable = "the classifier failed to fit; using rules only"


def _warm_calendar_model(app: FastAPI) -> None:
    try:
        _fit_calendar_model(app.state, app.state.feeds, Repository(app.state.db.conn))
    except Exception:
        log.exception("calendar classifier warm-up failed")


def _target(
    state: Any, cfg: Settings, resolved: todo.Resolved, picked: str | None
) -> tuple[str, str]:
    """Which calendar this line goes to, and how that was decided.

    Four stages, most specific first, and the ordering is the whole policy:

      the picker      an explicit choice is never second-guessed
      the subject     `[todo.subjects].calendar`, where one names its own
      the rule        `calclass.RULES` -- "test", "exam", "final" mean Tests
      the default     Homework, because a thing with a due date is homework

    The learned model is deliberately *not* in that list. It sits behind the
    picker's `auto`, and only there. On this account it is right about two
    thirds of the time, which is a fine assistant and a poor default -- and the
    thing it would be overruling is a prior that holds for nineteen lines in
    twenty. Choosing `auto` asks for the guess; not choosing it should not.
    """
    if picked:
        return picked, "chosen"
    if resolved.parsed is None:
        return cfg.todo.default_calendar, "fallback"

    # Every configured name is checked against the account before it is used,
    # the same way `calclass.RULES` are. A `[todo]` block naming a calendar
    # that has since been renamed would otherwise send gcalcli a `--calendar`
    # it cannot match, and that surfaces as a failed write rather than as the
    # misconfiguration it is.
    writable = set(state.calendar_clf.targets)

    for hit in resolved.hits:
        if hit.calendar and (not writable or hit.calendar in writable):
            return hit.calendar, "subject"

    prediction = state.calendar_clf.predict(resolved.parsed.title)
    if prediction.source == "rule":
        return prediction.calendar, "rule"
    if not writable or cfg.todo.default_calendar in writable:
        return cfg.todo.default_calendar, "default"
    return prediction.calendar, prediction.source


def _auto(state: Any, resolved: todo.Resolved, cfg: Settings) -> tuple[str, str, list]:
    """The classifier's answer, for when the picker says `auto`."""
    if resolved.parsed is None:
        return cfg.todo.default_calendar, "fallback", []
    prediction = state.calendar_clf.predict(resolved.parsed.title)
    return (
        prediction.calendar,
        prediction.source,
        [api.CalendarGuess(calendar=c, score=s) for c, s in prediction.ranked],
    )


def _resolve_line(
    state: Any, cfg: Settings, resolved: todo.Resolved, picked: str | None
) -> api.ResolvedEvent:
    """One read line -> what the box draws, and what the write will do."""
    parsed = resolved.parsed

    if resolved.undated:
        # The classifier is not consulted, and that is the point rather than an
        # optimisation: there is no event, so there is no calendar to pick, and
        # a preview naming one would promise a Google event that never arrives.
        #
        # `parsed.warning` is dropped on purpose. On one of these it is always
        # "no day found; used today", which is the thing the line just declared
        # on purpose -- printing it would be the preview arguing with the
        # sentence. What replaces it is the one piece of typing an undated line
        # genuinely throws away: a clock with no day to hang it on is not
        # stored, and silently dropping it is how you find out in a fortnight.
        warning = None
        if parsed is not None and parsed.start is not None:
            warning = "a time needs a day — the clock on this line is ignored"
        return api.ResolvedEvent(
            title=parsed.title if parsed else resolved.raw,
            date=None,
            calendar=None,
            source="todo",
            warning=warning,
            raw=resolved.raw,
            aliases=[api.AliasHit(key=h.key, name=h.name) for h in resolved.hits],
            is_todo=True,
            undated=True,
            error=resolved.error,
        )

    ranked: list[api.CalendarGuess] = []
    if picked == MODEL_PICK:
        calendar, source, ranked = _auto(state, resolved, cfg)
    else:
        calendar, source = _target(state, cfg, resolved, picked)

    return api.ResolvedEvent(
        title=parsed.title if parsed else resolved.raw,
        date=parsed.date if parsed else dt.date.today(),
        start=parsed.start.strftime("%H:%M") if parsed and parsed.start else None,
        end=parsed.end.strftime("%H:%M") if parsed and parsed.end else None,
        calendar=calendar,
        source=source,
        ranked=ranked,
        warning=(parsed.warning if parsed else None) or state.calendar_clf.unavailable,
        raw=resolved.raw,
        aliases=[api.AliasHit(key=h.key, name=h.name) for h in resolved.hits],
        is_todo=calendar in cfg.todo.todo_calendars,
        recurrence=resolved.recurrence.label() if resolved.recurrence else None,
        dates=resolved.dates(),
        error=resolved.error,
    )


def _write_line(
    cfg: Settings,
    fetch: Feeds,
    repo: Repository,
    resolved: todo.Resolved,
    line: api.ResolvedEvent,
) -> int:
    """Write one line's events, and its rows if it is work. Returns the events.

    A repeating line is N independent events and N independent rows -- see the
    `todo` table for why -- and they are written in order so that a failure
    part-way leaves the earlier weeks standing rather than rolling back work
    Google has already accepted. The row is written *after* its event, so a row
    existing is the claim that the event does.
    """
    parsed = resolved.parsed
    assert parsed is not None  # callers check `line.error` first
    series = uuid.uuid4().hex[:12] if resolved.recurrence else None
    subject = resolved.hits[0].name if resolved.hits else None
    written = 0

    if resolved.undated:
        # The one write in this file that never touches Google. `dates()` is
        # already empty for these, so the loop below would do nothing at all --
        # but "does nothing, therefore writes no event" is a thing to state
        # rather than to leave the reader to work out from two files away.
        repo.add_todo(
            raw=resolved.raw,
            title=parsed.title,
            subject=subject,
            calendar=None,
            due_date=None,
            start=None,
            end=None,
            series=None,
            event_id=None,
            event_url=None,
        )
        return 0

    for date in resolved.dates():
        when = (
            date.isoformat()
            if parsed.start is None
            else f"{date.isoformat()} {parsed.start.strftime('%H:%M')}"
        )
        event = fetch.add_calendar_event(
            calendar=line.calendar,
            title=parsed.title,
            when=when,
            duration=1 if parsed.all_day else parsed.duration_minutes,
            all_day=parsed.all_day,
        )
        written += 1
        if line.is_todo:
            repo.add_todo(
                raw=resolved.raw,
                title=parsed.title,
                subject=subject,
                calendar=line.calendar,
                due_date=date,
                start=parsed.start,
                end=parsed.end,
                series=series,
                event_id=event.event_id,
                event_url=event.url,
            )
    return written


def _read_box(
    state: Any, cfg: Settings, fetch: Feeds, repo: Repository, body: api.TodoWriteRequest
) -> list[tuple[todo.Resolved, api.ResolvedEvent]]:
    """The whole box, read. Writes nothing and never raises on bad input.

    A line that cannot be written comes back carrying its own `error` rather
    than failing the request, because the request is thirty other lines that
    are fine. The model is fitted here rather than per line -- it is only
    consulted under `auto`, but the fit is the expensive part and it is shared.
    """
    lines = todo.resolve_all(
        body.text,
        body.today or dt.date.today(),
        cfg.todo.subjects,
        default_weeks=cfg.todo.recur_weeks,
    )
    _fit_calendar_model(state, fetch, repo)
    return [(line, _resolve_line(state, cfg, line, body.calendar)) for line in lines]


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

    @app.exception_handler(investigate.InvestigateError)
    def _ballot(request: Request, exc: investigate.InvestigateError) -> JSONResponse:
        # 400, not 502: nothing upstream failed. A ballot that will not parse is
        # a paste that went wrong, and the message says which way -- so it is
        # logged at info without a traceback, like the other client mistakes.
        log.info("%s: unusable ballot: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(garmin.GarminError)
    def _garmin(request: Request, exc: garmin.GarminError) -> JSONResponse:
        # 502 and no traceback. Every case is the far end -- an expired token
        # store, a login Garmin declined, an endpoint that changed shape
        # overnight -- and the message already names the command that fixes the
        # one of them that is fixable from here.
        log.warning("%s: garmin unavailable: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(webauthn.WebAuthnError)
    def _webauthn(request: Request, exc: webauthn.WebAuthnError) -> JSONResponse:
        # 400 and no traceback. Every case here is either an expired challenge
        # or a ceremony the user abandoned, both of which are ordinary; a real
        # forgery attempt is indistinguishable from them at this layer and
        # would only fill the log with stacks from the retry loop.
        log.info("%s: unlock rejected: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    def _locked(request: Request, exc: PermissionError) -> JSONResponse:
        # 403 rather than 401: 401 makes the browser offer its own credential
        # dialog, and there is no scheme here that dialog could satisfy.
        #
        # Registered before LookupError only for readability -- but note it
        # must come before the ValueError and Exception handlers, since
        # PermissionError is an OSError and would otherwise be reported as an
        # internal error by the catch-all.
        log.info("%s: %s", request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=403)

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
        # The phase comes off the same cached read of training.toml the plan
        # panel uses, so editing that file and refreshing moves both. A clone
        # with no camp configured gets "", and the header drops the segment.
        program = trainplan.program(cfg.path("training"))
        return api.ConfigResponse(
            topic=cfg.math.topic_for(date),
            phase=program.phase if program else "",
            tickers=cfg.markets.tickers,
        )

    @app.get("/api/state")
    def dashboard_state(
        request: Request, cfg: Cfg, repo: Repo, date: Today
    ) -> api.DashboardState:
        # A GET with a side effect, deliberately. Closing out yesterday's
        # unmarked problem needs to know what "yesterday" is, and [Invariant 1]
        # says only the browser knows that -- so the reconciliation happens on
        # the first dated request after the day turns over, not on a server
        # clock. It is idempotent and a no-op on all but the first load of the
        # day, and doing it here means the rating in the payload already
        # accounts for it rather than being a figure the next reload contradicts.
        _seed_patterns(cfg, repo, date)
        # The watch, pulled in behind this response rather than inside it.
        #
        # This is the whole of the "it syncs itself" promise, and it lives here
        # for the same reason the reconciliation above does: the sync needs to
        # know what day it is and [Invariant 1] says only the browser does.
        # There is no scheduled job because the machine this runs on is asleep
        # at 4am; the first boot of the morning is the trigger that is actually
        # present when the numbers are wanted.
        #
        # Off the request path deliberately. Garmin is a stranger on the
        # internet, and a boot payload that waited for one would make the whole
        # dashboard as slow as the slowest thing it talks to. The numbers land
        # a few seconds later and are on screen at the next redraw -- which,
        # for a set of readings that change once a night, is soon enough.
        garmin.sync_in_background(
            cfg,
            request.app.state.db,
            date,
            # The short floor, because a page load is a person. The hour-long
            # one used to apply here and it was the wrong answer to the only
            # reload that matters -- the one you do *because* this morning's
            # numbers are not on the panel yet.
            floor_minutes=cfg.garmin.focus_interval_minutes,
        )
        # `carries_over` spares day one of the two-day problem: an unmarked
        # Thursday rolls into Friday instead of being charged a zero. Forgetting
        # to press "keep going tomorrow" is not abandoning a problem you were
        # meant to still be holding, and the lapse rule cannot tell those apart
        # without being handed the week's shape.
        lapses = repo.reconcile_math_lapses(date, cfg.math.carries_over)
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

    # -- the portfolio -----------------------------------------------------
    #
    # The gate is here rather than in the panel. Hiding a rendered panel
    # client-side leaves the numbers in the response body, in devtools and in
    # the back/forward cache -- it would look locked while the data had already
    # been handed over. So: `/api/portfolio` is the only route that reads the
    # positions file, and it refuses without a token minted by a verified
    # assertion.
    #
    # See `dashboard/webauthn.py` for what the fingerprint does and does not
    # prove. The short version is that Windows decides whether to accept a
    # finger, a face or the PIN, and the page cannot narrow that.

    def _challenge_from(client_data_json: str) -> str:
        """The challenge the browser says it signed.

        Read from the client's own data, which sounds like letting the caller
        mark its own homework -- and would be, if this were the check. It is
        not. `Challenges.take` is, and it accepts only a nonce this server
        issued and has not yet consumed. Taking the value from here just says
        *which* outstanding challenge to spend; a value we never issued, or one
        already spent by an earlier request, fails there.

        The alternative is a server-side "challenge currently in flight", which
        is a single slot two tabs would fight over.
        """
        try:
            data = json.loads(webauthn.b64url_decode(client_data_json))
            return str(data["challenge"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise webauthn.WebAuthnError("malformed request")

    def _unlock_token(request: Request) -> str | None:
        """The caller's token, from the Authorization header.

        A header and not a cookie, deliberately. A cookie would be attached by
        the browser to every request to this origin including the static mount,
        which puts an unlock token in the access log and makes it reachable
        from any other page that can provoke a request here. This token is held
        in one JavaScript closure and sent only where it is needed.
        """
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        return token.strip() if scheme.lower() == "bearer" and token.strip() else None

    def _require_unlock(request: Request) -> str:
        token = _unlock_token(request)
        if not request.app.state.unlocks.valid(token):
            # 403 rather than 401: 401 invites the browser's own auth dialog,
            # and there is no scheme here it could satisfy.
            raise PermissionError("locked — verify with Windows Hello first")
        return token

    @app.get("/api/portfolio/status")
    def portfolio_status(request: Request, cfg: Cfg, repo: Repo) -> api.PortfolioStatus:
        """What the locked panel draws itself from.

        Unauthenticated, and so it must carry nothing worth locking: whether a
        credential exists, whether this caller holds a live token, and where
        the page needs to be loaded from. Not the symbols, not a count.
        """
        token = _unlock_token(request)
        unlocked = request.app.state.unlocks.valid(token)
        return api.PortfolioStatus(
            enabled=cfg.portfolio.enabled,
            enrolled=bool(repo.credential_ids()),
            unlocked=unlocked,
            expires_in=request.app.state.unlocks.remaining(token) if unlocked and token else 0,
            present=cfg.path("positions").exists(),
            origin=next(iter(request.app.state.rp.origins)),
        )

    @app.post("/api/portfolio/enroll/begin")
    def portfolio_enroll_begin(request: Request, cfg: Cfg, repo: Repo) -> dict[str, Any]:
        """Options for `navigator.credentials.create()`.

        The *first* enrolment is unauthenticated, and that is a real gap worth
        naming: someone sitting at this machine before you have enrolled can
        register their own Hello credential and unlock the panel with it. There
        is no way around it that is not theatre -- any secret the server could
        demand would have to live on the same disk as the CSV, and anyone who
        can reach that disk can read the CSV directly and skip the panel.
        Enrol once, immediately, and the window closes.

        Every enrolment *after* the first requires a live unlock, so adding a
        second authenticator is something only the holder of the first can do.
        """
        if not cfg.portfolio.enabled:
            raise LookupError("the portfolio panel is switched off in config.toml")
        existing = repo.credential_ids()
        if existing:
            _require_unlock(request)
        challenge = request.app.state.challenges.issue()
        return webauthn.creation_options(
            request.app.state.rp,
            challenge,
            # Stable, and derived rather than stored: there is exactly one user
            # here, and a random handle would be one more row to keep.
            user_id=hashlib.sha256(b"daily-dashboard:portfolio").digest()[:16],
            # So Hello offers to *replace* rather than silently stacking a
            # second credential for the same authenticator.
            exclude=existing,
        )

    @app.post("/api/portfolio/enroll/finish")
    def portfolio_enroll_finish(
        request: Request, cfg: Cfg, repo: Repo, body: api.EnrollFinish
    ) -> api.PortfolioStatus:
        if not cfg.portfolio.enabled:
            raise LookupError("the portfolio panel is switched off in config.toml")
        if repo.credential_ids():
            _require_unlock(request)
        challenge = _challenge_from(body.client_data_json)
        request.app.state.challenges.take(challenge)
        registered = webauthn.verify_registration(
            credential_id=body.credential_id,
            client_data_json=body.client_data_json,
            public_key=body.public_key,
            alg=body.alg,
            challenge=challenge,
            rp=request.app.state.rp,
        )
        repo.add_credential(
            registered.credential_id, registered.public_key, registered.alg, body.date, body.label
        )
        log.info("enrolled a portfolio credential (alg %s)", registered.alg)
        return portfolio_status(request, cfg, repo)

    @app.post("/api/portfolio/unlock/begin")
    def portfolio_unlock_begin(request: Request, cfg: Cfg, repo: Repo) -> dict[str, Any]:
        if not cfg.portfolio.enabled:
            raise LookupError("the portfolio panel is switched off in config.toml")
        allow = repo.credential_ids()
        if not allow:
            raise LookupError("no authenticator is enrolled yet")
        return webauthn.request_options(
            request.app.state.rp, request.app.state.challenges.issue(), allow
        )

    @app.post("/api/portfolio/unlock/finish")
    def portfolio_unlock_finish(
        request: Request, cfg: Cfg, repo: Repo, body: api.UnlockFinish
    ) -> dict[str, Any]:
        if not cfg.portfolio.enabled:
            raise LookupError("the portfolio panel is switched off in config.toml")
        record = repo.credential(body.credential_id)
        if record is None:
            raise webauthn.WebAuthnError("that authenticator is not enrolled")
        _, public_key, alg, sign_count = record

        challenge = _challenge_from(body.client_data_json)
        request.app.state.challenges.take(challenge)
        seen = webauthn.verify_assertion(
            client_data_json=body.client_data_json,
            authenticator_data=body.authenticator_data,
            signature=body.signature,
            public_key=public_key,
            alg=alg,
            challenge=challenge,
            rp=request.app.state.rp,
            stored_sign_count=sign_count,
        )
        repo.touch_credential(body.credential_id, seen, body.date)

        token, ttl = request.app.state.unlocks.mint()
        log.info("portfolio unlocked for %ss", ttl)
        return {"token": token, "expires_in": ttl}

    @app.post("/api/portfolio/lock")
    def portfolio_lock(request: Request) -> dict[str, bool]:
        """Hand the token back early.

        Called by the panel when the countdown runs out and when the tab is
        closed, so the five minutes are usually *shorter* than five minutes
        rather than exactly it. The expiry in `Unlocks` is what guarantees the
        ceiling; this lowers it whenever the browser is still around to ask.
        """
        request.app.state.unlocks.revoke(_unlock_token(request))
        return {"locked": True}

    @app.get("/api/portfolio")
    def portfolio_holdings(request: Request, cfg: Cfg) -> api.PortfolioResponse:
        """The holdings themselves. The only route that opens the file."""
        if not cfg.portfolio.enabled:
            raise LookupError("the portfolio panel is switched off in config.toml")
        token = _require_unlock(request)
        held = portfolio.load(cfg.path("positions"))
        return api.PortfolioResponse(
            positions=[api.PortfolioPosition(**vars(p)) for p in held.positions],
            totals=api.PortfolioTotals(
                value=held.total_value,
                day_gain=held.total_day_gain,
                day_gain_pct=held.day_gain_pct,
                total_gain=held.total_gain,
                total_gain_pct=held.total_gain_pct,
            ),
            warnings=held.warnings,
            as_of=held.as_of,
            expires_in=request.app.state.unlocks.remaining(token),
        )

    @app.get("/api/calendar")
    def calendar(fetch: Fetch, cfg: Cfg, date: Today, weeks: int = 0) -> api.CalendarResponse:
        """One week of the grid. `weeks` pages, 0 being the week `date` is in."""
        start = _week_start(date, cfg.calendar.week_starts_on) + dt.timedelta(weeks=weeks)
        got = fetch.calendar(start, 7)
        return got.value.model_copy(update={"feed": _feed_meta(got)})

    @app.post("/api/calendar/retrain")
    def calendar_retrain(request: Request, fetch: Fetch, repo: Repo) -> api.ClassifierStatus:
        """Refit the quick-add classifier now, against a freshly read account.

        The model already refits on its own -- at boot, whenever a calendar is
        created or deleted, and immediately after every correction. What none of
        those do is re-read the *history*, which is cached for a day and is the
        bulk of the training data. So on a server left running, events filed
        since this morning are invisible to it, and the one thing you cannot do
        from the box is say "look again".
        """
        fetch.invalidate("calendarhistory")
        fetch.invalidate("calendarlist")
        _fit_calendar_model(request.app.state, fetch, repo, force=True)

        clf: calclass.CalendarClassifier = request.app.state.calendar_clf
        return api.ClassifierStatus(
            fitted=clf.fitted,
            samples=clf.sample_count,
            calendars=clf.targets,
            corrections=len(repo.calendar_corrections()),
            unavailable=clf.unavailable,
        )

    @app.post("/api/calendar/preview")
    def calendar_preview(
        request: Request, cfg: Cfg, fetch: Fetch, repo: Repo, body: api.TodoWriteRequest
    ) -> api.CalendarPreview:
        """What the box means. Writes nothing, on purpose.

        Called on every keystroke (debounced), so it must never be the thing
        that creates an event and must not depend on gcalcli being reachable --
        a failed model leaves the parse, which is the half that matters most.

        It returns a line per line, errors included, because that is what makes
        a pasted reading list reviewable *before* twelve events exist.
        """
        read = _read_box(request.app.state, cfg, fetch, repo, body)
        try:
            writable = [c.name for c in fetch.calendars() if c.writable]
        except feeds.FeedError:
            # The preview is not allowed to fail on the calendar list. Without
            # it the picker keeps whatever it already had, which is strictly
            # better than a box that stops previewing because gcalcli blinked.
            writable = []
        return api.CalendarPreview(lines=[line for _, line in read], calendars=writable)

    @app.post("/api/calendar/add")
    def calendar_add(
        request: Request, cfg: Cfg, fetch: Fetch, repo: Repo, body: api.TodoWriteRequest
    ) -> api.CalendarWrite:
        """Write the lines that can be written, and hand back the ones that cannot.

        Partial by design. All-or-nothing on a forty-line paste means one typo
        costs thirty-nine good lines, and the box keeps what failed with the
        error attached, so the fix is an edit rather than a retype.
        """
        read = _read_box(request.app.state, cfg, fetch, repo, body)
        if not read:
            return api.CalendarWrite()

        writable = [c.name for c in fetch.calendars() if c.writable]
        if body.calendar and body.calendar != MODEL_PICK and body.calendar not in writable:
            # Validated against the account rather than passed through: gcalcli
            # matches `--calendar` as a *regex* against every calendar summary,
            # so an unknown string does not fail cleanly -- it either matches
            # several and is refused, or matches one nobody asked for.
            raise ValueError(f"{body.calendar!r} is not a writable calendar")

        written: list[api.ResolvedEvent] = []
        failed: list[api.ResolvedEvent] = []
        done: list[tuple[todo.Resolved, api.ResolvedEvent]] = []
        events = 0

        for resolved, line in read:
            if line.error is not None or resolved.parsed is None:
                failed.append(line)
                continue
            try:
                events += _write_line(cfg, fetch, repo, resolved, line)
            except (feeds.FeedError, ValueError) as e:
                failed.append(line.model_copy(update={"error": str(e)}))
                continue
            written.append(line)
            done.append((resolved, line))

        # One correction per commit, not per line. Choosing `Tests` for a paste
        # is one decision about one paste, and recording it forty times would
        # let a single afternoon's filing outvote three years of history.
        #
        # Recorded against what the box *would* have done, which has to be
        # recomputed: `line.calendar` is already the override, so comparing the
        # two would be comparing the choice with itself.
        # The first *dated* line, not simply the first: an undated item was
        # never filed anywhere, so it cannot be evidence about where the picker
        # should have filed things.
        dated = [(r, ln) for r, ln in done if not r.undated]
        if body.calendar and body.calendar != MODEL_PICK and dated:
            resolved, line = dated[0]
            predicted, source = _target(request.app.state, cfg, resolved, None)
            if predicted != body.calendar:
                repo.record_calendar_correction(
                    text=line.title,
                    predicted=predicted,
                    chosen=body.calendar,
                    source=_CORRECTION_SOURCE.get(source, "fallback"),
                )
                # Refit now, not on the next restart: the correction exists so
                # the same phrasing stops being wrong, and "stops being wrong
                # later" is not what anyone means by that.
                _fit_calendar_model(request.app.state, fetch, repo, force=True)

        log.info("calendar add: %d lines -> %d events, %d refused",
                 len(written), events, len(failed))
        return api.CalendarWrite(written=written, failed=failed, events=events)

    # -- the todo list -----------------------------------------------------

    @app.get("/api/todo")
    def todo_list(cfg: Cfg, repo: Repo, date: Today) -> api.TodoPanel:
        return state.todo_panel(cfg, repo, date)

    @app.post("/api/todo/done")
    def todo_done(cfg: Cfg, fetch: Fetch, repo: Repo, body: api.TodoDoneRequest) -> api.TodoPanel:
        """Tick an item, and take its event off the calendar.

        In that order, and the order is the point. The tick is local and
        commits first, so it holds whether or not gcalcli answers; the delete
        is a network call whose failure is recorded against the item rather
        than raised, because an unreachable Google is not a reason to refuse to
        mark homework done. An item left with its event standing shows that
        way in the panel and can be retried.
        """
        item = repo.set_todo_done(body.todo_id, body.done)
        # An undated item has no event and never had one, so there is nothing
        # here to take down. It arrives with `event_deleted` already set and
        # would clear the guard on that alone; naming the case says why, and
        # says it beside the two null fields the call below would otherwise
        # reach for.
        undated = item.due_date is None or item.calendar is None
        if body.done and not item.event_deleted and not undated:
            try:
                fetch.delete_calendar_event(
                    calendar=item.calendar,
                    title=item.title,
                    date=item.due_date,
                    event_id=item.event_id,
                )
            except feeds.FeedError as e:
                # The one case that leaves the flag alone: either gcalcli could
                # not be reached, or the day held more than one event by that
                # name and it refused to guess. Both mean something may still
                # be on the calendar, which is what the panel then says.
                log.warning("todo %d ticked; its event stayed: %s", item.id, e)
            else:
                # Deleted, or found to be already gone. Both are "there is
                # nothing of this left in Google", which is the question the
                # flag answers -- distinguishing them would only let the panel
                # claim an event is standing when it demonstrably is not.
                repo.mark_todo_event_deleted(item.id)
        return state.todo_panel(cfg, repo, body.today or dt.date.today())

    # -- the day, for the phone --------------------------------------------

    @app.get("/api/day")
    def day(cfg: Cfg, fetch: Fetch, repo: Repo, date: Today) -> api.DayPanel:
        """What today asks for, what is on the list, and what is on the calendar.

        The phone's second screen, in one round trip. See `api.DayPanel` for why
        the whole boot payload goes rather than a summary of it.

        Side-effect free, which is the difference between this and `/api/state`
        and the reason it is a second route rather than a query parameter on the
        first. That one reconciles lapsed math attempts, seeds the pattern deck
        and kicks off a Garmin sync, all of it justified by "a page load is a
        person who wants this morning's numbers". A phone opening a screen to
        read its todo list is not that person, and a watch sync fired from a
        pocket is a surprise nobody asked for.

        The calendar is allowed to fail and the rest is not. gcalcli is a
        subprocess talking to Google and it is the slowest thing in here by an
        order of magnitude -- a cold read is three seconds against this route's
        three hundred milliseconds -- so a route that raised on it would make
        the todo list unreadable whenever the network was poor, which on this
        client is most of the time. `calendar_reached` is what carries the
        difference between "nothing on today" and "nobody could tell".
        """
        got_events: list[api.CalendarEvent] = []
        reached = False
        try:
            # Today and tomorrow. Its own cache key, separate from the week the
            # desktop grid asks for -- two entries rather than one slicing of a
            # wider window, because the grid's week starts on a Monday and this
            # one has to start today.
            got_events = list(fetch.calendar(date, 2).value.events)
            reached = True
        except feeds.FeedError as e:
            log.warning("day panel: the calendar could not be read: %s", e)

        return api.DayPanel(
            date=date,
            state=state.dashboard_state(cfg, repo, date),
            todo=state.todo_panel(cfg, repo, date),
            events=got_events,
            calendar_reached=reached,
        )

    # -- math --------------------------------------------------------------

    @app.post("/api/math/new")
    def math_new(cfg: Cfg, repo: Repo, body: api.MathNewRequest) -> api.MathPanel:
        """Serve today's problem from the batch, or the pool, or fail.

        This was an ndjson stream: the generation behind it was a multi-minute
        agentic run with web search, and the status lines were what kept the
        panel from looking hung. Nothing here is paid or slow any more -- both
        remaining sources are a local file read -- so it is an ordinary POST
        that hands back the panel, and the browser gets the problem in the
        same shape every other daily arrives in.
        """
        # A reading day draws no problem; what it "draws" is the plan, out of
        # the same overnight directory. One endpoint rather than two because
        # the panel's gesture is the same either way -- this is the button that
        # puts the day on the record.
        if cfg.math.mode_for(body.date) is DayMode.reading:
            content.import_study_plan(cfg, repo, body.date)
        else:
            content.next_problem(cfg, repo, body.date)
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/hint")
    def math_hint(cfg: Cfg, repo: Repo, body: api.MathHintRequest) -> api.MathPanel:
        """Reveal one more hint, up to what the *day* allows.

        The cap is computed here rather than in the repository, which may not
        read config, and it is the whole of the two-day barrier: on day one it
        is zero, so this call is a no-op and the hints stay out of the payload
        `math_panel` builds. A client cannot ask its way past it.
        """
        attempt = repo.math_attempt(body.date)
        problem = repo.problem(attempt.problem_id) if attempt else None
        if problem is None:
            raise LookupError(f"no problem drawn on {body.date}")
        repo.reveal_hint(body.date, content.hints_available(cfg, body.date, problem))
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/carry")
    def math_carry(cfg: Cfg, repo: Repo, body: api.MathCarryRequest) -> api.MathPanel:
        """End day one of the two-day problem with it still unsolved.

        Writes no rating event -- the result lands once, tomorrow. A 409 for a
        day that is not day one of a two-day problem, because carrying a
        Wednesday would be a day that never gets rated at all.
        """
        if not cfg.math.carries_over(body.date):
            raise ValueError(f"{body.date} is not day one of a two-day problem")
        repo.carry_math(body.date)
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/finish")
    def math_finish(cfg: Cfg, repo: Repo, body: api.MathFinishRequest) -> api.MathPanel:
        # Scoring and the elo update happen in one transaction inside the
        # repository. The browser no longer does the arithmetic -- including
        # the speed bonus, which is why the minutes cross the wire raw and the
        # multiplier is never sent by the client.
        #
        # `early_solve` is the one thing the repository cannot work out for
        # itself: whether today is day one of a two-day problem is a question
        # about [math.week]. It is asserted here and *checked* there against the
        # row -- the bonus needs a genuine day-one, unhinted solve.
        repo.finish_math(
            body.date,
            body.outcome,
            body.minutes,
            cfg.math.par_minutes,
            rubric_hit=body.rubric_hit,
            early_solve=cfg.math.carries_over(body.date),
        )
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/own")
    def math_own(cfg: Cfg, repo: Repo, body: api.MathOwnRequest) -> api.MathPanel:
        """Work a problem you brought, on one of the two days that allow it.

        The showcase day, and a day two that a day-one solve turned into a day
        off. Refused elsewhere with a 409: every other day has a problem of its
        own, and letting this replace it would make the ladder optional.

        This is the only paid call the math problem makes. It is a few hundred
        tokens on the cheap model and it fires only when you paste something --
        never on a page load, which is the property that made the old $0.15-0.80
        generation worth deleting.
        """
        mode = cfg.math.mode_for(body.date)
        free = cfg.math.is_second_day(body.date) and repo.carried_math_attempt(body.date) is None
        if mode is not DayMode.proof_showcase and not free:
            raise ValueError(
                f"{body.date} has a problem of its own; bring your own on the showcase day, "
                f"or on a day two you earned off by solving day one"
            )
        content.own_problem(cfg, repo, body.date, body.statement, body.source)
        return state.math_panel(cfg, repo, body.date)

    @app.post("/api/math/segment")
    def math_segment(cfg: Cfg, repo: Repo, body: api.StudySegmentRequest) -> api.MathPanel:
        """Tick or skip one segment of Sunday's reading plan.

        Unrated, like the whole day. What it writes that matters is the skip
        reason, which is the only thing that stops the next generation opening
        with the introduction you skipped last month.
        """
        repo.mark_segment(body.date, body.index, done=body.done, reason=body.reason)
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

    # -- investigations ----------------------------------------------------

    @app.get("/api/investigate")
    def investigate_today(cfg: Cfg, repo: Repo, date: Today) -> api.InvestigatePanel:
        return _investigate_panel(cfg, repo, date)

    @app.post("/api/investigate/add")
    def investigate_add(
        cfg: Cfg, repo: Repo, body: api.InvestigateAddRequest
    ) -> api.InvestigateAddResponse:
        """Read the whole box and add whatever is new in it.

        Never raises on a bad line. The box is the day's handout and a paste of
        six where one is a stray page number should add the five -- so every
        line comes back with its own verdict and the request itself succeeds.
        A line already in the pool is reported the same way, because pasting
        the same list twice is the ordinary way this endpoint is reached.
        """
        parsed = investigate.parse_topics(body.text)
        written = repo.add_investigations(
            [item.topic for item in parsed if item.topic is not None], body.date
        )

        lines: list[api.InvestigateLine] = []
        results = iter(written)
        for item in parsed:
            if item.topic is None:
                lines.append(api.InvestigateLine(raw=item.raw, error=item.error))
            elif next(results) is None:
                lines.append(api.InvestigateLine(raw=item.raw, error="already in the pool"))
            else:
                lines.append(api.InvestigateLine(raw=item.raw, topic=item.topic))

        return api.InvestigateAddResponse(
            panel=_investigate_panel(cfg, repo, body.date), lines=lines
        )

    @app.post("/api/investigate/draw")
    def investigate_draw(
        cfg: Cfg, repo: Repo, body: api.InvestigateDrawRequest
    ) -> api.InvestigatePanel:
        """Claim the day for one topic.

        A POST and a button, never a GET on render: drawing spends a topic, and
        a panel that drew on mount would spend one every morning the politics
        tab was painted. Idempotent all the same -- a second press returns the
        topic the first one drew, which is what makes it safe to press when you
        cannot remember whether you did.

        An empty pool is not an error. It comes back as a panel with nothing
        drawn, and the panel says so and offers the box.
        """
        repo.draw_investigation(body.date)
        return _investigate_panel(cfg, repo, body.date)

    @app.post("/api/investigate/clear")
    def investigate_clear(
        cfg: Cfg, repo: Repo, body: api.InvestigateClearRequest
    ) -> api.InvestigatePanel:
        """Strike a topic off without spending a day on it."""
        repo.clear_investigation(body.investigation_id, body.date)
        return _investigate_panel(cfg, repo, body.date)

    def _investigate_panel(
        cfg: Settings, repo: Repository, date: dt.date
    ) -> api.InvestigatePanel:
        """The panel, with the four config values it needs. Written once here
        rather than spelled out at each of the six endpoints that return it."""
        return state.investigate_panel(
            repo,
            date,
            cfg.path("investigations"),
            cfg.editor.scrollback,
            cfg.investigate.folders,
        )

    def _investigation_file(cfg: Settings, repo: Repository, date: dt.date) -> Path:
        """Today's write-up file, created if it is not there yet.

        The single place a path is derived, and it is derived from the day's
        draw rather than from anything a client sent -- both the editor and the
        read-back go through here so there is one answer to "which file", not
        two that could disagree.
        """
        day = repo.investigation_day(date)
        if day is None:
            raise LookupError(f"nothing was drawn on {date}")
        topic = repo.investigation(day.investigation_id)
        if topic is None:
            raise LookupError(f"investigation {day.investigation_id} is missing")

        root = cfg.path("investigations")
        path = investigate.file_for(root, topic.topic, topic.path)
        investigate.ensure_file(path, topic.topic, day.date)
        if topic.path is None:
            # Stored relative, so moving the whole directory -- or filing the
            # file under a topic folder later -- is a config edit and a row
            # update rather than a rewrite of every absolute path on record.
            repo.set_investigation_path(topic.id, path.relative_to(root).as_posix())
        return path

    @app.post("/api/investigate/open")
    def investigate_open(
        cfg: Cfg, repo: Repo, body: api.InvestigateOpenRequest
    ) -> api.InvestigatePanel:
        """Make sure today's file exists, before the terminal attaches to it.

        Separate from the websocket so the file is on disk -- and its path is
        on the row -- even when the editor cannot start. A missing `nvim` then
        costs you the panel's terminal and nothing else: the file is there, and
        `$EDITOR` in a real terminal still works.
        """
        _investigation_file(cfg, repo, body.date)
        return _investigate_panel(cfg, repo, body.date)

    @app.post("/api/investigate/read")
    def investigate_read(
        cfg: Cfg, repo: Repo, body: api.InvestigateReadRequest
    ) -> api.InvestigatePanel:
        """Take the file in as today's submission.

        Explicit rather than automatic on every save. What is submitted has to
        be something you decided to hand in -- the editor writes on `:w` as a
        matter of course, and an autosaved half-sentence becoming "the day's
        work" would make the snapshot meaningless.
        """
        path = _investigation_file(cfg, repo, body.date)
        text = investigate.read_file(path)
        if not investigate.body(text):
            raise ValueError("the file is still empty — write something first")
        repo.record_write_up(body.date, text)
        return _investigate_panel(cfg, repo, body.date)

    @app.post("/api/investigate/ballot")
    def investigate_ballot(
        cfg: Cfg, repo: Repo, body: api.InvestigateBallotRequest
    ) -> api.InvestigatePanel:
        """Grade the day on a pasted ballot, spend the topic, file the file.

        The order is deliberate and the filing is last. Parsing can fail, the
        classification is a network call that can fail, and the elo write is
        the one thing that must not happen twice -- so the ballot is read
        first, the folder decided second, and only then does one transaction
        close the day. A failed classification costs you the paste, not a
        double-rated afternoon.

        Filing moves the file into its folder. That is a move the server does
        make -- unlike a write, it cannot lose text, and it is the whole point
        of the folder. Nothing else in the directory is touched.
        """
        ballot = investigate.parse_ballot(body.text)
        score = investigate.score_ballot(ballot)

        day = repo.investigation_day(body.date)
        if day is None:
            raise LookupError(f"nothing was drawn on {body.date}")
        topic = repo.investigation(day.investigation_id)
        if topic is None:
            raise LookupError(f"investigation {day.investigation_id} is missing")

        root = cfg.path("investigations")
        folder, _ = content.file_investigation(
            cfg, body.date, topic.topic, investigate.known_folders(root, cfg.investigate.folders)
        )
        # Past the threshold the folder grows sub-folders rather than a
        # sixtieth flat file. Only this write-up is placed; nothing already in
        # there is moved -- see `investigate.needs_splitting`.
        sub = None
        if investigate.needs_splitting(root, folder, cfg.investigate.split_at):
            sub, _ = content.file_investigation(
                cfg,
                body.date,
                f"{topic.topic} (within {folder})",
                investigate.subfolders_of(root, folder),
            )

        _, event = repo.score_investigation(
            body.date, ballot, score, cfg.investigate.anchor, folder
        )

        if topic.path:
            moved = investigate.move_write_up(
                root / topic.path, investigate.filed_path(root, folder, sub, root / topic.path)
            )
            repo.relocate_investigation(topic.id, moved.relative_to(root).as_posix())

        log.info(
            "investigation %s scored %d/%d, filed under %s: %s -> %s",
            body.date,
            round(score * investigate.MAX_SCORE),
            investigate.MAX_SCORE,
            f"{folder}/{sub}" if sub else folder,
            event.rating_before,
            event.rating_after,
        )
        return _investigate_panel(cfg, repo, body.date)

    @app.websocket("/api/investigate/term")
    async def investigate_term(ws: WebSocket) -> None:
        """A real editor, on a real pty, in the panel.

        Which file it opens is the only thing this route decides; everything
        else about an editor socket is in `_serve_editor` below, shared with
        the theorem recall boxes. Nothing the client sends is ever interpreted
        as a path, an argument or a command -- the date in the query string
        chooses a row, and the row chooses the file.
        """

        def open_file(cfg: Settings, repo: Repository, params: Any) -> tuple[Path, Path]:
            date = dt.date.fromisoformat(params.get("date", ""))
            return _investigation_file(cfg, repo, date), cfg.path("investigations")

        await _serve_editor(ws, open_file)

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
        return repo.upsert_word(content.word_of_day(cfg, repo, body.date))

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

    # -- spelling ----------------------------------------------------------
    #
    # Free, like the investigation: five words come off a curated pool, and the only
    # work here is choosing them and comparing strings.

    @app.post("/api/spelling/new")
    def spelling_new(cfg: Cfg, repo: Repo, body: api.SpellingNewRequest) -> api.SpellingPanel:
        """Draw today's words, or hand back the ones already drawn.

        A POST rather than a GET-that-writes, unlike the theorem draw. Hearing
        a word *is* the test here, so a round that could be re-requested is one
        you could reroll until you got five you knew — and `open_spelling_round`
        makes that impossible, but the verb should say so too.
        """
        if not cfg.spelling.enabled:
            raise LookupError("the spelling bee is not configured")
        existing = repo.spelling_round(body.date)
        if existing is not None:
            return state.spelling_panel(cfg, repo, body.date)

        pool = spelling.load_pool(cfg.path("spelling"))
        if not pool:
            raise LookupError(f"no words in the spelling pool at {cfg.path('spelling')}")
        seen = {
            word: spelling.Seen(last_seen=last, missed=missed)
            for word, (last, missed) in repo.spelling_seen().items()
        }
        words = spelling.draw(pool, seen, cfg.spelling, body.date)
        if not words:
            raise LookupError("the spelling pool has no usable words for today's ladder")
        repo.open_spelling_round(body.date, words)
        return state.spelling_panel(cfg, repo, body.date)

    @app.post("/api/spelling/submit")
    def spelling_submit(cfg: Cfg, repo: Repo, body: api.SpellingSubmitRequest) -> api.SpellingPanel:
        """Mark the round and rate every word.

        Marking is server-side: the client posts what was typed and nothing
        else. The words are in its payload — they have to be, for the browser
        to say them out loud — but the *score* is not its to decide, which is
        the same split `/api/math/finish` uses.
        """
        round_ = repo.spelling_round(body.date)
        if round_ is None:
            raise LookupError(f"no spelling round on {body.date}")
        marks = spelling.mark(round_.words, body.answers)
        _, events = repo.finish_spelling(body.date, body.answers, marks)
        log.info(
            "spelling %s: %d/%d, %s -> %s",
            body.date,
            sum(marks),
            len(marks),
            events[0].rating_before if events else "-",
            events[-1].rating_after if events else "-",
        )
        return state.spelling_panel(cfg, repo, body.date)

    # -- the trick of the day ----------------------------------------------
    #
    # One generation buys the whole cell, and the marking is a string compare
    # rather than eight gradings -- see `dashboard/tricks.py`. That generation
    # is the 3am task's, so nothing on this route can spend: the only paid call
    # left in the feature is the challenge grader, and only when the challenge
    # is actually attempted.

    @app.post("/api/trick/new")
    def trick_new(cfg: Cfg, repo: Repo, body: api.TrickNewRequest) -> api.TricksPanel:
        """Draw today's trick from the overnight batch, which is its only source.

        Still a POST rather than a GET-that-writes, though the old reason is
        gone -- it used to be able to reach a paid generation, and a GET that
        spends money is one a reload can spend twice. What is left is that this
        writes: it opens the day's round. `next_trick` is idempotent per day, so
        the boot gather calling it on every page load is a no-op after the first.

        A morning with no file is a `ContentError`, which is the same 502 and
        the same shape of message the math problem gives on a night the task
        missed: what did not happen, and what to run to fix it by hand.
        """
        if not cfg.tricks.enabled:
            raise LookupError(
                "the trick of the day is not configured — see [tricks] in config.toml"
            )
        content.next_trick(cfg, repo, body.date)
        return state.tricks_panel(cfg, repo, body.date)

    @app.post("/api/trick/submit")
    def trick_submit(cfg: Cfg, repo: Repo, body: api.TrickSubmitRequest) -> api.TricksPanel:
        """Mark the set and rate it.

        Marking is server-side and the answers were never sent: the panel posts
        what was typed, and until this call returns it has not seen a single
        canonical answer. That is the difference from the spelling bee, whose
        words have to be in the payload for the browser to say them out loud --
        here there is no such bargain to strike.
        """
        round_ = repo.trick_round(body.date)
        if round_ is None:
            raise LookupError(f"no trick round on {body.date}")
        marks = tricks.mark(round_.problems, body.answers)
        anchor = cfg.tricks.set_anchor([p.band for p in tricks.marked_problems(round_.problems)])
        closed, event = repo.finish_trick(body.date, body.answers, marks, anchor)
        log.info(
            "trick %s (%s): %d/%d, %s -> %s",
            body.date,
            event.ref,
            closed.correct,
            len(marks),
            event.rating_before,
            event.rating_after,
        )
        return state.tricks_panel(cfg, repo, body.date)

    @app.post("/api/trick/challenge")
    def trick_challenge(cfg: Cfg, repo: Repo, body: api.TrickChallengeRequest) -> api.TricksPanel:
        """Grade the optional problem, and let it move the trick rating.

        The grading happens before the transaction, as `/api/theorem/recall`
        does: it is a network call of a few seconds, and holding SQLite's write
        lock across one would block every other write in the app for its
        duration. The double-submit guard is in the UPDATE's WHERE clause, so
        the worst a concurrent second post can do is pay for a grading whose
        result is then rejected.
        """
        round_ = repo.trick_round(body.date)
        if round_ is None:
            raise LookupError(f"no trick round on {body.date}")
        if round_.challenge_ok is not None:
            raise ValueError(f"the challenge on {body.date} was already graded")
        problem = tricks.challenge_of(round_.problems)
        if problem is None:
            raise LookupError(f"the set on {body.date} has no challenge problem")
        trick = repo.trick(round_.trick_id)
        if trick is None:
            raise LookupError(f"trick {round_.trick_id} is missing")

        grade = content.grade_challenge(cfg, body.date, trick, problem, body.answer)
        repo.record_challenge(
            body.date, body.answer, grade, cfg.tricks.anchor_for(tricks.CHALLENGE)
        )
        return state.tricks_panel(cfg, repo, body.date)

    @app.get("/api/trick/archive")
    def trick_archive(repo: Repo, date: Today) -> api.TrickArchiveResponse:
        """Past sets, with their answers and worked solutions.

        Dated for one reason: a challenge is answerable on the day it is set,
        so its answer is withheld on today's row and shown on every other. Only
        the browser knows which row is today's.
        """
        return state.trick_archive(repo, date)

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
        _draw_card(cfg, repo, date, Deck.math)
        return state.theorems_panel(repo, date)

    @app.post("/api/theorem/add")
    def theorem_add(cfg: Cfg, repo: Repo, body: api.TheoremAddRequest) -> api.TheoremsPanel:
        repo.add_card(content.theorem_from_prompt(cfg, body.date, body.prompt))
        return state.theorems_panel(repo, body.date)

    @app.post("/api/theorem/new")
    def theorem_new(cfg: Cfg, repo: Repo, body: api.TheoremNewRequest) -> api.TheoremsPanel:
        repo.add_card(content.theorem_card(cfg, body.date))
        return state.theorems_panel(repo, body.date)

    # The two recall boxes, written in nvim instead of in the textareas.
    #
    # Three calls, and the middle one is the websocket. `open` puts whatever is
    # in the boxes on disk, the socket forks the editor onto that file, and
    # `scratch` reads it back when the editor exits. Nothing here grades
    # anything and nothing here is submitted: the file is a form of the two
    # boxes, not a document, and `/api/theorem/recall` below is still the only
    # way an answer is handed in. That is the difference from the investigation
    # write-up, where the file *is* the work.

    def _theorem_scratch_file(cfg: Settings, repo: Repository, date: dt.date) -> tuple[Path, Any]:
        """Today's card, and the file its answer is being typed into.

        Raises if nothing was drawn. Opening an editor on a day with no
        question would give you a buffer with nowhere to submit it, which is a
        worse way to find out than a line of red text in the panel.
        """
        drawn = repo.draw(date)
        if drawn is None:
            raise LookupError(f"no theorem drawn on {date}")
        card = repo.card(drawn.card_id)
        if card is None:
            raise LookupError(f"theorem card {drawn.card_id} is missing")
        return scratch.file_for(cfg.path("scratch"), "theorem", date), card

    @app.post("/api/theorem/open")
    def theorem_open(
        cfg: Cfg, repo: Repo, body: api.TheoremOpenRequest
    ) -> api.TheoremScratch:
        """Seed the file from the boxes, before the terminal attaches to it.

        Separate from the websocket for the same reason the investigation's
        open is: the file is on disk whether or not an editor can start, so a
        machine without `nvim` loses the panel's terminal and nothing else --
        the path comes back either way and a real terminal still works.
        """
        path, card = _theorem_scratch_file(cfg, repo, body.date)
        scratch.ensure_file(
            path,
            scratch.header_for(card.name, card.topic, body.date),
            body.explanation,
            body.application,
        )
        explanation, application = scratch.read_file(path)
        return api.TheoremScratch(
            path=str(path),
            scrollback=cfg.editor.scrollback,
            explanation=explanation,
            application=application,
        )

    @app.get("/api/theorem/scratch")
    def theorem_scratch(cfg: Cfg, repo: Repo, date: Today) -> api.TheoremScratch:
        """The file, split back into the two boxes. What `:wq` hands over.

        A read, and only a read -- the panel puts what comes back in the
        textareas and the submit button is still a submit button. Writing in
        the editor is therefore never the same act as handing in, which is the
        rule the investigation's "read it back" exists to keep too.
        """
        path, _ = _theorem_scratch_file(cfg, repo, date)
        explanation, application = scratch.read_file(path)
        return api.TheoremScratch(
            path=str(path),
            scrollback=cfg.editor.scrollback,
            explanation=explanation,
            application=application,
        )

    @app.websocket("/api/theorem/term")
    async def theorem_term(ws: WebSocket) -> None:
        """nvim on today's recall buffer. See `_serve_editor`."""

        def open_file(cfg: Settings, repo: Repository, params: Any) -> tuple[Path, Path]:
            date = dt.date.fromisoformat(params.get("date", ""))
            path, card = _theorem_scratch_file(cfg, repo, date)
            # Belt and braces: the panel POSTs to `open` first and this is
            # then a no-op, but a socket that arrived on its own would
            # otherwise chdir into a directory that does not exist yet and the
            # editor would die at exec with nothing to show for it.
            scratch.ensure_file(path, scratch.header_for(card.name, card.topic, date))
            return path, cfg.path("scratch")

        await _serve_editor(ws, open_file)

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

    # -- argument patterns -------------------------------------------------
    #
    # The theorem deck's machinery pointed at a second deck. The routes are
    # separate rather than a `deck` parameter on the theorem ones because they
    # return different payloads and because a client that could pass the deck
    # in could pass the wrong one -- and the wrong one here means a pattern
    # recall moving a math area rating.

    @app.get("/api/pattern/today")
    def pattern_today(cfg: Cfg, repo: Repo, date: Today) -> api.PatternsPanel:
        """Today's pattern, drawn on first ask and fixed from then on.

        A GET that writes, exactly as `/api/theorem/today` is, and for the
        identical reason: the draw is the act of surfacing, and surfacing has
        to be idempotent per day or a reload rerolls it.
        """
        _draw_card(cfg, repo, date, Deck.argument)
        return state.patterns_panel(repo, date)

    @app.post("/api/pattern/add")
    def pattern_add(cfg: Cfg, repo: Repo, body: api.PatternAddRequest) -> api.PatternsPanel:
        """Add a card by name, generating its statement.

        A name already in the deck is a no-op rather than a second card and a
        second generation: the usual way this is reached is pressing a button
        beside a pattern the ballot named, and the same pattern gets named on
        plenty of nights.
        """
        existing = repo.card_named(body.prompt, Deck.argument)
        if existing is None:
            card = content.pattern_from_prompt(cfg, body.date, body.prompt)
            if repo.card_named(card.name, Deck.argument) is None:
                repo.add_card(card)
            else:
                # The generation resolved a phrase to a card already held --
                # "the commons thing" to "Tragedy of the commons". Paid for,
                # and worth it: the alternative is a duplicate in the rotation.
                log.info("%r resolved to %r, already in the deck", body.prompt, card.name)
        return state.patterns_panel(repo, body.date)

    @app.post("/api/pattern/recall")
    def pattern_recall(cfg: Cfg, repo: Repo, body: api.PatternRecallRequest) -> api.PatternsPanel:
        """Grade a written recall, and let it move the pattern rating.

        The grading happens before the transaction, as `/api/theorem/recall`
        does: it is a network call of a few seconds, and holding SQLite's write
        lock across one would block every other write in the app for its
        duration. The double-submit guard is in the UPDATE's WHERE clause.
        """
        drawn = repo.draw(body.date, Deck.argument)
        if drawn is None:
            raise LookupError(f"no pattern drawn on {body.date}")
        if drawn.explained is not None:
            raise ValueError(f"the pattern drawn on {body.date} was already graded")

        card = repo.card(drawn.card_id)
        if card is None:
            raise LookupError(f"pattern card {drawn.card_id} is missing")

        grade = content.grade_pattern(cfg, body.date, card, body.explanation, body.application)
        repo.record_recall(
            body.date,
            explanation=body.explanation,
            application=body.application,
            grade=grade,
            # Fixed, not chosen: a pattern applies everywhere, so there is no
            # issue it belongs to and nothing for the grader to pick.
            area=ARGUMENT_AREA,
            deck=Deck.argument,
        )
        return state.patterns_panel(repo, body.date)

    @app.post("/api/pattern/archive")
    def pattern_archive(repo: Repo, body: api.PatternArchiveRequest) -> api.PatternsPanel:
        repo.set_archived(body.card_id, body.archived)
        return state.patterns_panel(repo, body.date)

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

    # Each of these returns the whole panel rather than the row it wrote. The
    # derived block is a function of the entire history, so a session logged at
    # 6pm moves this week's load, the ratio and the monotony at once -- handing
    # back one row would leave the browser to recompute three numbers it has
    # deliberately never been taught to compute.

    @app.get("/api/health")
    def health_panel(
        cfg: Cfg, repo: Repo, date: Today, request: Request, beat: Beat = None
    ) -> api.HealthPanel:
        """The health tab's payload, on its own.

        Every write below already returns this, so the browser has never needed
        to *ask* for it -- the dashboard gets it in the boot payload and keeps
        it fresh from its own responses. The phone log at `/m` is the client
        that does need to ask: it opens on one of the five logs, and the whole
        of `/api/state` is a page of maths problems, feeds and ratings to
        redraw a list of what you have eaten today.

        The dashboard asks too, on a beat, and that is the second thing this
        route does. The panel has two sources -- rows the phone posted, which
        are in the table by the time anyone could ask for them, and rows the
        watch has not been fetched for yet -- and polling only ever covered the
        first. So the readings the phone hands to Garmin arrived on a trigger
        the dashboard no longer had: a page load. Kicking the sync from here
        gives them the same treatment as a meal, one beat late instead of one
        reload late, and costs nothing on the request -- `sync_in_background`
        is a thread, and the floor in `_claim` is what stops a poll every
        thirty seconds becoming a trip to Garmin every thirty seconds.
        """
        if beat is not None:
            garmin.sync_in_background(
                cfg,
                request.app.state.db,
                date,
                floor_minutes=(
                    cfg.garmin.focus_interval_minutes
                    if beat == "focus"
                    else cfg.garmin.min_interval_minutes
                ),
            )
        return state.health_panel(cfg, repo, date)

    @app.post("/api/health")
    def health_log(cfg: Cfg, repo: Repo, body: api.HealthLogRequest) -> api.HealthPanel:
        repo.log_health(body.metric, body.date, body.value, body.source)
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/readiness")
    def readiness(cfg: Cfg, repo: Repo, body: api.ReadinessRequest) -> api.HealthPanel:
        repo.log_readiness(
            body.date,
            body.sleep_quality,
            body.fatigue,
            body.soreness,
            body.stress,
            body.motivation,
            bed=body.bed,
            woke=body.woke,
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/session")
    def session_add(cfg: Cfg, repo: Repo, body: api.SessionRequest) -> api.HealthPanel:
        repo.add_session(
            body.date,
            body.kind,
            body.rpe,
            body.minutes,
            body.note,
            plan_key=body.plan_key,
            focus=body.focus,
            quality=body.quality,
            started=body.started,
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/session/card")
    def session_card(cfg: Cfg, repo: Repo, body: api.SessionCardRequest) -> api.HealthPanel:
        """One prescribed outing, however many rows it splits into.

        The unique index on `plan_key` is what makes a double-submitted card an
        error rather than a doubled day, and the request's own transaction is
        what stops a rejected second half leaving the first half logged.
        """
        repo.add_sessions(
            body.date,
            [(p.kind, p.rpe, p.minutes, p.note, p.plan_key) for p in body.parts],
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/session/delete")
    def session_delete(cfg: Cfg, repo: Repo, body: api.SessionDeleteRequest) -> api.HealthPanel:
        # POST rather than DELETE, and the id in the body rather than the path,
        # because the response needs `date` to rebuild the panel -- and "today"
        # is the browser's to name, never this process's. A DELETE with the id
        # in the path would have had the server reach for its own clock.
        if not repo.delete_session(body.session_id):
            raise LookupError(f"no session {body.session_id}")
        return state.health_panel(cfg, repo, body.date)

    # -- the camp stopping and starting --------------------------------------
    #
    # The two writes the health tab is allowed to make, and they are worth a
    # word because that tab took no input at all between 2026-09-01 and this.
    # The rule that emptied it holds: *logging what happened* belongs to the
    # phone, because it is something you say standing in a doorway having just
    # finished. Editing the plan is the other thing -- a decision made at a
    # desk, off the same panel that shows the camp -- and there is nowhere
    # better for it than beside the weeks it moves.

    @app.post("/api/camp/pause")
    def camp_pause(cfg: Cfg, repo: Repo, body: api.CampPauseRequest) -> api.HealthPanel:
        """Stop the camp from `date`. Refused if it is already stopped.

        Two refusals, and they are not the same one. The index refuses a second
        *open* break -- see `Repository.pause_camp`. This refuses a second break
        while an earlier one is closed but has not reached its Sunday, which the
        index cannot see and which would leave two breaks covering the same
        days: `paused_on` would answer with whichever came back first and
        `shift` would count them both. The panel never offers it, because a
        committed pause shows no pause button; an API client could.
        """
        if trainplan.paused_on(repo.camp_breaks(), body.date):
            raise ValueError("the camp is already paused")
        repo.pause_camp(body.date, body.reason)
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/camp/resume")
    def camp_resume(cfg: Cfg, repo: Repo, body: api.CampResumeRequest) -> api.HealthPanel:
        """Set the Sunday the camp comes back on, or take one back.

        The date is derived here, from the browser's `today`, rather than taken
        from the body: `next_sunday` has one answer and a field that can hold
        only one value is a field a client can only get wrong.
        """
        if body.cancel:
            if repo.cancel_resume(body.date) is None:
                raise LookupError("there is no pending resume to cancel")
        elif repo.resume_camp(trainplan.next_sunday(body.date)) is None:
            raise LookupError("the camp is not paused")
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/bathroom")
    def bathroom_add(cfg: Cfg, repo: Repo, body: api.BathroomRequest) -> api.HealthPanel:
        """One visit. The kind/field pairing is already validated by the
        request model, so anything arriving here is a row the table will take.
        """
        repo.add_bathroom(
            body.date,
            body.hour,
            body.kind,
            body.volume,
            solidity=body.solidity,
            color=body.color,
            minute=body.minute,
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/bathroom/delete")
    def bathroom_delete(cfg: Cfg, repo: Repo, body: api.LogDeleteRequest) -> api.HealthPanel:
        if not repo.delete_bathroom(body.entry_id):
            raise LookupError(f"no bathroom entry {body.entry_id}")
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/meal")
    def meal_add(cfg: Cfg, repo: Repo, body: api.MealRequest) -> api.HealthPanel:
        repo.add_meal(
            body.date,
            body.hour,
            body.size,
            body.quality,
            body.note,
            preset=body.preset,
            flags=body.flags,
            minute=body.minute,
            label=body.label,
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/meal/delete")
    def meal_delete(cfg: Cfg, repo: Repo, body: api.LogDeleteRequest) -> api.HealthPanel:
        if not repo.delete_meal(body.entry_id):
            raise LookupError(f"no meal {body.entry_id}")
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/drink")
    def drink_add(cfg: Cfg, repo: Repo, body: api.DrinkRequest) -> api.HealthPanel:
        """One drink. `amount` is in drinks; the bounds are the request
        model's and the table's, which agree."""
        repo.add_drink(
            body.date, body.hour, body.tags, body.amount, name=body.name, minute=body.minute
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/drink/delete")
    def drink_delete(cfg: Cfg, repo: Repo, body: api.LogDeleteRequest) -> api.HealthPanel:
        if not repo.delete_drink(body.entry_id):
            raise LookupError(f"no drink {body.entry_id}")
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/feel")
    def feel_add(cfg: Cfg, repo: Repo, body: api.FeelRequest) -> api.HealthPanel:
        """How you feel. The kinds are already held to their scales by the
        request model; see `FeelRequest`."""
        repo.add_feel(
            body.date,
            body.hour,
            body.energy,
            body.mood,
            minute=body.minute,
            tired=body.tired,
            low=body.low,
            symptoms=body.symptoms,
            note=body.note,
            prompted=body.prompted,
        )
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/feel/delete")
    def feel_delete(cfg: Cfg, repo: Repo, body: api.LogDeleteRequest) -> api.HealthPanel:
        if not repo.delete_feel(body.entry_id):
            raise LookupError(f"no feel {body.entry_id}")
        return state.health_panel(cfg, repo, body.date)

    @app.get("/api/push/key")
    def push_key(cfg: Cfg, repo: Repo) -> api.PushStatus:
        """What a phone subscribes against, or why it cannot. Makes the
        signing key on first ask."""
        return feel.status(cfg, repo)

    @app.post("/api/push/subscribe")
    def push_subscribe(
        cfg: Cfg, repo: Repo, body: api.PushSubscribeRequest, request: Request, date: Today
    ) -> api.PushStatus:
        """A phone saying yes to pings. Direct rather than queued on the phone:
        it is set up once, at the laptop's elbow, and it has to say at the time
        whether it worked.

        The page's own origin is kept as the contact claim the push service asks
        for -- see `feel.send_all`."""
        repo.add_push_subscription(
            body.endpoint, body.keys.p256dh, body.keys.auth, request.headers.get("origin"), date
        )
        return feel.status(cfg, repo)

    @app.post("/api/push/test")
    def push_test(cfg: Cfg, repo: Repo, body: api.PushTestRequest) -> api.PushStatus:
        """A ping now, to see that one arrives."""
        reached = feel.send_all(cfg, repo, {
            "title": "pings are on",
            "body": "this is what one looks like — tap it to open the feel log",
            "tag": "feel-test",
            "url": "./?ping=test",
        })
        return feel.status(cfg, repo).model_copy(update={"sent": reached})

    @app.post("/api/garmin/sync")
    def garmin_sync(cfg: Cfg, repo: Repo, body: api.GarminSyncRequest) -> api.HealthPanel:
        """A sync you asked for, run **on the request** rather than behind it.

        The opposite of the automatic one, on purpose. The automatic sync is
        allowed to be invisible because nobody is waiting for it; a sync you
        pressed a button for has somebody watching, and handing them a panel
        that has not changed yet -- with the numbers landing a few seconds
        later in a payload they have to reload to see -- is the button
        appearing not to work.

        Failure is a sentence, not a traceback: `GarminError` already carries
        one, and the error handler renders it into the panel.
        """
        if not cfg.garmin.enabled:
            raise ValueError("the garmin sync is off -- set [garmin].enabled in config.toml")
        wrote = garmin.sync(cfg, repo, body.date, body.days)
        garmin.record_manual(wrote)
        log.info("garmin sync (manual) wrote %d row(s) through %s", wrote, body.date)
        return state.health_panel(cfg, repo, body.date)

    @app.post("/api/usage")
    def usage_add(cfg: Cfg, body: api.UsageRequest) -> api.UsageReply:
        """The phone's screen-open counts. See `dashboard/usage.py`."""
        return api.UsageReply(usage_days=usage.merge(cfg.path("usage"), body.days))

    @app.post("/api/daylog")
    def daylog(repo: Repo, body: api.DayLogRequest) -> dict[str, bool]:
        repo.set_daylog(body.date, body.text)
        return {"ok": True}

    @app.post("/api/piano")
    def piano(repo: Repo, body: api.PianoPracticeRequest) -> dict[str, bool]:
        repo.set_piano_practice(body.date, body.practiced)
        return {"practiced": body.practiced}

    @app.post("/api/completion")
    def completion(repo: Repo, body: api.CompletionRequest) -> api.DayCompletion:
        return repo.set_completion(body.date, body.done, body.total, body.bonus)

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


async def _serve_editor(
    ws: WebSocket, open_file: Callable[[Settings, Repository, Any], tuple[Path, Path]]
) -> None:
    """One websocket, one editor, on a file the *server* picked.

    Shared by the investigation write-up and the theorem recall boxes, which
    differ in nothing but which file they open -- so `open_file` is the whole
    of the difference, and it is handed the config, a repository and the query
    string rather than anything the client could shape into a path.

    The protocol is deliberately tiny and one-directional in each shape: the
    client sends JSON (`in` for keystrokes, `resize` for a window size), and
    the server sends raw bytes straight off the pty. Nothing the client sends
    is ever interpreted as a path, an argument or a command.

    Refused from anywhere but loopback. The rest of this app is equally
    unauthenticated but exposes readings and ratings; an editor is a shell, and
    `[server].host` is a line somebody could widen without thinking about what
    is behind this one.
    """
    client = ws.client.host if ws.client else None
    if client not in webauthn.LOOPBACK and client != "testclient":
        await ws.close(code=1008, reason="the editor is loopback-only")
        return

    cfg: Settings = ws.app.state.settings
    repo = Repository(ws.app.state.db.conn)
    await ws.accept()

    try:
        path, cwd = open_file(cfg, repo, ws.query_params)
        editor = term.resolve_editor(cfg.editor.editor)
    except (ValueError, LookupError, term.TerminalError) as e:
        # Reported down the socket rather than as a close reason: the panel
        # has a terminal on screen by now, and a line of text in it is read
        # where a close code is not.
        await ws.send_bytes(f"\r\n\x1b[31m{e}\x1b[0m\r\n".encode())
        await ws.close()
        return

    session = term.Session.spawn(editor, cfg.editor.commands, path, cwd=cwd)
    log.info("editor %d opened %s", session.pid, path.name)

    loop = asyncio.get_running_loop()
    outbound: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def pump() -> None:
        """Queue -> socket. One task, so frames keep the pty's order."""
        while True:
            chunk = await outbound.get()
            if chunk is None:
                return
            await ws.send_bytes(chunk)

    async def keys() -> None:
        """Socket -> pty. The only thing that reads from the client."""
        while True:
            message = await ws.receive_json()
            kind = message.get("t")
            if kind == "in":
                session.write(str(message.get("d", "")).encode())
            elif kind == "resize":
                session.resize(int(message.get("rows", 24)), int(message.get("cols", 80)))

    tasks = [
        asyncio.create_task(term.bridge(outbound, session, loop)),
        asyncio.create_task(pump()),
        asyncio.create_task(keys()),
    ]
    try:
        # Whichever ends first ends the session: the editor quitting, the tab
        # closing, or the socket dropping. All three mean the same thing here,
        # so none of them is special-cased.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        # Off-thread: close() waits on the editor to die and would otherwise
        # stall every other request for up to `GRACE_SECONDS`.
        await loop.run_in_executor(None, session.close)
        log.info("editor %d closed", session.pid)
        with contextlib.suppress(RuntimeError):
            await ws.close()



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


def _domain_wordle(body: api.WordleLogRequest) -> WordleResult:
    return WordleResult(**body.model_dump())


def _domain_pokedoku(body: api.PokedokuLogRequest) -> PokedokuResult:
    return PokedokuResult(**body.model_dump())


def _draw_card(
    cfg: Settings,
    repo: Repository,
    date: dt.date,
    deck: Deck = Deck.math,
    rng: random.Random | None = None,
) -> None:
    """Pick the day's card from one deck, unless that deck already drew today.

    Two rules sit in front of the weighted sample:

    * A day that already drew keeps its card. This is the first check because
      it is the one that makes reloading safe.
    * A card that has never appeared goes first, oldest addition first. You
      added it because you just met it and want it in rotation, so making it
      wait on a coin flip is the wrong answer -- and "days since last
      appeared" has no value for a card that never has, so the alternative is
      inventing one. New cards drain in the order you added them, one a day.

    Everything after that is `scoring.draw_weight` over the active deck.

    `deck` is a parameter rather than two copies of this function: the argument
    deck wants the identical scheduler, and the recency curve is the one piece
    of this feature that has already been tuned once.
    """
    if repo.draw(date, deck) is not None:
        return
    cards = repo.cards(deck)
    if not cards:
        return
    last = repo.last_drawn(deck)

    unseen = [c for c in cards if c.id not in last]
    if unseen:
        repo.record_draw(date, unseen[0].id, deck)
        return

    weights = [
        scoring.draw_weight(
            (date - last[c.id]).days,
            cooldown=cfg.theorems.cooldown_days,
            exponent=cfg.theorems.recency_exponent,
        )
        for c in cards
    ]
    repo.record_draw(date, cards[scoring.weighted_draw(weights, rng)].id, deck)


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
    # Bound to `host`, but *opened* at localhost when that host is loopback.
    # WebAuthn's relying-party ID has to be a domain, and an IP literal is not
    # one -- at `http://127.0.0.1:8502` the browser refuses the Hello ceremony
    # before any of this server's code runs, so the portfolio panel simply
    # never unlocks and says nothing useful about why. `localhost` is also a
    # secure context over plain HTTP, which is the other thing WebAuthn needs
    # and an IP on http cannot supply.
    url = f"http://{'localhost' if host in webauthn.LOOPBACK else host}:{port}"

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
    # `DASHBOARD_NO_BROWSER` is for the one caller that wants everything in
    # config.toml except the tab: the logon task, which starts this at every
    # sign-in so the phone has something to send to. `open_browser = false` in
    # the file would be the obvious way to say it and is the wrong one -- it is
    # also how you launch it by hand, and a flag that turns the tab off for both
    # is a flag you turn back on and then forget about. An env var scopes the
    # difference to the caller that wants it.
    if cfg.server.open_browser and not os.environ.get("DASHBOARD_NO_BROWSER"):
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
