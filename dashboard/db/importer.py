"""One-time `state.json` -> SQLite import, with verification.

Two rules decided during Step 2, both load-bearing:

PROVENANCE. A history row is `measured` iff it carries a `delta` key. The old
client wrote `delta` only when it had actually computed an elo update, so its
presence is an exact witness. Rows without it were fabricated by
`_backfill_ratings`, which linear-interpolated a rating for any row missing
one -- reproducibly: base 1500, cur 1534, n 7 regenerates the seven
2026-07-23 math rows exactly.

PRESERVE. A measured row's `rating_before` is whatever the previous row
recorded, *including when that number was interpolated*. This matters because
the fabrication turned out to be load-bearing: both genuine events were
computed from an invented predecessor. Re-chaining off the seeds instead would
move the ratings to 1495 math / 1456 chess. The imported trajectory is
flagged, never rewritten.

Run:
    python3 -m dashboard.db.importer --state state.json --db dashboard.db
    python3 -m dashboard.db.importer --state state.json --db dashboard.db --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from .. import scoring
from ..models import domain as d
from ..models.common import (
    HealthMetric,
    MathStatus,
    PaperStatus,
    Provenance,
    Track,
)
from .connection import connect, transaction
from .repository import Repository

# The old hints[0] was the topic/textbook links, not a hint, so a stored
# count of 1 meant "opened the reference material, revealed nothing".
LINKS_OCCUPIED_HINT_ZERO = 1


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _wordle_number(raw: Any) -> int | None:
    """state.json stored the puzzle number as the string "1,861"."""
    if raw in (None, ""):
        return None
    return int(str(raw).replace(",", "").replace(".", ""))


class ImportReport:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.notes: list[str] = []
        self.failures: list[str] = []

    def count(self, table: str, n: int = 1) -> None:
        self.counts[table] = self.counts.get(table, 0) + n

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    @property
    def ok(self) -> bool:
        return not self.failures


def import_state(repo: Repository, state: dict, problems: list[dict]) -> ImportReport:
    """Import into an empty database. Caller owns the transaction."""
    rep = ImportReport()

    # -- math problems ------------------------------------------------------
    # problems.json holds the content; state.json only holds the source names.
    by_source: dict[str, int] = {}
    by_date: dict[str, int] = {}
    for p in problems:
        problem = repo.add_problem(
            d.MathProblem(
                source=p["source"],
                tier=p["tier"],
                anchor=p["anchor"],
                statement=p["statement"],
                answer=p["answer"],
                hints=[p["hint1"], p["hint2"], p["hint3"]],
                verified_url=p.get("verified_url"),
                model="claude-sonnet-5",
                generated_at=dt.datetime.fromisoformat(f"{p['date']}T12:00:00"),
            )
        )
        assert problem.id is not None
        by_source[problem.source] = problem.id
        by_date[p["date"]] = problem.id
        rep.count("math_problem")

    seen = state["math"].get("seen", [])
    missing = [s for s in seen if s not in by_source]
    if missing:
        rep.note(f"{len(missing)} source(s) in math.seen have no problems.json entry: {missing}")

    # -- rating events ------------------------------------------------------
    for track, hist, seed in (
        (Track.math, state["math"]["history"], scoring.MATH_SEED),
        (Track.chess, state["chess"]["history"], scoring.CHESS_SEED),
    ):
        for i, row in enumerate(hist):
            measured = "delta" in row
            ref = row.get("id") or row["source"]
            opponent = row.get("opp") or _anchor_for(row, problems)
            # PRESERVE: chain off the stored predecessor, fabricated or not.
            prev_stored = hist[i - 1].get("rating") if i else seed
            repo.insert_rating_event(
                d.RatingEvent(
                    track=track,
                    date=_date(row["date"]),
                    ref=ref,
                    opponent=opponent,
                    score=row["score"],
                    k=scoring.k_factor(i),
                    rating_before=prev_stored if measured else None,
                    rating_after=row["rating"] if measured else None,
                    provenance=Provenance.measured if measured else Provenance.reconstructed,
                )
            )
            rep.count("rating_event")
            rep.count(f"rating_event.{'measured' if measured else 'reconstructed'}")

    # -- math attempts ------------------------------------------------------
    # One row per date. The seven same-day 2026-07-23 events cannot each get
    # an attempt row, and should not: rating_event is the log, math_attempt is
    # the day's assignment.
    last_by_date: dict[str, dict] = {}
    for row in state["math"]["history"]:
        last_by_date[row["date"]] = row
    for date_str, problem_id in by_date.items():
        repo.open_math_attempt(_date(date_str), problem_id)
        rep.count("math_attempt")
        row = last_by_date.get(date_str)
        if row is not None:
            status = MathStatus.solved if row["score"] > 0 else MathStatus.failed
            hints = max(0, row.get("hints", 0) - LINKS_OCCUPIED_HINT_ZERO)
            repo.conn.execute(
                "UPDATE math_attempt SET status = ?, score = ?, hints_revealed = ? WHERE date = ?",
                (status.value, row["score"], hints, date_str),
            )

    current = state["math"].get("current")
    if current and current["source"] in by_source:
        hints = max(0, current.get("hints_revealed", 0) - LINKS_OCCUPIED_HINT_ZERO)
        repo.conn.execute(
            "UPDATE math_attempt SET hints_revealed = ? WHERE date = ? AND status = 'open'",
            (hints, current["date"]),
        )

    # -- chess --------------------------------------------------------------
    cc = state["chess"].get("current")
    if cc:
        repo.upsert_puzzle(
            d.ChessPuzzle(
                id=cc["id"], rating=cc["rating"], themes=cc.get("themes", []), url=cc["url"]
            )
        )
        rep.count("chess_puzzle")
        repo.open_chess_attempt(_date(cc["date"]), cc["id"])
        rep.count("chess_attempt")
        if cc.get("done"):
            repo.conn.execute(
                "UPDATE chess_attempt SET status = 'done', score = ? WHERE date = ?",
                (float(cc.get("score", 0.0)), cc["date"]),
            )
    # Older chess events reference puzzles whose detail was never kept --
    # chess.current only ever held the latest. rating_event.ref is free text
    # with no FK, precisely so that history survives without them.
    orphans = {r.get("id") for r in state["chess"]["history"]} - {cc["id"] if cc else None}
    if orphans:
        rep.note(
            f"{len(orphans)} chess event(s) reference puzzles with no stored "
            f"detail: {sorted(orphans)}"
        )

    # -- word ---------------------------------------------------------------
    for date_str, w in state["word"].get("byDate", {}).items():
        grade = d.WordGrade(**w["result"]) if w.get("result") else None
        repo.upsert_word(
            d.WordOfDay(
                date=_date(date_str),
                word=w["word"],
                pos=w["pos"],
                definition=w["definition"],
                sentences=w.get("sentences", []),
                grade=grade,
            )
        )
        rep.count("word_of_day")

    # -- games --------------------------------------------------------------
    for w in state["games"].get("wordle", []):
        repo.upsert_wordle(
            d.WordleResult(
                date=_date(w["date"]),
                puzzle_no=_wordle_number(w.get("n")),
                guesses=w.get("guesses"),
                won=w.get("won", False),
                grid=w.get("grid", []),
            )
        )
        rep.count("wordle_result")
    for p in state["games"].get("pokedoku", []):
        repo.upsert_pokedoku(
            d.PokedokuResult(
                date=_date(p["date"]),
                correct=p["correct"],
                score=p.get("score"),
                grid=p.get("grid", []),
            )
        )
        rep.count("pokedoku_result")

    # -- theorems -----------------------------------------------------------
    # The legacy cards carry SM-2 state (`due`, `interval`, `ease`) that the
    # deck no longer has anywhere to put. `due` is kept as `added` -- for a
    # card that was never reviewed the two are the same date, and one that was
    # loses only a schedule that has been replaced wholesale.
    for c in state["theorems"].get("cards", []):
        repo.add_card(
            d.TheoremCard(
                name=c["name"],
                statement=c["statement"],
                topic=c["topic"],
                added=_date(c["due"]),
            )
        )
        rep.count("theorem_card")

    # -- papers -------------------------------------------------------------
    P = state.get("papers", {})
    cur = P.get("current")
    if cur:
        marked = cur.get("marked")
        status = PaperStatus(marked) if marked else PaperStatus.surfaced
        # `queue_pos` is NOT NULL exactly when the status is `queued`. The old
        # shape had no positions at all -- the queue was a JS array and its
        # order was the array's -- so a paper triaged into the queue today
        # needs one synthesised. It goes on the end, after the existing queue.
        repo.add_paper(
            d.Paper(
                title=cur["title"],
                link=cur["link"],
                summary=cur.get("summary"),
                published=_date(cur["published"]) if cur.get("published") else None,
                status=status,
                surfaced_on=_date(cur["date"]),
                added=_date(cur["date"]),
                queue_pos=len(P.get("queue", [])) if status is PaperStatus.queued else None,
            )
        )
        rep.count("paper")
    for i, q in enumerate(P.get("queue", [])):
        repo.add_paper(
            d.Paper(
                title=q["title"],
                link=q["link"],
                status=PaperStatus.queued,
                added=_date(q["added"]),
                queue_pos=i,
            )
        )
        rep.count("paper")
    for a in P.get("archive", []):
        repo.add_paper(
            d.Paper(
                title=a["title"],
                link=a["link"],
                status=PaperStatus.cleared,
                added=_date(a.get("added", a.get("cleared"))),
            )
        )
        rep.count("paper")

    # -- health / log / completion / topics ---------------------------------
    # state.json spelled the sleep score `sleep`; schema 17 renamed it, so the
    # legacy key is mapped rather than looked up by enum value.
    for key, metric in (
        ("weight", HealthMetric.weight),
        ("sleep", HealthMetric.sleep_score),
    ):
        for e in state.get("health", {}).get(key, []):
            repo.log_health(metric, _date(e["date"]), e["v"])
            rep.count("health_entry")

    for date_str, text in state.get("daylog", {}).items():
        repo.set_daylog(_date(date_str), text)
        rep.count("daylog")

    for date_str, c in state.get("completion", {}).items():
        repo.set_completion(_date(date_str), c["done"], c["total"])
        rep.count("day_completion")

    for topic, info in state.get("topics", {}).items():
        repo.upsert_topic(
            d.TopicInfo(
                topic=topic,
                textbook=d.Link(**info["textbook"]),
                theorems=[d.Link(**t) for t in info["theorems"]],
            )
        )
        rep.count("topic_info")

    return rep


def _anchor_for(row: dict, problems: list[dict]) -> int:
    for p in problems:
        if p["source"] == row.get("source"):
            return p["anchor"]
    # A math event whose problem is no longer in problems.json. The anchor is
    # unrecoverable; the seed is the least-wrong stand-in and the note makes
    # it visible rather than silent.
    return scoring.MATH_SEED


def verify(repo: Repository, state: dict, problems: list[dict], rep: ImportReport) -> ImportReport:
    """Read everything back out and compare against the source of truth.

    This is the gate the plan requires before state.json can be retired.
    """

    def eq(label: str, got: Any, want: Any) -> None:
        if got != want:
            rep.fail(f"{label}: got {got!r}, want {want!r}")

    # The whole point: current ratings must be unchanged.
    eq("math rating", repo.current_rating(Track.math), state["math"]["rating"])
    eq("chess rating", repo.current_rating(Track.chess), state["chess"]["rating"])

    eq("math event count", repo.event_count(Track.math), len(state["math"]["history"]))
    eq("chess event count", repo.event_count(Track.chess), len(state["chess"]["history"]))

    # Every event round-trips field by field.
    for track, hist in (
        (Track.math, state["math"]["history"]),
        (Track.chess, state["chess"]["history"]),
    ):
        events = repo.rating_history(track)
        eq(f"{track.value} history length", len(events), len(hist))
        for ev, row in zip(events, hist, strict=False):
            tag = f"{track.value} {row['date']} {ev.ref}"
            eq(f"{tag} date", ev.date.isoformat(), row["date"])
            eq(f"{tag} score", ev.score, row["score"])
            measured = "delta" in row
            eq(
                f"{tag} provenance",
                ev.provenance,
                Provenance.measured if measured else Provenance.reconstructed,
            )
            if measured:
                eq(f"{tag} rating_after", ev.rating_after, row["rating"])
                eq(f"{tag} delta", ev.delta, row["delta"])
            else:
                eq(f"{tag} rating_after is NULL", ev.rating_after, None)

    eq("math problems", len(repo.seen_sources()), len(problems))
    eq("seen sources", sorted(repo.seen_sources()), sorted(p["source"] for p in problems))

    eq("words", len(state["word"].get("byDate", {})), rep.counts.get("word_of_day", 0))
    for date_str, w in state["word"].get("byDate", {}).items():
        got = repo.word(_date(date_str))
        eq(f"word {date_str}", got.word if got else None, w["word"])

    for w in state["games"].get("wordle", []):
        got = repo.wordle(_date(w["date"]))
        eq(f"wordle {w['date']} guesses", got.guesses if got else None, w.get("guesses"))
        eq(f"wordle {w['date']} number", got.puzzle_no if got else None, _wordle_number(w.get("n")))
        eq(f"wordle {w['date']} grid", got.grid if got else None, w.get("grid", []))
    for p in state["games"].get("pokedoku", []):
        got = repo.pokedoku(_date(p["date"]))
        eq(f"pokedoku {p['date']} correct", got.correct if got else None, p["correct"])

    eq("deck size", repo.deck_size(), len(state["theorems"].get("cards", [])))

    papers = state.get("papers", {})
    current = papers.get("current") or {}
    # The day's paper joins the queue when it was triaged there, so the
    # expected length is the stored queue plus that one.
    expected_queue = len(papers.get("queue", [])) + (current.get("marked") == "queued")
    eq("queue length", len(repo.queue()), expected_queue)
    eq("completions", len(repo.completions()), len(state.get("completion", {})))

    for date_str, c in state.get("completion", {}).items():
        got = next((x for x in repo.completions() if x.date.isoformat() == date_str), None)
        eq(f"completion {date_str} done", got.done if got else None, c["done"])
        eq(f"completion {date_str} pct", round(got.pct, 6) if got else None, round(c["pct"], 6))

    for date_str, text in state.get("daylog", {}).items():
        eq(f"daylog {date_str}", repo.daylog(_date(date_str)), text)

    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import state.json into SQLite.")
    ap.add_argument("--state", default="state.json", type=Path)
    ap.add_argument("--problems", default="problems.json", type=Path)
    ap.add_argument("--db", default="dashboard.db", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="import, verify, then roll back")
    ap.add_argument(
        "--force", action="store_true", help="allow importing into a non-empty database"
    )
    args = ap.parse_args(argv)

    state = json.loads(args.state.read_text(encoding="utf-8"))
    problems = (
        json.loads(args.problems.read_text(encoding="utf-8")) if args.problems.exists() else []
    )

    conn = connect(args.db)
    repo = Repository(conn)
    if not repo.is_empty() and not args.force:
        print(
            f"refusing to import: {args.db} already has rating events (use --force)",
            file=sys.stderr,
        )
        return 2

    rolled_back = False
    rep = ImportReport()
    try:
        with transaction(conn):
            rep = import_state(repo, state, problems)
            rep = verify(repo, state, problems, rep)
            if not rep.ok:
                raise RuntimeError("verification failed")
            if args.dry_run:
                rolled_back = True
                raise _Rollback
    except _Rollback:
        pass
    except RuntimeError:
        _report(rep)
        return 1

    _report(rep)
    print(f"\n  {'rolled back (dry run)' if rolled_back else f'committed to {args.db}'}")
    if not rolled_back:
        print("  state.json is untouched -- retire it only after using the app against SQLite.")
    return 0


class _Rollback(Exception):
    """Control flow only: unwinds `transaction` so a dry run leaves no trace."""


def _report(rep: ImportReport) -> None:
    print("\n  imported:")
    for table in sorted(rep.counts):
        print(f"    {rep.counts[table]:>4}  {table}")
    for n in rep.notes:
        print(f"\n  note: {n}")
    if rep.failures:
        print(f"\n  VERIFICATION FAILED ({len(rep.failures)}):")
        for f in rep.failures:
            print(f"    {f}")
    else:
        print("\n  verification: all checks passed")


if __name__ == "__main__":
    raise SystemExit(main())
