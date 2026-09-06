"""The watch, read once a morning and never trusted twice.

This is pass two of the health tab: the numbers a Forerunner 165 already
measures, pulled in so they do not have to be typed. Sleep, HRV, resting heart
rate, Body Battery, calories out, the day's stress average, what was sweated
out, and -- on the days there is one -- a VO2max estimate. Deliberately **not**
activities -- minutes and RPE stay hand-entered,
because session load is rpe x minutes and a duration the watch is confident
about paired with an RPE nobody answered is a load figure that is half
invented. `training_session.garmin_activity_id` is already there for the day
that changes; nothing here writes it.

Three properties this module is built around, in order of how much trouble
they save:

**Manual outranks garmin, always.** Enforced one layer down, in
`Repository.log_health`, so it holds for every writer rather than for the ones
that remembered. A hand correction survives every subsequent sync. Without it
you notice the watch scored a restless night as five hours, you fix it, and
tomorrow it is five hours again -- and after the third time you stop
correcting anything, at which point the series is the watch's opinion rather
than the truth.

**Every failure is silence, never an error.** `python-garminconnect` is an
unofficial client against a private API, and that API has already broken this
integration's predecessor once: `garth` was killed by Garmin's March 2026 auth
change. So the sync is best-effort at every level -- a missing library, an
expired token, one endpoint that changed shape overnight -- and what a failure
costs is one metric arriving absent, which every reader downstream already
renders as nothing at all. The panel says when the last sync worked and what
went wrong if it did not, because a sync quietly failing for a week is the
failure mode actually worth designing against.

**Nothing here asks what day it is.** [Invariant 1]: the browser owns "today",
including for this. The date being synced arrives from a request. The clock is
consulted for exactly one thing -- how long since the last run, for the
throttle -- and elapsed time is not a calendar.

--- credentials ---------------------------------------------------------------

`GARMIN_EMAIL` and `GARMIN_PASSWORD` in `.env`, and the password is read once,
by you, running

    ./venv.sh && $PY -m dashboard.garmin login

which does the interactive login (MFA code included, prompted on the terminal)
and writes an OAuth token to `[garmin].token_dir`. After that the password is
not needed and can come back out of `.env` if you would rather it did not sit
there -- the tokens are refreshed from themselves for about a year. The server
never prompts for anything: if the token store is missing or stale, the sync
fails with a sentence telling you to run that command again.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import logs
from .models import api
from .models.common import EntrySource, HealthMetric

log = logs.get(__name__)


class GarminError(RuntimeError):
    """Anything that stopped a sync. Always carries a sentence for the panel."""


# --- pulling the numbers out of the payloads --------------------------------
#
# Each of these takes one endpoint's response and returns `{metric: value}`.
# They are pure and they are separate from the fetching for one reason: they
# are the part most likely to break, since the shape of these payloads is
# nobody's published contract, and pure functions over recorded fixtures are
# the only way to have any test coverage of them at all.
#
# The house rule inside every one of them: a field that is missing, null, zero
# or the wrong type yields nothing, and yielding nothing is not an error. A
# night the watch was on the charger is a night with no sleep row, and that is
# the truth about it.


def _num(value: Any) -> float | None:
    """A positive float, or None for everything else.

    Zero is deliberately not a value here. `health_entry` CHECKs `value > 0`,
    and every zero these endpoints produce means "no reading" rather than
    "measured zero" -- nobody's resting heart rate is nought, and a hydration
    total of zero is a day the glyph went untapped.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _dig(payload: Any, *path: str) -> Any:
    """Walk a chain of dict keys, giving up quietly at the first thing that is
    not a dict or not there."""
    for key in path:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def sleep_metrics(payload: Any) -> dict[HealthMetric, float]:
    """Hours asleep, hours in bed, and Garmin's 0-100 score.

    Garmin dates a night by the morning it ends, which is the same convention
    the check-in uses -- so what this writes against 2026-08-27 is the night
    you woke up from on the 27th, and the box on the morning panel is already
    filled in by the time you look at it.

    Time in bed is computed from the two local timestamps rather than read from
    a field, because the field that reports it has been three different names
    across the client's own history and the timestamps have been stable.
    """
    dto = _dig(payload, "dailySleepDTO") or {}
    out: dict[HealthMetric, float] = {}

    seconds = _num(dto.get("sleepTimeSeconds"))
    if seconds:
        out[HealthMetric.sleep_hours] = round(seconds / 3600, 2)

    start, end = dto.get("sleepStartTimestampLocal"), dto.get("sleepEndTimestampLocal")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
        hours = (end - start) / 3_600_000  # milliseconds since the epoch
        # Sanity-bounded rather than trusted. A clock change or a botched
        # timestamp produces a plausible-looking number here, and "38 hours in
        # bed" would sail through every CHECK on the table.
        if 0 < hours <= 24:
            out[HealthMetric.time_in_bed] = round(hours, 2)

    score = _num(_dig(dto, "sleepScores", "overall", "value"))
    if score:
        out[HealthMetric.sleep_score] = score
    return out


