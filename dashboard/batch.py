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

from . import content, llm, logs, tricks
from .config import Settings, load_settings
from .db import Repository
from .models.batch import DailyBatch

log = logs.get(__name__)

LADDER = 3
"""How many tiers to ask for. Wide enough that a rating drift or a repeated
source still leaves a usable rung, narrow enough that the generator is not
paying for problems nobody will see."""


# --- context ---------------------------------------------------------------


def suggested_tiers(settings: Settings, rating: int, *, showcase: bool = False) -> list[str]:
    """`LADDER` tiers bracketing the current rating, easiest first.

    The tier→difficulty policy stays in code. The generator is told which
    tiers to aim at rather than being handed a rating and asked to work it
    out, for the same reason the live prompt constrains it to a tier name
    instead of a number: a model's idea of "1528-appropriate" drifts between
    versions, and the elo history has to stay comparable across years.

    A showcase day gets the top of the table instead, and the rating plays no
    part. Saturday is not selecting for difficulty at all -- it is selecting
    for famous problems, which mostly live up there, and a window centred on
    the rating would quietly exclude them.
    """
    by_anchor = sorted(settings.math.anchors, key=lambda t: settings.math.anchors[t])
    if len(by_anchor) <= LADDER:
        return by_anchor
    if showcase:
        return by_anchor[-LADDER:]

    # The rung the rating sits at, then a window centred on it, clamped to
    # the ends of the table.
    nearest = min(
        range(len(by_anchor)), key=lambda i: abs(settings.math.anchors[by_anchor[i]] - rating)
    )
    start = max(0, min(nearest - LADDER // 2, len(by_anchor) - LADDER))
    return by_anchor[start : start + LADDER]


SHOWCASE_BRIEF = (
    "SHOWCASE DAY. Ignore the rating and ignore difficulty: every rung of today's "
    "ladder should be a CLASSIC, BEAUTIFUL problem on the topic — famous ones, the "
    "kind with a named trick or a one-line solution that is remembered decades "
    "later. Pick on elegance and fame alone, and report each problem's honest tier "
    "rather than softening it. The dashboard serves the hardest rung on this day, "
    "so the ladder is a choice of classics, not a spread of difficulties."
)
"""What Saturday asks for instead of a calibrated ladder.

Spelled out in the context payload rather than in the scheduled task's prompt,
for the reason the prompt is static at all: the task says "run this and read the
answer", so anything that depends on the date has to come back from the CLI.
"""


def context(settings: Settings, repo: Repository, date: dt.date) -> dict[str, Any]:
    """Everything the generator needs, as one JSON object."""
    rating = repo.current_rating("math")
    seen = sorted(repo.seen_sources())
    showcase = content.is_showcase(date)
    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "topic": settings.math.topic_for(date),
        "rating": rating,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, rating, showcase=showcase),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": seen[-content.SEEN_WINDOW :],
        "already_generated": batch_path(settings, date).exists(),
        "showcase": SHOWCASE_BRIEF if showcase else None,
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
    showcase = content.is_showcase(date)
    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "topic": settings.math.topic_for(date),
        "rating": None,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, seed, showcase=showcase),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": [],
        "already_generated": batch_path(settings, date).exists(),
        # Unaffected by the outage: the showcase depends on the calendar and
        # config, both of which are still readable.
        "showcase": SHOWCASE_BRIEF if showcase else None,
        "degraded": (
            f"could not read the database ({why}). Rating and the do-not-repeat list "
            f"are unavailable; the tier ladder is centred on the default tier instead. "
            f"Generate anyway — a repeat is rejected on import, not served."
        ),
    }


def batch_path(settings: Settings, date: dt.date) -> Path:
    return settings.path("batches") / f"{date.isoformat()}.json"


# --- the trick of the day --------------------------------------------------
#
# The batch matters more here than anywhere else in this file. The math problem
# has a curated pool behind it; the
# trick has neither, and there is no longer anything behind it at all -- no
# paste box, no paid generation. A night this does not run is a morning with no
# trick, which is the trade, and it is why the CLI rejects a set the serving
# path would have accepted: at 3am the task is still there to write another.

TRICK_SCHEMA = (
    "Write the file as JSON:\n"
    '{"date":"YYYY-MM-DD, the date in this payload","topic":"the topic in this payload, '
    'verbatim","name":"the standard name of the trick, plain text, no LaTeX",'
    '"statement":"what it says, stated precisely, LaTeX delimited",'
    '"idea":"when to reach for it and what it buys you — two or three sentences, and '
    'name the situation that should trigger it","kind":"trick or technique",'
    '"callback":"the name of the older trick given below, or null if none was",'
    '"problems":[{"band":"easy|medium|callback|challenge","prompt":"the problem, LaTeX '
    'delimited","answer":"the bare final answer","accept":["other spellings of the same '
    'answer"],"solution":"one or two lines: the trick applied, and the answer"}]}'
)
"""The batch file's shape: the set, plus `date`, `topic` and `callback`.

Those three are echoed back so `--validate-trick` can check the generator
answered the day it was asked about -- so the file names the day it is for and
issue and angle, and worth more here than there, because a trick set for the
wrong week is not visibly wrong at 8am.
"""


