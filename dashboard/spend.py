"""What the LLM actually costs, read back out of `metrics.jsonl`.

Every paid call has appended a line to that file since the first generation,
and until now nothing has ever read one. The number the README quotes a range
for -- $0.15-0.80 for a live math generation -- was the one number the
dashboard would not show you, which is the wrong way round for the only
recurring cost the app has.

Read-only and file-based on purpose. The log is append-only, written by both
the server and the batch CLI, and it is the audit trail for real money; putting
it in SQLite would mean a failed write could lose a record of a call that has
already been billed. `log_generation` never raises for the same reason.

Nothing here is authoritative about money. `est_cost_usd` is an estimate
computed from `[llm.pricing]` at the time of the call, and a rate that has
since changed does not rewrite it -- which is correct, and is why the figure is
labelled an estimate everywhere it surfaces.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, NamedTuple

from . import logs
from .models.api import SpendKind, SpendResponse, SpendWindow

log = logs.get(__name__)

WINDOWS: tuple[tuple[int, str], ...] = ((1, "today"), (7, "7d"), (30, "30d"))
"""The three spans, in days back from the browser's today, inclusive.

`today` is a one-day window rather than a special case, so all three are the
same arithmetic and the strip cannot show a "today" that disagrees with the
first day of "7d".
"""

MAX_LINES = 5_000
"""How much of the tail to parse. Roughly two years at current volume.

A bound rather than a full read because this runs on every dashboard boot and
the file only ever grows -- and because the longest window is 30 days, so
everything past the tail is already outside every question being asked.
"""

LEGACY_KIND = "math"
"""What a line with no `kind` was.

The first line of the real log predates the field. It is not a guess that it
was a math generation: `kind` was added in the same change that routed the
word, the grader, the theorem card and the topic links through the metrics
log at all, so every line written before it exists is by construction the only
generation that was being recorded then.
"""


class Entry(NamedTuple):
    date: dt.date
    kind: str
    usd: float


def _entry(raw: Any) -> Entry | None:
    """One parsed line, or None if it cannot be read as one.

    Deliberately forgiving. This file is an append-only log spanning every
    schema the app has had, and a line this cannot understand is a line to
    count and skip -- refusing to render any spend at all because of one
    malformed record from July would be a worse answer than a slightly low
    total, and the count is reported so the total is never silently wrong.
    """
    if not isinstance(raw, dict):
        return None
    try:
        date = dt.date.fromisoformat(raw["date"])
    except (KeyError, TypeError, ValueError):
        return None

    cost = raw.get("est_cost_usd")
    if cost is None:
        # A call that was logged without metrics -- the cost was never
        # measured. Counted as a call at $0 rather than dropped: it happened,
        # and hiding it would make the call count disagree with the log.
        cost = 0.0
    try:
        usd = float(cost)
    except (TypeError, ValueError):
        return None

    return Entry(date=date, kind=str(raw.get("kind") or LEGACY_KIND), usd=usd)


def load(path: Path) -> tuple[list[Entry], int]:
    """`(entries, lines that could not be read)`.

    Never raises. A missing file is the normal state of a fresh checkout and
    reads as "nothing has been spent", which is true.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.exception("could not read the metrics log at %s", path)
        return [], 0

    entries: list[Entry] = []
    bad = 0
    for line in lines[-MAX_LINES:]:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        entry = _entry(raw)
        if entry is None:
            bad += 1
        else:
            entries.append(entry)
    return entries, bad


def _window(entries: list[Entry], today: dt.date, days: int, label: str) -> SpendWindow:
    since = today - dt.timedelta(days=days - 1)
    inside = [e for e in entries if since <= e.date <= today]

    by_kind: dict[str, list[float]] = {}
    for entry in inside:
        by_kind.setdefault(entry.kind, []).append(entry.usd)

    return SpendWindow(
        days=days,
        label=label,
        usd=round(sum(e.usd for e in inside), 4),
        calls=len(inside),
        # Dearest first: the question this answers is "where is the money
        # going", and the answer should be the first line rather than
        # something to be found by scanning.
        by_kind=sorted(
            (SpendKind(kind=k, calls=len(v), usd=round(sum(v), 4)) for k, v in by_kind.items()),
            key=lambda b: -b.usd,
        ),
    )


def summarise(
    entries: list[Entry], bad_lines: int, today: dt.date, problems_served: int
) -> SpendResponse:
    """The three windows, plus the two numbers that say whether the batch ran.

    `problems_served` is counted from `math_attempt` rather than from this log,
    and that is the whole trick: the log records only calls that were *paid
    for*, so a problem served from the overnight batch or from the curated pool
    leaves no line in it at all. The gap between the two counts is therefore
    exactly the number of problems that cost nothing -- which is the batch's
    job, stated as a number, and which nothing in the UI has ever said.
    """
    longest = max(days for days, _ in WINDOWS)
    since = today - dt.timedelta(days=longest - 1)
    live_math = [e.date for e in entries if e.kind == "math" and since <= e.date <= today]

    return SpendResponse(
        windows=[_window(entries, today, days, label) for days, label in WINDOWS],
        window_days=longest,
        problems_served=problems_served,
        problems_paid=len(live_math),
        last_live_generation=max(live_math) if live_math else None,
        tracking_since=min((e.date for e in entries), default=None),
        unreadable_lines=bad_lines,
    )
