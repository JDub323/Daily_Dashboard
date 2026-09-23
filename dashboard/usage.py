"""How often each phone screen is opened -- the file behind `POST /api/usage`.

The database records what is written and nothing about what is read, so a
screen that only shows things looks identical in it whether it is opened ten
times a day or never. The phone counts openings per screen per day
(`web/m/tally.js`) and sends every day it still holds whenever the send button
gets something through; this keeps them.

**Merged by maximum, not by sum.** The phone's count for a day only ever goes
up, and it sends the same days again on every send. Taking the larger of what
is here and what arrived makes a resend, a send that lands twice, and one that
arrives after a later one all harmless -- and a phone whose storage was cleared
mid-day cannot pull a day's count down.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()
"""Two sends racing would each read the file, merge, and write -- and the second
write would drop the first's days. Requests run on a thread pool, so this is a
real interleaving, however unlikely on a one-phone app."""


def read(path: Path) -> dict[str, dict[str, int]]:
    """What is held, or `{}` for a missing or unreadable file."""
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def merge(path: Path, days: dict[dt.date, dict[str, int]]) -> int:
    """Fold one send into the file. Returns how many days it now holds."""
    with _lock:
        held = read(path)
        for date, screens in days.items():
            day = held.setdefault(date.isoformat(), {})
            for screen, n in screens.items():
                day[screen] = max(n, int(day.get(screen, 0)))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(sorted(held.items())), indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return len(held)