def trick_context(settings: Settings, repo: Repository | None, date: dt.date) -> dict[str, Any]:
    """Everything tonight's trick generation needs, as one JSON object.

    The callback is chosen *here*, at 3am, rather than at serve time -- the
    problem has to be written against a particular old trick, so the choice has
    to precede the generation. `content.next_trick` resolves it back by name on
    import, so the set and the card it reaches back to cannot disagree.

    `repo` is optional and degrades gracefully: without it
    there is no do-not-repeat list and no callback, so the night produces a
    trick that might duplicate one you have had -- caught on import by the
    UNIQUE index, at the cost of the morning.
    """
    cfg = settings.tricks
    if not cfg.enabled:
        return {"date": date.isoformat(), "enabled": False}

    topic = settings.math.topic_for(date)
    path = content.trick_batch_path(settings, date)

    callback = None
    seen: list[str] = []
    if repo is not None:
        seen = repo.trick_names()
        # The same pick the panel's copyable prompt makes, and seeded by the
        # day so the two agree -- a night's file and a morning's paste must not
        # be reinforcing different tricks.
        callback = content.today_callback(settings, repo, date)

    wanted = tricks.wanted_bands(cfg.easy, cfg.medium, callback=callback is not None)
    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "enabled": True,
        "topic": topic,
        "output_path": str(path),
        "already_generated": path.exists(),
        "bands": wanted,
        "callback": (
            {"name": callback.name, "statement": callback.statement} if callback else None
        ),
        # The rules and the instructions come back from here rather than living
        # in the scheduled task's prompt, which is the whole reason this CLI
        # exists: the prompt says "run this and read the answer", so anything
        # that depends on the date, the rotation or the history has to arrive
        # in the payload. It also means the free path is asking for exactly what
        # the paid one asks for, from one copy of the words.
        "rules": content.TRICK_RULES,
        "instructions": content.trick_instructions(topic, wanted, seen, callback),
        "schema": TRICK_SCHEMA,
    }


def validate_trick(
    settings: Settings, raw: str, date: dt.date | None = None
) -> tuple[Any | None, list[str]]:
    """Parse and check a generated trick set, for the CLI.

    The reading itself is `content.parse_trick_set`, shared with the serving
    path -- the same document read at both ends of the night, and a second set
    of rules for either end would be a set that drifts.

    What is local to here is the *strictness*: warnings are folded in with the
    errors, so a set one easy problem short is rejected. At 3am regenerating is
    free and the alternative is a morning that is quietly thinner than it should
    be. The import makes the opposite trade at 8am, and says why.
    """
    parsed, errors, warnings = content.parse_trick_set(settings, raw, date)
    return parsed, errors + warnings


def _report_trick(settings: Settings, path: Path, date: dt.date) -> int:
    """`--validate-trick`, printed. Same contract as the reports above: non-zero
    and a list of reasons, or a one-line summary of what landed."""
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    parsed, errors = validate_trick(settings, path.read_text(encoding="utf-8"), date)

    # The repeat check needs the database, and is the one check here that can
    # only be answered by it. Degraded rather than fatal when it cannot be read:
    # `trick.name` is UNIQUE, so a duplicate is rejected on import instead of
    # served, which is a lost morning rather than a wrong one.
    if parsed is not None:
        try:
            with snapshot(settings.path("db")) as repo:
                if repo.trick_named(parsed.name) is not None:
                    errors.append(f"you have already had {parsed.name!r}")
        except (OSError, sqlite3.Error) as e:
            print(f"note: could not check for repeats ({e})", file=sys.stderr)

    if parsed is None or errors:
        print(f"REJECTED {path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(
        f"OK {path}: {parsed.date} — {parsed.name} ({parsed.kind}, {parsed.topic}), "
        f"{len(parsed.problems)} problem(s)"
        + (f", callback to {parsed.callback}" if parsed.callback else "")
    )
    for problem in parsed.problems:
        print(f"  {problem.band:>10}  {problem.prompt[:70]}  ->  {problem.answer[:24]}")
    return 0


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
        description="Overnight hand-off: the math ladder and the trick of the day. "
        "See dashboard/batch.py.",
    )
    ap.add_argument("--context", action="store_true", help="print what the generator needs")
    ap.add_argument("--validate", metavar="FILE", type=Path, help="check a generated batch")
    ap.add_argument(
        "--trick-context", action="store_true", help="print what tonight's trick set needs"
    )
    ap.add_argument(
        "--validate-trick", metavar="FILE", type=Path, help="check a generated trick set"
    )
    ap.add_argument("--date", type=dt.date.fromisoformat, help="default: today, locally")
    args = ap.parse_args(argv)

    wanted = [
        args.context,
        args.validate,
        args.trick_context,
        args.validate_trick,
    ]
    if not any(wanted):
        ap.error(
            "one of --context, --validate, --trick-context or --validate-trick "
            "is required"
        )

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

    if args.trick_context:
        try:
            with snapshot(settings.path("db")) as repo:
                payload = trick_context(settings, repo, date)
        except (OSError, sqlite3.Error) as e:
            # Degraded like the two above, and the most consequential of the
            # three: without the database there is no do-not-repeat list, so
            # tonight's trick may be one you have already had. `trick.name` is
            # UNIQUE, so that is caught on import rather than served -- a lost
            # morning, not a wrong one. Generate anyway and say so.
            log.exception("could not read %s", settings.path("db"))
            payload = trick_context(settings, None, date)
            payload["degraded"] = (
                f"could not read the database ({e}). The do-not-repeat list and the "
                f"callback are unavailable; generate anyway — a repeat is rejected on "
                f"import, not served."
            )
        print(json.dumps(payload, indent=2))
        return 0

    if args.validate_trick:
        return _report_trick(settings, args.validate_trick, date)

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
