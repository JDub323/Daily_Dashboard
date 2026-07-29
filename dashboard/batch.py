"""The 3am hand-off: what the generator needs, and what it must produce.

The math problem is the only generation worth moving off the API. It is
roughly 99% of the bill -- four web searches drag a few hundred thousand
input tokens into the context (EXPLAINER §5.5) -- while the word, the theorem
card and the topic links come to under a cent a day between them and stay
exactly where they are.

Two subcommands, and the split is the point:

    --context          everything the generator must know, read live from
                       config.toml and the database
    --validate FILE    the checks the server would apply, run at 3am instead
                       of at 8am

`--context` exists so the scheduled task's prompt can be *static*. Baking the
rating and the do-not-repeat list into the prompt would mean rewriting it
whenever either changed, and a stale do-not-repeat list is precisely how you
pay for a problem you have already solved. The prompt says "run this and read
the answer"; the answer is always current.

On [Invariant 1] (the browser owns "today"): this module calls
`date.today()`, which the server must never do. That is not an exception to
the rule but the other side of it. There is no browser at 3am, so the CLI has
to name the day itself -- and it names it only to *choose a filename*. The
serving path still resolves `batches/<date>.json` against the date the browser
supplies, so a machine whose clock disagrees produces a file that simply is
not found rather than one that silently serves the wrong day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import content, llm, logs
from .config import Settings, load_settings
from .db import Repository
from .models.batch import DailyBatch

log = logs.get(__name__)

LADDER = 3
"""How many tiers to ask for. Wide enough that a rating drift or a repeated
source still leaves a usable rung, narrow enough that the generator is not
paying for problems nobody will see."""


# --- context ---------------------------------------------------------------


def suggested_tiers(settings: Settings, rating: int) -> list[str]:
    """`LADDER` tiers bracketing the current rating, easiest first.

    The tier→difficulty policy stays in code. The generator is told which
    tiers to aim at rather than being handed a rating and asked to work it
    out, for the same reason the live prompt constrains it to a tier name
    instead of a number: a model's idea of "1528-appropriate" drifts between
    versions, and the elo history has to stay comparable across years.
    """
    by_anchor = sorted(settings.math.anchors, key=lambda t: settings.math.anchors[t])
    if len(by_anchor) <= LADDER:
        return by_anchor

    # The rung the rating sits at, then a window centred on it, clamped to
    # the ends of the table.
    nearest = min(
        range(len(by_anchor)), key=lambda i: abs(settings.math.anchors[by_anchor[i]] - rating)
    )
    start = max(0, min(nearest - LADDER // 2, len(by_anchor) - LADDER))
    return by_anchor[start : start + LADDER]


def context(settings: Settings, repo: Repository, date: dt.date) -> dict[str, Any]:
    """Everything the generator needs, as one JSON object."""
    rating = repo.current_rating("math")
    seen = sorted(repo.seen_sources())
    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "topic": settings.math.topic_for(date),
        "rating": rating,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, rating),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": seen[-content.SEEN_WINDOW :],
        "already_generated": batch_path(settings, date).exists(),
    }


def context_without_db(settings: Settings, date: dt.date, why: str) -> dict[str, Any]:
    """What can still be said when the database is unreadable.

    The topic rotation and the tier table live in config.toml, so the
    generator keeps its two most important instructions. It loses the rating
    (so the ladder is centred on the seed instead) and the do-not-repeat list
    (so a duplicate becomes possible -- caught on import by the UNIQUE
    constraint, at the cost of a rung).
    """
    seed = settings.math.anchors[settings.math.default_tier]
    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "topic": settings.math.topic_for(date),
        "rating": None,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, seed),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": [],
        "already_generated": batch_path(settings, date).exists(),
        "degraded": (
            f"could not read the database ({why}). Rating and the do-not-repeat list "
            f"are unavailable; the tier ladder is centred on the default tier instead. "
            f"Generate anyway — a repeat is rejected on import, not served."
        ),
    }


def batch_path(settings: Settings, date: dt.date) -> Path:
    return settings.path("batches") / f"{date.isoformat()}.json"


@contextmanager
def snapshot(db_path: Path) -> Iterator[Repository]:
    """A read-only view of the live database, via a copy on local disk.

    One reason, and it is enough: this is an unattended job touching real data
    at 3am, and it should not be *able* to disturb the database, whatever else
    goes wrong. The copy is what makes that true, because the two obvious
    alternatives are not as read-only as they look.

    `?mode=ro` on the live file still writes. It stamps the -shm sidecar as it
    reads, and if the sidecars are absent -- server down, its last clean close
    having removed them -- then merely connecting *creates* a -wal and a -shm
    next to the live database and leaves them there.

    `?immutable=1` genuinely touches nothing, and is wrong the other way: it
    tells SQLite there is no -wal to consult, so while the server is running it
    reads whatever the last checkpoint left behind. In the probe below that was
    total -- a table created since the checkpoint was not visible at all. The
    seen-list is the one piece of state that has to be current, because a stale
    one is how you regenerate a problem you solved last night.

    Not a 9p problem. An earlier version of this docstring blamed the mount: no
    POSIX advisory locking, no mmap of the -shm, so the first WAL opener wins
    and the rest are locked out. Measured on 2026-07-27 (WSL2, /mnt/c, SQLite
    3.46.1), both are false -- a second process reads a WAL database on /mnt/c
    while the server holds it open, read-write or read-only, and leaves the
    server's sidecars alone. What does break a later opener is sidecars going
    missing from under it: with both gone it silently reads the last
    checkpoint, with only the -wal gone it gets SQLITE_IOERR_SHORT_READ, and
    with the whole directory gone the "unable to open database file" that the
    old text pinned on locking. None of that is what the copy defends against.

    The database is ~150KB. The copy costs less than reasoning about the
    alternatives.
    """
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    tmp = Path(tempfile.mkdtemp(prefix="dashboard-batch-"))
    try:
        # The sidecars matter: an active -wal holds everything committed
        # since the last checkpoint, which is most of today.
        for src in sorted(db_path.parent.glob(db_path.name + "*")):
            shutil.copy2(src, tmp / src.name)

        # Deliberately not `db.connect()`: that sets journal_mode=WAL, which
        # is exactly what must not happen to a throwaway read-only copy.
        conn = sqlite3.connect(f"file:{tmp / db_path.name}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield Repository(conn)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- validation ------------------------------------------------------------


def validate(settings: Settings, raw: str) -> tuple[DailyBatch | None, list[str]]:
    """Parse and check a generated batch. Returns (batch, problems found).

    Runs the *same* extraction the API path runs -- `extract_json`, LaTeX
    repair and all -- because the failure modes are identical. A model asked
    for JSON containing LaTeX writes `\\frac` whether it is answering over
    HTTPS or writing a file, and `\\f` is a legal JSON escape that parses
    happily into a formfeed. See EXPLAINER §5.7.
    """
    errors: list[str] = []
    try:
        parsed = llm.extract_json(raw)
    except json.JSONDecodeError as e:
        return None, [f"not JSON: {e}"]

    try:
        batch = DailyBatch(**parsed)
    except Exception as e:
        return None, [f"does not match the batch schema: {e}"]

    for i, p in enumerate(batch.problems):
        if p.tier not in settings.math.anchors:
            errors.append(
                f"problems[{i}] ({p.source}): unknown tier {p.tier!r}; "
                f"expected one of {sorted(settings.math.anchors)}"
            )
        if not p.verified_url:
            errors.append(f"problems[{i}] ({p.source}): no verified_url — was it actually checked?")
        try:
            content.to_problem(p.model_dump(), settings, model="scheduled-task")
        except content.ContentError as e:
            errors.append(f"problems[{i}] ({p.source}): {e}")

    sources = [p.source for p in batch.problems]
    if len(set(sources)) != len(sources):
        errors.append("the ladder repeats a source; each rung must be a different problem")

    return batch, errors


# --- cli -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="batch.sh",
        description="Overnight math-problem hand-off. See dashboard/batch.py.",
    )
    ap.add_argument("--context", action="store_true", help="print what the generator needs")
    ap.add_argument("--validate", metavar="FILE", type=Path, help="check a generated batch")
    ap.add_argument("--date", type=dt.date.fromisoformat, help="default: today, locally")
    args = ap.parse_args(argv)

    if not args.context and not args.validate:
        ap.error("one of --context or --validate is required")

    settings = load_settings()
    # See the module docstring: naming the day is the CLI's job, not the
    # server's, and it only ever chooses a filename.
    date = args.date or dt.date.today()

    if args.context:
        try:
            with snapshot(settings.path("db")) as repo:
                print(json.dumps(context(settings, repo, date), indent=2))
        except (OSError, sqlite3.Error) as e:
            # Degraded, not dead. Everything except the rating and the
            # do-not-repeat list comes from config.toml, and a generator that
            # gets the topic but not the seen list still produces a usable
            # ladder -- `source` is UNIQUE, so a repeat is caught on import
            # rather than served. Saying so in the payload matters more than
            # failing: the task can note it and carry on.
            log.exception("could not read %s", settings.path("db"))
            print(json.dumps(context_without_db(settings, date, str(e)), indent=2))
            return 0
        return 0

    path: Path = args.validate
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    batch, errors = validate(settings, path.read_text(encoding="utf-8"))
    if batch is None or errors:
        print(f"REJECTED {path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if batch.date != date:
        # A warning, not a rejection: regenerating yesterday's batch by hand
        # is a legitimate thing to do, and the serving path keys off the
        # filename anyway.
        print(f"note: batch is dated {batch.date}, today is {date}", file=sys.stderr)

    print(f"OK {path}: {len(batch.problems)} problem(s) for {batch.date}")
    for p in batch.problems:
        print(f"  {p.tier:>14}  {p.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