def hrv_metrics(payload: Any) -> dict[HealthMetric, float]:
    """Last night's average, ms. The five-minute-window number Garmin reports
    on the Morning Report."""
    value = _num(_dig(payload, "hrvSummary", "lastNightAvg"))
    return {HealthMetric.hrv: value} if value else {}


def rhr_metrics(payload: Any) -> dict[HealthMetric, float]:
    """Resting heart rate, from the metrics-map shape this endpoint returns.

    `get_rhr_day` asks the wellness-statistics URL for a date *range* with
    `metricId=60`, and a range endpoint answers with a map of series rather
    than with a scalar. So the reading lives at
    `allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE[0].value`, and that is
    the only shape this call has been observed to produce.

    The flat `restingHeartRate` key is real, but it belongs to the daily user
    summary -- `get_stats` -- and `stats_metrics` reads it there. It is kept
    here as a fallback rather than as the lead because it costs one lookup and
    because these two endpoints have merged before; what it must not be is
    first, which is what made the live path a fallback that always ran.
    """
    entries = _dig(payload, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE")
    value = None
    if isinstance(entries, list) and entries:
        value = _num(_dig(entries[0], "value"))
    if value is None:
        value = _num(_dig(payload, "restingHeartRate"))
    return {HealthMetric.resting_hr: value} if value else {}


def stats_metrics(payload: Any) -> dict[HealthMetric, float]:
    """Calories out, Body Battery, and the day's stress average.

    `totalKilocalories` is everything -- BMR plus what you did -- which is the
    half of the energy balance the meal log cannot supply.

    Body Battery is taken as the day's *highest*, not its most recent. The
    highest is the wake value in all but pathological cases, and the wake value
    is the one worth having: read at 9pm, "most recent" measures how long your
    day was rather than how well you recovered from yesterday.

    Stress rides along in this same payload rather than needing an endpoint of
    its own -- `get_stats` is the daily user summary, and `averageStressLevel`
    has been in it as long as the rest of these have. One call fewer against an
    unofficial API is one fewer thing to break.

    Resting heart rate rides along too, and this is the endpoint that really
    carries the flat `restingHeartRate` key -- `get_rhr_day` never has. It is
    second-best and not first: the dedicated endpoint is a range query for
    metric 60 and reports the figure Garmin settled on for the day, where this
    one is the running daily summary. `read_day` keeps the first answer for a
    metric and `get_rhr_day` is ahead of this in `ENDPOINTS`, so what this line
    buys is the morning that endpoint 401s -- not a second opinion on the
    mornings it works.

    Garmin reports **-1 or -2** for a day with no valid stress reading -- the
    watch off, or too little wear time to average. `_num` refuses anything at
    or below zero, so those arrive here as no reading at all, which is what
    they are.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[HealthMetric, float] = {}
    calories = _num(payload.get("totalKilocalories"))
    if calories:
        out[HealthMetric.calories_out] = round(calories, 1)
    battery = _num(payload.get("bodyBatteryHighestValue"))
    if battery:
        out[HealthMetric.body_battery] = battery
    stress = _num(payload.get("averageStressLevel"))
    if stress:
        out[HealthMetric.stress] = stress
    resting = _num(payload.get("restingHeartRate"))
    if resting:
        out[HealthMetric.resting_hr] = resting
    return out


def hydration_metrics(payload: Any) -> dict[HealthMetric, float]:
    """What was drunk and what was sweated out, both in millilitres.

    `valueInML` is the glyph total and is no longer read by anything -- see
    `HealthMetric.hydration_ml`. It is still written, because a series stops
    being collectable the moment it stops being written and this one costs a
    dictionary lookup to keep.

    `sweatLossInML` is the reading the hydration score actually uses. The FR165
    computes it per activity and the summary totals the day, so it exists on
    days you trained and is absent on days you did not. Absent, again, rather
    than zero: a rest day did not sweat nothing, it produced no estimate.

    Zero is dropped by `_num` for both, and for the glyph that is the
    interesting case rather than an edge one -- it means the glyph went
    untapped, which is a day with no reading on it and not a day you drank
    nothing.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[HealthMetric, float] = {}
    tapped = _num(payload.get("valueInML"))
    if tapped:
        out[HealthMetric.hydration_ml] = round(tapped, 1)
    sweat = _num(payload.get("sweatLossInML"))
    if sweat:
        out[HealthMetric.sweat_loss_ml] = round(sweat, 1)
    return out


def vo2max_metrics(payload: Any) -> dict[HealthMetric, float]:
    """The running VO2max estimate, ml/kg/min.

    The only endpoint here that answers with a **list**: the max-metrics URL
    takes a date range and returns one entry per day, and this module always
    asks for a range of one. Nothing downstream cares which day an entry claims
    to be -- `read_day` is already scoped to a date and the row is written
    against it -- so the first entry that carries a number is the answer.

    `vo2MaxPreciseValue` first and `vo2MaxValue` second. The precise field is
    the same estimate to one decimal place; the plain one is it rounded to a
    whole number, and a whole-number series would show a fortnight of no change
    followed by a step, which reads as a plateau that is really a rounding.

    Absent on most days on purpose. Garmin recomputes this only after an
    outdoor run with heart rate, so a lifting week yields nothing at all and
    the gaps in the series are the truth about it -- see `HealthMetric.vo2max`.
    Under `read_day`'s house rule that costs nothing: no number, no row.
    """
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        generic = _dig(entry, "generic")
        value = _num(_dig(generic, "vo2MaxPreciseValue")) or _num(
            _dig(generic, "vo2MaxValue")
        )
        if value:
            return {HealthMetric.vo2max: round(value, 1)}
    return {}


@dataclass(frozen=True)
class Endpoint:
    """One call and the function that reads its answer.

    A table rather than a block of code because the failure being designed for
    is *one* of these changing shape or disappearing. Iterating a list means a
    dead endpoint costs its own metrics and nothing else; a straight-line
    function would have the first breakage take everything after it with it.
    """

    name: str
    """Method on the client. Looked up rather than called directly, so a client
    that has dropped one is a skip rather than an AttributeError."""

    read: Callable[[Any], dict[HealthMetric, float]]


ENDPOINTS = (
    Endpoint("get_sleep_data", sleep_metrics),
    Endpoint("get_hrv_data", hrv_metrics),
    Endpoint("get_rhr_day", rhr_metrics),
    Endpoint("get_stats", stats_metrics),
    Endpoint("get_hydration_data", hydration_metrics),
    # Last, and the one that usually answers with nothing. It is a whole extra
    # round trip for a number that changes a few times a month -- which is
    # affordable exactly because the table is a table: the cost of it being
    # here is one request, and the cost of it breaking is one metric.
    Endpoint("get_max_metrics", vo2max_metrics),
)


def read_day(client: Any, date: dt.date) -> Iterator[tuple[HealthMetric, float]]:
    """Every metric one day can offer, from whichever endpoints answered.

    Each call is isolated. A 404 on hydration must not cost you last night's
    sleep, and over an unofficial API that is not a hypothetical: these six
    endpoints have independent histories and break independently.

    Where two endpoints can answer for the same metric -- resting heart rate is
    the one, from `get_rhr_day` and from `get_stats` -- the **first** answer
    wins and the later one is dropped. `ENDPOINTS` is therefore a precedence
    order as well as a call order: the dedicated endpoint is listed ahead of
    the daily summary, so the summary is what you fall back to on the morning
    the dedicated one breaks rather than a value that quietly overwrites it.
    Yielding both would make the winner whichever came last, which is a
    property of list order that nothing states and no test would catch.
    """
    iso = date.isoformat()
    seen: set[HealthMetric] = set()
    for endpoint in ENDPOINTS:
        fetch = getattr(client, endpoint.name, None)
        if fetch is None:
            log.debug("garmin client has no %s; skipping", endpoint.name)
            continue
        try:
            payload = fetch(iso)
        # An unofficial API: any exception shape is possible here.
        except Exception as error:
            log.warning("garmin %s failed for %s: %s", endpoint.name, iso, error)
            continue
        try:
            metrics = endpoint.read(payload)
        except Exception:
            log.exception("garmin %s returned a shape %s could not read", endpoint.name, iso)
            continue
        for metric, value in metrics.items():
            if metric in seen:
                log.debug(
                    "garmin %s also offered %s for %s; keeping the earlier answer",
                    endpoint.name, metric.value, iso,
                )
                continue
            seen.add(metric)
            yield metric, value


# --- the client -------------------------------------------------------------


def installed() -> bool:
    """Whether `garminconnect` is importable in this venv.

    Lazily, like `cryptography` in webauthn.py and scikit-learn in calclass.py:
    a venv predating this feature still boots, and the panel says what to
    install rather than the server refusing to start.
    """
    try:
        import garminconnect  # noqa: F401
    except Exception:
        return False
    return True


def connect(cfg: Any) -> Any:
    """A logged-in client, resumed from the token store.

    Never interactive. The server has no terminal to prompt an MFA code at, and
    a background thread blocking forever on `input()` is a worse outcome than a
    failed sync -- so a missing or expired token store is a `GarminError`
    naming the command that fixes it, and `main()` below is the only thing in
    this codebase that ever logs in.
    """
    try:
        import garminconnect
    except Exception:
        raise GarminError(
            "garminconnect is not installed -- run: . ./venv.sh && \"$PY\" -m pip "
            "install -e '.[garmin]'"
        ) from None

    tokens = Path(cfg.garmin.token_dir).expanduser()
    if not tokens.exists():
        raise GarminError(
            f"no garmin token store at {tokens} -- run: $PY -m dashboard.garmin login"
        )
    client = garminconnect.Garmin()
    try:
        client.login(str(tokens))
    except Exception as error:
        raise GarminError(
            f"garmin login from {tokens} failed ({error}) -- "
            "run: $PY -m dashboard.garmin login"
        ) from None
    return client


# --- the sync ---------------------------------------------------------------


def sync(cfg: Any, repo: Any, today: dt.date, days: int = 3, client: Any = None) -> int:
    """Pull `days` days ending `today` and return how many rows were written.

    The window is not one day, on purpose. Garmin revises a night's sleep for
    some hours after you get up -- the score in particular -- and an activity
    synced from the watch after breakfast lands against yesterday. A window
    that only ever covered today would freeze the first draft of every night.
    Re-pulling is free: `log_health` is an upsert, and it declines to touch
    anything you have corrected by hand.

    `client` is injectable so the whole path above can be tested against a
    recorded fixture rather than against a wrist.
    """
    api_client = client if client is not None else connect(cfg)
    wrote = 0
    for offset in range(days):
        date = today - dt.timedelta(days=offset)
        for metric, value in read_day(api_client, date):
            try:
                entry = repo.log_health(metric, date, value, EntrySource.garmin)
            except Exception:
                log.exception("could not store garmin %s for %s", metric.value, date)
                continue
            # `log_health` declines rather than clobbers when a hand entry
            # holds the row, and reports the row that won. Counting only the
            # ones that changed keeps "wrote 0" meaning what it says.
            if entry is not None and entry.source is EntrySource.garmin:
                wrote += 1
    return wrote


# --- status, and the run that runs itself -----------------------------------
#
# One sync at a time, at most one every `min_interval_minutes`, kicked off by
# the boot payload and finishing after it. Process-lifetime state, like the
# feed cache and the portfolio unlocks: a restart re-syncs, which costs one
# HTTP round trip to Garmin and is not worth a table.


@dataclass
class _State:
    running: bool = False
    last_sync: dt.datetime | None = None
    last_ok: bool = True
    last_error: str | None = None
    wrote: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _State()


def status(cfg: Any) -> api.GarminStatus:
    """What the panel says about the watch."""
    with _state.lock:
        return api.GarminStatus(
            installed=installed(),
            configured=bool(cfg.garmin.enabled and os.environ.get("GARMIN_EMAIL")),
            last_sync=_state.last_sync,
            last_ok=_state.last_ok,
            last_error=_state.last_error,
            wrote=_state.wrote,
        )


def record_manual(wrote: int) -> None:
    """Log a hand-pressed sync into the same status the panel reads.

    The manual path runs on the request rather than through `_claim`, so it
    would otherwise leave the strip claiming the last sync was whenever the
    automatic one last managed to run -- which, on the morning you pressed the
    button *because* the numbers were missing, is precisely the wrong thing to
    say.
    """
    _record(True, wrote, None)


def _record(ok: bool, wrote: int, error: str | None) -> None:
    with _state.lock:
        _state.running = False
        _state.last_sync = dt.datetime.now()
        _state.last_ok = ok
        _state.last_error = error
        _state.wrote = wrote


def _claim(cfg: Any, force: bool, floor_minutes: int | None = None) -> bool:
    """Whether this caller gets to run the sync.

    The clock here is the server's, and that is not a breach of [Invariant 1]:
    what is being measured is minutes since the last run, and an interval is
    not a date. Nothing in this function decides what day it is.

    `floor_minutes` is how long this particular caller is willing to accept a
    stale answer for, and it is the caller's to say rather than a constant here
    because the two callers mean different things by it. See
    `[garmin].focus_interval_minutes`: a thirty-second poll on an unwatched tab
    and a person reloading the page because the numbers are missing are not the
    same request, and giving them one floor meant the second one got the first
    one's answer -- which is the whole reason the watch used to take two
    reloads and a wait to turn up.
    """
    with _state.lock:
        if _state.running:
            return False
        if not force and _state.last_sync is not None:
            floor = cfg.garmin.min_interval_minutes if floor_minutes is None else floor_minutes
            elapsed = (dt.datetime.now() - _state.last_sync).total_seconds()
            if elapsed < floor * 60:
                return False
        _state.running = True
        return True


def sync_in_background(
    cfg: Any,
    db: Any,
    today: dt.date,
    *,
    force: bool = False,
    floor_minutes: int | None = None,
) -> bool:
    """Kick off a sync off the request path. True if one actually started.

    Called from the boot payload and from the pulse's poll of `/api/health`,
    which between them are what make this automatic: opening the dashboard is a
    trigger, turning back to its window is a trigger, and a tab left open goes
    and looks on its own every `min_interval_minutes`. There is no scheduler
    and no daemon, deliberately -- a machine that is asleep at 4am cannot run a
    cron job, and this one is asleep at 4am.

    The poll being a trigger is what closes the last gap. This starting a
    thread does not put anything on screen; the rows land a few seconds later
    and the *next* poll is what adopts them. Before the poll could start one,
    the only thing that could was a page load -- so the reload that kicked the
    sync off always rendered before it finished, and seeing the numbers took a
    second reload. Now the same beat that would have shown them is the one that
    went and got them.

    Daemon thread, own connection (`Database.conn` is thread-local), every
    exception caught and recorded. Nothing in here can take a request down.
    """
    if not cfg.garmin.enabled:
        return False
    if not force and not cfg.garmin.auto:
        return False
    if not _claim(cfg, force, floor_minutes):
        return False

    def run() -> None:
        from .db import Repository

        try:
            wrote = sync(cfg, Repository(db.conn), today, cfg.garmin.days)
        except GarminError as error:
            log.warning("garmin sync: %s", error)
            _record(False, 0, str(error))
        except Exception as error:
            log.exception("garmin sync failed")
            _record(False, 0, f"{type(error).__name__}: {error}")
        else:
            if wrote:
                log.info("garmin sync wrote %d row(s) through %s", wrote, today)
            _record(True, wrote, None)

    threading.Thread(target=run, name="garmin-sync", daemon=True).start()
    return True


# --- the one interactive path -----------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`$PY -m dashboard.garmin login|sync`.

    `login` is the only thing in this codebase that types a password at Garmin,
    and it is run by hand because it has to be: the MFA code arrives on your
    phone and there is nobody at the server's terminal to enter it. It writes
    the token store the server then reuses without ever seeing a credential.
    """
    import argparse

    from .config import load_env, load_settings
    from .db import Database, Repository

    parser = argparse.ArgumentParser(prog="dashboard.garmin", description=__doc__)
    parser.add_argument("command", choices=["login", "sync", "status"])
    parser.add_argument("--date", help="the day to sync through (default: today, locally)")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args(argv)

    cfg = load_settings()
    load_env(cfg.root / ".env")
    logs.setup("INFO", None)

    if args.command == "login":
        try:
            import garminconnect
        except Exception:
            print(
                "garminconnect is not installed. from the repo root:\n"
                "  . ./venv.sh && \"$PY\" -m pip install -e '.[garmin]'"
            )
            return 1
        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not email or not password:
            print(
                "set GARMIN_EMAIL and GARMIN_PASSWORD in .env first.\n"
                "they are read once, here, to mint the token store; after that\n"
                "the server logs in from the tokens and the password can come out."
            )
            return 1
        tokens = Path(cfg.garmin.token_dir).expanduser()
        # `prompt_mfa` is the library's hook for the six digits. Passing
        # `input` is what makes this command interactive and the server not.
        client = garminconnect.Garmin(
            email=email, password=password, prompt_mfa=lambda: input("MFA code: ").strip()
        )
        tokens.mkdir(parents=True, exist_ok=True)
        # Handed the path, `login` writes the token store itself. It used to be
        # a bare `login()` and a separate `client.garth.dump(...)`; garth is not
        # on the client public surface any more, and the dump it carried moved
        # inside `login` along with it.
        client.login(str(tokens))
        print(f"tokens written to {tokens}. the server will use them from here.")
        return 0

    if args.command == "status":
        state = status(cfg)
        print(state.model_dump_json(indent=2))
        return 0

    # `sync`, by hand -- the CLI is the one caller allowed to name its own
    # date, because there is no browser on the other end of it to ask.
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    db = Database(cfg.path("db"))
    try:
        wrote = sync(cfg, Repository(db.conn), date, args.days)
    except GarminError as error:
        print(str(error))
        return 1
    finally:
        db.close()
    print(f"wrote {wrote} row(s) through {date}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
