"""The feel log's working parts: the pings, and what came before a feeling.

The log itself is ordinary -- `feel_log`, a request model, a row on the phone.
What lives here is everything around it that the other logs never needed:

1. **The pings.** Twice a day, at a minute drawn fresh each morning inside a
   window, the laptop pushes a notification to the phone. This is the one
   place in the server that reads its own clock: a ping has to go out with
   nobody's browser open, so there is no browser to ask what time it is
   ([Invariant 1] is suspended here, and only here). The laptop and the phone
   share a timezone, which is what makes that honest.

2. **What came before.** For the day's latest feeling that says something is
   off, every signal the dashboard already keeps -- load, the watch's night,
   the meal and drink logs -- measured in the hours before it against your own
   normal, and ranked. Each word has suspects chosen ahead of time (thirst
   suspects the fluid log; malaise suspects resting heart rate and the load);
   `other` has none, and is ranked on everything.

3. **Two sentences.** The flag line on the health panel, which was a
   placeholder until this, and one line under the load zone saying how the
   week's load is landing.

Why pings at all, and why random: a log that is only written when something is
wrong has no baseline to be wrong against, so every correlation it produces
says "bad days follow bad days". The pings collect the fine rows. The minute is
random so it cannot be braced for, which is the standard design for this kind
of sampling (Shiffman, Stone & Hufford 2008); logging on your own when
something happens is the other half of the same design.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import random
import statistics
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import health
from .models import api
from .models import domain as d
from .models.common import SICK, Symptom, Tired, clock_minutes

log = logging.getLogger(__name__)

GRACE_MIN = 60
"""How long after a window closes a feel still answers it. A ping drawn for
7:58pm and answered at 8:20 was answered; a window's end is when the ping stops
going out, not when the question stops being worth answering."""

ANSWER_LEAD_MIN = 15
"""A ping is never drawn inside the last quarter hour of its window, so there
is always time to answer it before the window is over."""

PERIOD_S = 60
"""How often the scheduler looks at the clock. A ping a minute late is on time."""

TTL_S = 3600
"""How long the push service holds a ping for a phone that is off. An hour,
because a ping that arrives after its window is a ping about a different part
of the day."""

MIN_FLAGS = 8
"""Flags of one word before the lookback switches from a story to counts. With
fewer, a count is an anecdote with a denominator."""

COUNT_DAYS = 90
"""How far back the counts look. A season -- long enough for a rare word to
reach `MIN_FLAGS`, short enough that the camp it describes is this one."""

OFF_DEV = 1.0
"""Where a signal counts as "off" for the counts: the same line the table draws
as worth a look."""


# --- the pings ---------------------------------------------------------------


def _mod(hour: int, minute: int | None) -> int:
    """Minutes past midnight; a remembered hour taken at its half."""
    return hour * 60 + (minute if minute is not None else 30)


def answers(entry: d.FeelLog, start: int, end: int) -> bool:
    """Whether this feel answers the window `[start, end]`.

    An exact time has to fall inside the window or its grace. A remembered
    hour answers if any minute of that hour does -- the 8pm row logged against a
    7:58 ping is an answer, and a rule that took it at 8:30 would call it one
    that came too late.
    """
    if entry.minute is not None:
        at = entry.hour * 60 + entry.minute
        return start <= at <= end + GRACE_MIN
    return entry.hour * 60 <= end + GRACE_MIN and entry.hour * 60 + 59 >= start


def draw_dues(windows: Sequence[Any], rng: random.Random) -> list[str]:
    """A minute in each window, uniformly, short of its last quarter hour."""
    dues = []
    for window in windows:
        start, end = window.span
        at = rng.randint(start, max(start, end - ANSWER_LEAD_MIN))
        dues.append(f"{at // 60:02d}:{at % 60:02d}")
    return dues


def ping_states(
    windows: Sequence[Any],
    drawn: dict[int, tuple[str, str | None]],
    feels_today: Sequence[d.FeelLog],
    now_minute: int | None,
) -> list[api.FeelPing]:
    """Each window's ping and what became of it -- see `api.FeelPing`.

    `now_minute` is None for a day that is over, so every window in it has
    closed. The drawn minute itself is never sent to the panel: it is only a
    secret if nobody can read it.
    """
    out = []
    for slot, window in enumerate(windows):
        start, end = window.span
        sent = drawn.get(slot, (None, None))[1]
        if any(answers(entry, start, end) for entry in feels_today):
            state = "answered"
        elif now_minute is not None and now_minute <= end + GRACE_MIN:
            state = "sent" if sent else "waiting"
        else:
            state = "missed"
        out.append(
            api.FeelPing(
                slot=slot, name=window.name, start=window.start, end=window.end,
                state=state, sent=sent,
            )
        )
    return out


def message(window: Any, date: dt.date, slot: int) -> dict[str, str]:
    """What a ping says. `url` is where tapping it lands: the phone app, on the
    feel tab, knowing which ping it is answering."""
    return {
        "title": "how do you feel?",
        "body": f"the {window.name} check — energy and mood, two taps",
        "tag": f"feel-{date.isoformat()}-{slot}",
        "url": f"./?ping={date.isoformat()}.{slot}",
    }


Sender = Callable[[Any, Any, dict[str, str]], int]


def tick(cfg: Any, repo: Any, now: dt.datetime, send: Sender | None = None) -> int:
    """One look at the clock: draw today's minutes if they are not drawn, and
    send any ping whose minute has come. Returns how many went out.

    A ping is skipped outright when a feel already answers its window -- you
    have said how you are, and being asked again is the nag this was built not
    to be. It is marked sent even when no phone is subscribed, because the
    dashboard's nudge is the other place it shows up.
    """
    windows = cfg.feel.windows
    today = now.date()
    minute = now.hour * 60 + now.minute
    drawn = repo.feel_pings(today)
    if len(drawn) < len(windows):
        drawn = repo.draw_pings(today, draw_dues(windows, random.Random()))
    feels = repo.feel_on(today)
    fired = 0
    for slot, window in enumerate(windows):
        start, end = window.span
        due, sent = drawn[slot]
        if sent is not None or any(answers(entry, start, end) for entry in feels):
            continue
        if clock_minutes(due) <= minute <= end:
            reached = (send or send_all)(cfg, repo, message(window, today, slot))
            repo.mark_ping_sent(today, slot, f"{now:%H:%M}")
            log.info("feel ping %s sent to %d phone(s)", window.name, reached)
            fired += 1
    return fired


def start(cfg: Any, db: Any) -> threading.Event:
    """The scheduler: a daemon thread that ticks every `PERIOD_S` until the
    event it returns is set. Its own connection, like the garmin sync's, and
    every failure logged rather than raised -- a ping that could not go out is
    a nudge on the dashboard, never a server that fell over."""
    stop = threading.Event()

    def run() -> None:
        from .db import Repository

        while True:
            try:
                tick(cfg, Repository(db.conn), dt.datetime.now())
            except Exception:
                log.exception("feel ping tick failed")
            if stop.wait(PERIOD_S):
                return

    threading.Thread(target=run, name="feel-pings", daemon=True).start()
    return stop


# --- web push ------------------------------------------------------------------


def _vapid(cfg: Any) -> Any | None:
    """The signing key, made on first use. None when `pywebpush` (which brings
    `py_vapid`) is not installed -- the `[push]` extra."""
    try:
        from py_vapid import Vapid
    except ImportError:
        return None
    path = cfg.path("push_key")
    if path.exists():
        return Vapid.from_file(str(path))
    key = Vapid()
    key.generate_keys()
    key.save_key(str(path))
    os.chmod(path, 0o600)
    return key


def status(cfg: Any, repo: Any) -> api.PushStatus:
    """What the phone needs to subscribe, or why it cannot."""
    count = len(repo.push_subscriptions())
    if not cfg.feel.pings:
        return api.PushStatus(reason="pings are off in config.toml", subscriptions=count)
    key = _vapid(cfg)
    if key is None:
        return api.PushStatus(
            reason="pywebpush is not installed on the laptop", subscriptions=count
        )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = key.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return api.PushStatus(
        public_key=base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        subscriptions=count,
    )


def send_all(cfg: Any, repo: Any, body: dict[str, str]) -> int:
    """Push `body` to every subscribed phone. Returns how many took it.

    A phone the push service says is gone -- 404 or 410 -- is dropped, which
    is how an uninstalled app stops being tried. The contact claim is the
    app's own https address: Apple refuses a push without one, and that is the
    honest one to give.
    """
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("feel ping not pushed: pywebpush is not installed")
        return 0
    if _vapid(cfg) is None:
        return 0
    key = str(cfg.path("push_key"))
    reached = 0
    for sub in repo.push_subscriptions():
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(body),
                vapid_private_key=key,
                # A fresh dict each time: `webpush` writes `aud` and `exp` into
                # the one it is given.
                vapid_claims={"sub": sub["origin"] or "https://localhost"},
                ttl=TTL_S,
                timeout=10,
            )
            reached += 1
        except WebPushException as error:
            code = getattr(error.response, "status_code", None)
            if code in (404, 410):
                repo.drop_push_subscription(sub["endpoint"])
                log.info("dropped a push subscription the service says is gone")
            else:
                log.warning("feel ping push failed (%s): %s", code, error)
        except Exception:
            log.exception("feel ping push failed")
    return reached


# --- what came before ------------------------------------------------------------


@dataclass
class Context:
    """Everything a lookback reads, handed over once. Series are `(date,
    value)` pairs, as `health.py` takes them."""

    sessions: Sequence[d.TrainingSession] = ()
    readiness: Sequence[d.Readiness] = ()
    hrv: Sequence[tuple[dt.date, float]] = ()
    rhr: Sequence[tuple[dt.date, float]] = ()
    sleep: Sequence[tuple[dt.date, float]] = ()
    sleep_score: Sequence[tuple[dt.date, float]] = ()
    battery: Sequence[tuple[dt.date, float]] = ()
    stress: Sequence[tuple[dt.date, float]] = ()
    sweat: Sequence[tuple[dt.date, float]] = ()
    meals: Sequence[d.MealLog] = ()
    drinks: Sequence[d.DrinkLog] = ()
    visits: Sequence[d.BathroomLog] = ()
    loads: dict[dt.date, float] = field(default_factory=dict)

    def load_window(self, end: dt.date) -> None:
        """Daily loads for the load signals: far enough back that the oldest
        feel the counts look at still has four weeks of normal behind it."""
        start = end - dt.timedelta(days=COUNT_DAYS + health.CHRONIC_DAYS + 7)
        self.loads = health.daily_load(self.sessions, start, end)


def _tone(dev: float) -> str:
    return "hi" if dev >= 2 else "mid" if dev >= OFF_DEV else "lo"


def _signal(key: str, label: str, value: str, normal: str | None, tag: str, dev: float,
            note: str | None = None, *, check: bool = False) -> api.FeelSignal:
    dev = round(max(0.0, dev), 2)
    return api.FeelSignal(
        key=key, label=label, value=value, normal=normal, tag=tag, dev=dev,
        tone="check" if check and dev >= OFF_DEV / 2 else _tone(dev), note=note,
    )


def _day(date: dt.date) -> str:
    return f"{date:%a}"


def _night(date: dt.date) -> str:
    """Garmin dates a night by the morning it ends, so the night *before*."""
    return f"{date - dt.timedelta(days=1):%a} night"


def _num(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


Points = Sequence[tuple[dt.date, float]]


def _last_two(points: Points, date: dt.date) -> list[tuple[dt.date, float]]:
    by = dict(points)
    return [(day, by[day]) for day in (date - dt.timedelta(days=1), date) if day in by]


def _before(logs: Sequence[Any], date: dt.date, at: int) -> list[Any]:
    """One day's rows up to minute `at`, in time order."""
    rows = [row for row in logs if row.date == date and _mod(row.hour, row.minute) <= at]
    return sorted(rows, key=lambda row: _mod(row.hour, row.minute))


def _kept(logs: Sequence[Any], date: dt.date, days: int = 3) -> bool:
    """Whether a log was being kept in the days up to `date` -- an absence in
    a log nobody was keeping is not evidence of anything."""
    start = date - dt.timedelta(days=days)
    return any(start <= row.date <= date for row in logs)


def _wake(ctx: Context, date: dt.date) -> int:
    row = next((r for r in ctx.readiness if r.date == date), None)
    return clock_minutes(row.woke) if row and row.woke else 7 * 60


def _load_pair(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    day = entry.date
    if health._history_days(ctx.sessions, day) < health.MIN_HISTORY_FOR_RATIO:
        return None
    first = min(s.date for s in ctx.sessions)
    one, two = day - dt.timedelta(days=1), day - dt.timedelta(days=2)
    base = [
        ctx.loads.get(day - dt.timedelta(days=k), 0.0)
        for k in range(3, health.CHRONIC_DAYS + 3)
        if day - dt.timedelta(days=k) >= first
    ]
    normal = statistics.fmean(base) if base else 0.0
    if normal <= 0:
        return None
    pair = [ctx.loads.get(two, 0.0), ctx.loads.get(one, 0.0)]
    ratio = statistics.fmean(pair) / normal
    tag = f"{ratio:.1f}× normal" if ratio >= 1.2 else "light" if ratio < 0.8 else "normal"
    pairs = [
        ctx.loads.get(day - dt.timedelta(days=k), 0.0)
        + ctx.loads.get(day - dt.timedelta(days=k + 1), 0.0)
        for k in range(1, health.CHRONIC_DAYS)
    ]
    heaviest = sum(pair) > 0 and sum(pair) >= max(pairs)
    return _signal(
        "load2", "training, last two days",
        f"{_day(two)} {pair[0]:.0f} · {_day(one)} {pair[1]:.0f}",
        f"{normal:.0f} a day", tag, ratio - 1.0,
        "the heaviest two days in four weeks" if heaviest else None,
    )


def _week(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    week = health.window_load(ctx.loads, entry.date, health.ACUTE_DAYS)
    prev = health.window_load(
        ctx.loads, entry.date - dt.timedelta(days=health.ACUTE_DAYS), health.ACUTE_DAYS
    )
    if prev <= 0:
        return None
    ratio = week / prev
    ratio_4 = health.acwr(ctx.sessions, entry.date)
    note = (
        f"the four-week ratio reads {ratio_4:.2f} — it smooths a jump like this out"
        if ratio_4 is not None and ratio >= 1.5 and ratio_4 < health.BAND_HIGH
        else None
    )
    tag = f"{ratio:.1f}×" if ratio >= 1.2 else "lighter" if ratio < 0.8 else "level"
    return _signal("week", "this week vs last", f"{week:,.0f}", f"{prev:,.0f} last week",
                   tag, ratio - 1.0, note)


def _heart(ctx: Context, entry: d.FeelLog, *, rhr: bool) -> api.FeelSignal | None:
    points = ctx.rhr if rhr else ctx.hrv
    baseline = health.scalar_baseline(points, entry.date)
    two = _last_two(points, entry.date)
    if baseline is None or not two:
        return None
    mean, spread = baseline
    zs = [((value - mean) / spread) * (1 if rhr else -1) for _, value in two]
    dev = max(zs)
    run = health.scalar_run(points, entry.date, above=rhr)
    label = _day if rhr else _night
    value = " · ".join(f"{label(day)} {_num(v)}" for day, v in two)
    if run:
        tag = f"{'up' if rhr else 'down'} {run} day{'s' if run > 1 else ''}"
    elif dev >= OFF_DEV:
        tag = "dipped, back" if zs[-1] < OFF_DEV else ("up" if rhr else "down")
    else:
        tag = "normal"
    note = (
        "raised two mornings running — an early sign of both overreaching and a cold"
        if rhr and run >= 2
        else None
    )
    return _signal("rhr" if rhr else "hrv", "resting HR" if rhr else "overnight HRV", value,
                   _num(mean), tag, dev, note)


def _scalar_low(ctx: Context, entry: d.FeelLog, key: str, label: str, unit: str,
                fallback_sd: float) -> api.FeelSignal | None:
    """Sleep and Body Battery: the lower of the last two readings against your
    own normal, or against the week with a fixed spread while the series is
    too young for one."""
    points = ctx.sleep if key == "sleep" else ctx.battery
    two = _last_two(points, entry.date)
    if not two:
        return None
    baseline = health.scalar_baseline(points, entry.date)
    if baseline is None:
        week = health.scalar_mean(points, entry.date)
        if week is None:
            return None
        baseline = (week, fallback_sd)
    mean, spread = baseline
    low = min(value for _, value in two)
    dev = (mean - low) / spread
    labeller = _night if key == "sleep" else _day
    value = " · ".join(f"{labeller(day)} {_num(v)}{unit}" for day, v in two)
    note = None
    if key == "sleep":
        scores = _last_two(ctx.sleep_score, entry.date)
        if scores:
            note = "scored " + ", then ".join(_num(v) for _, v in scores)
    tag = ("one short night" if key == "sleep" else "low") if dev >= OFF_DEV else "normal"
    return _signal(key, label, value, f"{_num(mean)}{unit}", tag, dev, note)


def _felt(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    row = next((r for r in ctx.readiness if r.date == entry.date), None)
    if row is None:
        return None
    said = row.sleep_quality
    score = dict(ctx.sleep_score).get(entry.date)
    dev = float(max(0, 3 - said))
    note = None
    if said <= 2 and score is not None and score >= 70:
        tag, dev = "disagrees", dev + 0.5
        note = f"you said you slept badly; the watch scored it {score:.0f}"
    else:
        tag = "slept badly" if said <= 2 else "fine"
    recent = [r.sleep_quality for r in ctx.readiness
              if entry.date - dt.timedelta(days=14) <= r.date < entry.date]
    normal = f"{statistics.fmean(recent):.1f} usually" if recent else None
    return _signal("felt", "sleep, as you rated it", f"{said} / 5 this morning", normal,
                   tag, dev, note)


def _stress(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    two = _last_two(ctx.stress, entry.date)
    if not two:
        return None
    baseline = health.scalar_baseline(ctx.stress, entry.date)
    if baseline is None:
        week = health.scalar_mean(ctx.stress, entry.date)
        if week is None:
            return None
        baseline = (week, 8.0)
    mean, spread = baseline
    high = max(value for _, value in two)
    dev = (high - mean) / spread
    return _signal("stress", "watch stress", " · ".join(f"{_day(k)} {_num(v)}" for k, v in two),
                   _num(mean), "was high" if dev >= OFF_DEV else "normal", dev)


def _fluid(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    if not _kept(ctx.drinks, entry.date):
        return None
    at = entry.minute_of_day
    wet = [row for row in _before(ctx.drinks, entry.date, at) if "alcohol" not in row.tags]
    if wet:
        last = wet[-1]
        gap = (at - _mod(last.hour, last.minute)) / 60
        value = f"nothing since {last.at}"
    else:
        gap = (at - _wake(ctx, entry.date)) / 60
        value = "nothing logged today"
    usual = health.fluid_mean(ctx.drinks, entry.date - dt.timedelta(days=1))
    sweat = dict(ctx.sweat).get(entry.date - dt.timedelta(days=1))
    note = f"after {sweat / 1000:.1f} L of sweat yesterday" if sweat and sweat >= 800 else None
    tag = f"{gap:.1f} h dry" if gap >= 3 else "recent"
    return _signal("fluid", "fluid", value, f"{usual / 1000:.1f} L a day" if usual else None,
                   tag, (gap - 3) / 2, note, check=True)


def _urine(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    if not _kept(ctx.visits, entry.date):
        return None
    voids = [row for row in _before(ctx.visits, entry.date, entry.minute_of_day)
             if row.kind == "urine" and row.color is not None]
    counted = voids[1:]
    if not counted:
        value = f"{voids[0].color}, {voids[0].at}" if voids else "none today"
        return _signal("urine", "urine colour", value, None, "none that counts", 0.5,
                       "the first of the day is left out — overnight is dark on everyone",
                       check=True)
    last = counted[-1]
    darkness = health.URINE_DARKNESS[str(last.color)]
    return _signal("urine", "urine colour", f"{last.color}, {last.at}", "light",
                   str(last.color) if darkness >= 3 else "fine", darkness - 2)


def _meal(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    if not _kept(ctx.meals, entry.date):
        return None
    at = entry.minute_of_day
    eaten = _before(ctx.meals, entry.date, at)
    if eaten:
        last = eaten[-1]
        gap = (at - _mod(last.hour, last.minute)) / 60
        value = f"{last.at}, {last.size}"
    else:
        gap = (at - _wake(ctx, entry.date)) / 60
        value = "nothing yet today"
    square = sum(1 for meal in eaten if not meal.is_snack)
    note = f"{square} meal{'s' if square != 1 else ''} so far today"
    return _signal("meal", "last meal", value, "three a day", f"{gap:.1f} h ago",
                   (gap - 4) / 1.5, note)


def _quality(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    eaten = _before(ctx.meals, entry.date, entry.minute_of_day)
    if not eaten:
        return None
    today = statistics.fmean(meal.quality for meal in eaten)
    week = health.diet_quality(ctx.meals, entry.date - dt.timedelta(days=1))
    dev = (week - today) if week is not None else (3 - today)
    fried = sum(1 for meal in eaten if "fried" in meal.flags)
    note = f"{fried} fried" if fried else None
    return _signal("quality", "what you ate", " · ".join(f"q{m.quality}" for m in eaten),
                   f"{week:.1f} this week" if week is not None else None,
                   "worse than usual" if dev >= OFF_DEV else "fine", dev, note)


def _caffeine(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    if not _kept(ctx.drinks, entry.date):
        return None
    had = sum(row.amount for row in _before(ctx.drinks, entry.date, entry.minute_of_day)
              if "caffeine" in row.tags)
    habit = health.caffeine_mean(ctx.drinks, entry.date - dt.timedelta(days=1))
    if had >= 3:
        tag, dev, note = f"{_num(had)} today", had - 2, None
    elif had == 0 and habit is not None and habit >= 1:
        tag, dev = "none — withdrawal?", 1.5
        note = f"against {habit:.1f} a day this week"
    else:
        tag, dev, note = ("clear" if had == 0 else f"{_num(had)} today"), 0.0, None
    return _signal("caffeine", "caffeine", f"{_num(had)} today" if had else "none today",
                   f"{habit:.1f} a day" if habit is not None else None, tag, dev, note)


def _alcohol(ctx: Context, entry: d.FeelLog) -> api.FeelSignal | None:
    if not _kept(ctx.drinks, entry.date):
        return None
    start = entry.date - dt.timedelta(days=2)
    total = sum(
        row.amount for row in ctx.drinks
        if "alcohol" in row.tags and start <= row.date <= entry.date
        and (row.date < entry.date or _mod(row.hour, row.minute) <= entry.minute_of_day)
    )
    return _signal("alcohol", "alcohol", f"{_num(total)} in three days", "none",
                   "clear" if total == 0 else f"{_num(total)} drinks", min(3.0, total))


SIGNALS: tuple[Callable[[Context, d.FeelLog], api.FeelSignal | None], ...] = (
    _load_pair,
    _week,
    lambda ctx, e: _heart(ctx, e, rhr=True),
    lambda ctx, e: _heart(ctx, e, rhr=False),
    lambda ctx, e: _scalar_low(ctx, e, "sleep", "sleep", " h", 1.0),
    _felt,
    lambda ctx, e: _scalar_low(ctx, e, "battery", "body battery", "", 15.0),
    _stress,
    _fluid,
    _urine,
    _meal,
    _quality,
    _caffeine,
    _alcohol,
)

ALL_KEYS = [
    "load2", "week", "rhr", "hrv", "sleep", "felt", "battery", "stress",
    "fluid", "urine", "meal", "quality", "caffeine", "alcohol",
]
"""Every signal's key, in `SIGNALS` order -- what `other` is ranked on."""

_COLD = ["rhr", "hrv", "load2", "week", "sleep"]

SUSPECTS: dict[str, list[str]] = {
    "sleepy": ["sleep", "felt", "meal", "caffeine", "battery"],
    "drained": ["load2", "week", "battery", "fluid", "meal", "quality"],
    "malaise": ["rhr", "hrv", "load2", "week", "sleep", "fluid"],
    "flat": ["sleep", "felt", "stress", "load2", "week"],
    "down": ["sleep", "felt", "stress", "load2", "alcohol"],
    "on_edge": ["stress", "caffeine", "sleep", "hrv"],
    "irritable": ["meal", "sleep", "stress", "caffeine"],
    **{symptom.value: _COLD for symptom in SICK},
    "headache": ["fluid", "caffeine", "sleep", "alcohol", "felt", "urine"],
    "shaky": ["meal", "caffeine", "fluid"],
    "dizzy": ["fluid", "meal", "urine"],
    "hungry": ["meal", "load2", "quality"],
    "thirsty": ["fluid", "urine", "load2"],
    "nauseous": ["meal", "quality", "load2", "fluid"],
    "gut": ["quality", "meal", "alcohol"],
    "sore": ["load2", "week"],
    "hurt": ["load2", "week"],
    "foggy": ["sleep", "felt", "fluid", "caffeine", "alcohol", "quality"],
    # An energy or mood at 2 with no kind tapped: the kinds' suspects together.
    "tired": ["sleep", "load2", "week", "rhr", "battery", "meal", "fluid"],
    "low": ["sleep", "felt", "stress", "load2"],
}
"""Which signals each word suspects -- chosen ahead of time, from what each
word is known to follow. `other` is absent on purpose: it has no suspects, and
is ranked on everything."""


WORDS = {"on_edge": "on edge", "sore_throat": "sore throat"}


def word_label(word: str) -> str:
    return WORDS.get(word, word.replace("_", " "))


def lookback_words(entry: d.FeelLog) -> list[str]:
    """What the lookback is asked about: the entry's words, or `tired` / `low`
    for a scale at 2 that nobody put a word to."""
    words = list(entry.words)
    if entry.energy <= 2 and not entry.tired:
        words.insert(0, "tired")
    if entry.mood <= 2 and not entry.low:
        words.append("low")
    return words


def signals(ctx: Context, entry: d.FeelLog) -> list[api.FeelSignal]:
    """Every signal there is data for, before `entry`, strongest first."""
    out = [signal for measure in SIGNALS if (signal := measure(ctx, entry)) is not None]
    return sorted(out, key=lambda signal: -signal.dev)


def _counts(ctx: Context, history: Sequence[d.FeelLog], words: Sequence[str]) -> tuple[
    dict[str, int], dict[str, list[api.FeelCount]]
]:
    tagged = {entry.id: set(lookback_words(entry)) for entry in history}
    flags = {word: sum(1 for ws in tagged.values() if word in ws) for word in words}
    counts: dict[str, list[api.FeelCount]] = {}
    ready = [word for word in words if flags[word] >= MIN_FLAGS]
    if not ready:
        return flags, counts
    measured = {entry.id: {s.key: s.dev for s in signals(ctx, entry)} for entry in history}
    for word in ready:
        keys = SUSPECTS.get(word) or ALL_KEYS
        with_word = [entry.id for entry in history if word in tagged[entry.id]]
        without = [entry.id for entry in history if word not in tagged[entry.id]]
        rows = []
        for key in dict.fromkeys(keys):
            had = [measured[i][key] for i in with_word if key in measured[i]]
            other = [measured[i][key] for i in without if key in measured[i]]
            if not had:
                continue
            rows.append(api.FeelCount(
                key=key,
                flagged=sum(1 for dev in had if dev >= OFF_DEV),
                of=len(had),
                otherwise=sum(1 for dev in other if dev >= OFF_DEV),
                of_other=len(other),
            ))
        counts[word] = rows
    return flags, counts


def lookback(ctx: Context, feels_today: Sequence[d.FeelLog],
             history: Sequence[d.FeelLog] = ()) -> api.FeelLookback | None:
    """What came before the day's latest flagged feel, or None if none is."""
    flagged = [entry for entry in feels_today if entry.flagged]
    if not flagged:
        return None
    entry = flagged[-1]
    words = lookback_words(entry)
    flags, counts = _counts(ctx, history, words)
    return api.FeelLookback(
        entry=entry,
        signals=signals(ctx, entry),
        suspects={word: SUSPECTS[word] for word in words if word in SUSPECTS},
        flags=flags,
        counts=counts,
    )


# --- two sentences -----------------------------------------------------------------


def _what(entry: d.FeelLog) -> str:
    words = [word_label(w) for w in entry.words if w != "other"]
    if entry.note:
        words.append(f"“{entry.note}”")
    if words:
        return ", ".join(words)
    parts = [f"energy {entry.energy}/5"] if entry.energy <= 2 else []
    parts += [f"mood {entry.mood}/5"] if entry.mood <= 2 else []
    return " and ".join(parts)


def flag_line(look: api.FeelLookback | None, rhr_run: int) -> str | None:
    """The health panel's flag line, off the latest flagged feel.

    The one case said outright is a cold starting: a sick word, or malaise, on a
    morning resting heart rate is already up. Everything else is one sentence
    naming the loudest suspect -- the lookback beneath it has the rest.
    """
    if look is None:
        return None
    entry = look.entry
    what = f"{_what(entry)} at {entry.at}"
    sickish = Tired.malaise in entry.tired or any(Symptom(s) in SICK for s in entry.symptoms)
    if sickish and rhr_run >= 1:
        mornings = f"{rhr_run} morning{'s' if rhr_run > 1 else ''}"
        return (f"{what}, with resting HR up {mornings} running — the shape a cold "
                f"starts with. Worth watching tomorrow morning.")
    keys = set().union(*(look.suspects.get(w, []) for w in lookback_words(entry))) or None
    loud = [s for s in look.signals if s.dev >= OFF_DEV and (keys is None or s.key in keys)]
    if loud:
        return f"{what} — the loudest thing before it: {loud[0].label}, {loud[0].tag}."
    return f"{what} — nothing before it stands out yet."


def landing_line(feels_today: Sequence[d.FeelLog], week: float, prev: float) -> str | None:
    """How the week's load is landing, for under the load zone.

    Not an input to load, and on purpose: load is the work done and feel is how
    it is being taken, and averaging the two would make a hard week look like an
    easier one. Side by side they are the overtraining signature -- load up while
    energy goes down (Meeusen et al. 2013).
    """
    if not feels_today:
        return None
    energy = statistics.fmean(entry.energy for entry in feels_today)
    verdict = "landing hard" if energy <= 2 else "landing well" if energy >= 4 else "landing ok"
    load = (f"this week is {week / prev:.1f}× last" if prev > 0
            else f"{week:,.0f} load this week")
    return f"{load} · energy {_num(energy)}/5 today → {verdict}"


# --- the panel ----------------------------------------------------------------------


def panel(
    cfg: Any,
    repo: Any,
    today: dt.date,
    ctx: Context,
    feels_today: Sequence[d.FeelLog],
    *,
    week: float,
    prev_week: float,
    rhr_run: int,
    now: dt.datetime | None = None,
) -> api.FeelPanel:
    """Everything the feel log adds to the health panel.

    `now` is the laptop's clock, used for one thing: whether today's windows
    are still open. See the head of this module for why that is allowed here.
    """
    now = now or dt.datetime.now()
    if today == now.date():
        now_minute: int | None = now.hour * 60 + now.minute
    elif today < now.date():
        now_minute = None
    else:
        now_minute = -1
    pings = (
        ping_states(cfg.feel.windows, repo.feel_pings(today), feels_today, now_minute)
        if cfg.feel.pings
        else []
    )
    ctx.load_window(today)
    look = None
    if any(entry.flagged for entry in feels_today):
        history = repo.feel_history(COUNT_DAYS, before=today)
        latest = [entry for entry in feels_today if entry.flagged][-1]
        ripe = any(
            sum(1 for entry in history if word in lookback_words(entry)) >= MIN_FLAGS
            for word in lookback_words(latest)
        )
        if ripe:
            # The counts measure every feel in the season, so the logs have to
            # reach back that far too; the panel's own windows are four weeks.
            days = COUNT_DAYS + health.ACUTE_DAYS
            ctx.meals = repo.meal_history(days, before=today)
            ctx.drinks = repo.drink_history(days, before=today)
            ctx.visits = repo.bathroom_history(days, before=today)
        look = lookback(ctx, feels_today, history)
    return api.FeelPanel(
        pings=pings,
        flag=flag_line(look, rhr_run),
        landing=landing_line(feels_today, week, prev_week),
        lookback=look,
    )
