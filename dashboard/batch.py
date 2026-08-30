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
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import content, llm, logs, proofbook, tricks
from .config import Settings, load_settings
from .db import Repository
from .db.schema import migrate
from .models import domain as d
from .models.batch import DailyBatch
from .models.common import DayMode, ProblemKind

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


COMPUTE_SCHEMA = (
    "Write the file as JSON:\n"
    '{"date":"the date in this payload","topic":"the topic, verbatim",'
    '"generated_at":"current ISO 8601 timestamp","problems":['
    '{"source":"e.g. Putnam 1998 B3","tier":"one of suggested_tiers",'
    '"kind":"compute","statement":"the problem, LaTeX between $..$ or $$..$$",'
    '"answer":"the final answer","hints":["direction, under five words",'
    '"minor hint","major hint"],"verified_url":"where you confirmed it"}]}\n'
    "One problem per suggested tier, easiest first, all different. Real problems "
    "from past competitions only (AIME, AMC, Putnam, IMO, USAMO, national "
    "olympiad archives), none of them in do_not_use, and preferring ones that "
    "exercise the week's topic.\n"
    "VERIFY EACH ONE WITH WEB SEARCH BEFORE WRITING IT DOWN: that it genuinely "
    "appeared in the competition you cite, that your statement matches the "
    "original, and that your answer is right. Discard anything you cannot "
    "confirm and verify a replacement. `verified_url` is the evidence; a missing "
    "one is read as 'not actually checked' and the file is rejected."
)
"""What a calibrated day must produce.

Lives here rather than in the scheduled task's prompt, which is the whole point
of `--context`: a schema in a prompt is a copy of this file that nothing keeps
in step, and it goes stale silently -- the first proof day would have been
generated against the compute shape by a task nobody had remembered to edit.
"""

PROOF_SCHEMA = (
    "Write the file as JSON, with exactly ONE problem in it:\n"
    '{"date":"the date in this payload","topic":"the topic, verbatim",'
    '"generated_at":"current ISO 8601 timestamp","problems":['
    '{"source":"P&B <problem number as the book prints it>",'
    '"tier":"one of the tiers in `proof.tiers`","kind":"proof",'
    '"statement":"the problem, quoted as printed, LaTeX between $..$ or $$..$$",'
    '"answer":"three to six sentences sketching the BOOK\'S solution",'
    '"hints":["the nudge","the one that gives the argument away"],'
    '"rubric":["three or four checkpoints a complete proof hits"],'
    '"location":"section number, its title, and the printed page"}]}\n'
    "No `verified_url` — the page is the provenance. Exactly two hints and at "
    "least one rubric line, both written from the printed solution rather than "
    "from your own."
)

READING_SCHEMA = (
    "Write the file as JSON:\n"
    '{"date":"the date in this payload","topic":"the topic, verbatim",'
    '"book":"the book you drew it from","segments":[{"title":"short name",'
    '"reading":"section, pages, and where to stop","problems":["problem numbers '
    'as the book prints them"],"minutes":30,"why":"one line on what it buys"}]}'
)


def context(settings: Settings, repo: Repository, date: dt.date) -> dict[str, Any]:
    """Everything the generator needs for one day, as one JSON object.

    What it needs now depends on what the day is. A compute day gets what it
    always got -- a rating and a tier window. A proof day gets a page range out
    of a book instead, and the rating only decides which rung of the band to
    aim at. A reading day gets neither: see `reading_context`.
    """
    mode = settings.math.mode_for(date)
    if mode is DayMode.reading:
        return reading_context(settings, repo, date)

    rating = content.target_rating(settings, repo, date)
    seen = sorted(repo.seen_sources())
    showcase = content.is_showcase(settings, date)
    payload: dict[str, Any] = {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "mode": mode.value,
        "topic": settings.math.topic_for(date),
        "rating": rating,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, rating, showcase=showcase),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": seen[-content.SEEN_WINDOW :],
        "already_generated": batch_path(settings, date).exists(),
        "showcase": SHOWCASE_BRIEF if showcase else None,
        "schema": PROOF_SCHEMA if mode.is_proof else COMPUTE_SCHEMA,
    }
    if mode.is_proof:
        payload.update(proof_context(settings, date, mode))
    return payload


def context_without_db(settings: Settings, date: dt.date, why: str) -> dict[str, Any]:
    """What can still be said when the database is unreadable.

    The topic rotation and the tier table live in config.toml, so the
    generator keeps its two most important instructions. It loses the rating
    (so the ladder is centred on the seed instead) and the do-not-repeat list
    (so a duplicate becomes possible -- caught on import by the UNIQUE
    constraint, at the cost of a rung).
    """
    seed = settings.math.anchors[settings.math.default_tier]
    mode = settings.math.mode_for(date)
    showcase = content.is_showcase(settings, date)
    payload: dict[str, Any] = {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "mode": mode.value,
        "topic": settings.math.topic_for(date),
        "rating": None,
        "anchors": settings.math.anchors,
        "suggested_tiers": suggested_tiers(settings, seed, showcase=showcase),
        "output_path": str(batch_path(settings, date)),
        "do_not_use": [],
        "already_generated": batch_path(settings, date).exists(),
        # Unaffected by the outage: the week's shape, the showcase and the
        # proof pages all come from config and the committed index, and both
        # are still readable. Losing the database costs the rating and the
        # do-not-repeat list and nothing else.
        "showcase": SHOWCASE_BRIEF if showcase else None,
        "schema": PROOF_SCHEMA if mode.is_proof else COMPUTE_SCHEMA,
        "degraded": (
            f"could not read the database ({why}). Rating and the do-not-repeat list "
            f"are unavailable; the tier ladder is centred on the default tier instead. "
            f"Generate anyway — a repeat is rejected on import, not served."
        ),
    }
    if mode.is_proof:
        payload.update(proof_context(settings, date, mode))
    return payload


def batch_path(settings: Settings, date: dt.date) -> Path:
    return settings.path("batches") / f"{date.isoformat()}.json"


# --- the proof days --------------------------------------------------------

PROOF_BRIEF = (
    "PROOF DAY. The problem comes out of the book named below and nowhere else. "
    "Open the PDF at the pages given, read the section, and choose ONE problem "
    "from it — do not write a problem, do not recall one, and do not adapt one. "
    "Quote the statement exactly as printed. Then open the solutions pages and "
    "write the hints and the rubric FROM THE BOOK'S OWN SOLUTION, so that the "
    "hints unlock the intended argument rather than some other one.\n"
    "`source` must be \"P&B <problem number>\" using the number the book prints, "
    "which runs once through the whole volume and is therefore unique. `location` "
    "is the section number, its title and the printed page. `kind` is \"proof\". "
    "`answer` is a three-to-six sentence sketch of the book's solution — not a "
    "final answer, because there is not one. `hints` is exactly TWO: the first a "
    "nudge, the observation you would have made after another twenty minutes; the "
    "second more or less hands over the argument. `rubric` is three or four short "
    "checkpoints a complete proof has to hit, each one checkable by reading a "
    "write-up — \"showed the map is injective\", \"handled the n=1 case\"."
)
"""What a proof day asks for that a compute day does not.

Every clause here is aimed at one failure. A model asked for "a problem from
Gelca-Andreescu on polynomials" with no book in front of it produces something
that reads exactly like one and is not in it -- a drifted statement, an invented
number, and a solution that was never printed. `verified_url`, the one
provenance check the math batch had, does not exist for a textbook. So the
instruction is not "recall", it is "open these pages and read them".
"""

TWO_DAY_BRIEF = (
    "This is the TWO-DAY problem. Day one offers no hints at all and day two "
    "offers both, so the first hint has to be worth waiting a day for and the "
    "second has to be enough to finish on. Pick something that rewards playing "
    "around — a problem whose idea arrives rather than one whose method is "
    "recognisable from the statement."
)


def proof_context(settings: Settings, date: dt.date, mode: DayMode) -> dict[str, Any]:
    """The pages to read, and what to make of them.

    Everything here comes out of `proofbook.json` rather than out of the
    model's memory of the book, which is the whole point -- see
    `dashboard/proofbook.py`. Degrades to a note rather than raising when the
    index is missing: the night still produces something if the file was never
    built, and it says what is wrong instead of failing silently.
    """
    book = proofbook.load(settings.path("proofbook"))
    brief = PROOF_BRIEF + ("\n" + TWO_DAY_BRIEF if mode is DayMode.proof_long else "")
    if book is None:
        return {
            "proof": {
                "brief": brief,
                "degraded": (
                    f"no proof-textbook index at {settings.path('proofbook')}. Build it with "
                    f"`python -m dashboard.proofbook <pdf>`; without it there are no page "
                    f"numbers and a problem must not be written from memory."
                ),
            }
        }

    topic = settings.math.topic_for(date)
    sections = proofbook.sections_for(
        book, topic, settings.math.area_for(date), override=settings.math.section_for(date)
    )
    return {
        "proof": {
            "brief": brief,
            "book": book.title,
            "pdf": str((settings.root / settings.math.proof.pdf).resolve()),
            "tiers": settings.math.tiers_for(mode),
            "sections": [
                {
                    "number": s.number,
                    "title": f"{s.parent} / {s.title}" if s.parent else s.title,
                    "pdf_pages": [s.pdf_page, s.pdf_end_page],
                    "printed_pages": [s.page, s.end_page],
                    "problems": [s.first_problem, s.last_problem],
                    "solutions_pdf_pages": list(pages)
                    if (pages := proofbook.solution_pages(book, s))
                    else None,
                }
                for s in sections
            ],
        }
    }


# --- sunday ----------------------------------------------------------------

READING_BRIEF = (
    "READING DAY. No problem today. Produce a study plan for the week's topic: "
    "{segments} segments of about {minutes} minutes each, of which one or two "
    "will actually be done. Order them so the first is the most useful to "
    "someone who has never met the topic and the last is the most useful to "
    "someone who has.\n"
    "The point of the day is to get used to the objects — many easy problems "
    "rather than one hard one — so each segment names what to read WITH PAGE "
    "NUMBERS and then a list of problem numbers to work, weighted to the easy "
    "end of the section. `why` is one line on what the segment buys, so choosing "
    "between them is a decision rather than a guess.\n"
    "Write it as JSON: {{\"date\":\"the date in this payload\",\"topic\":\"the topic, "
    "verbatim\",\"book\":\"the book you drew it from\",\"segments\":[{{\"title\":\"...\","
    "\"reading\":\"section, pages, and where to stop\",\"problems\":[\"numbers as the "
    "book prints them\"],\"minutes\":{minutes},\"why\":\"one line\"}}]}}"
)


def reading_context(settings: Settings, repo: Repository, date: dt.date) -> dict[str, Any]:
    """Sunday: what to read and what to drill, plus what was skipped before.

    The history is the half that makes this worth generating weekly rather than
    once. Three segments are produced and one or two are done, so what was
    skipped -- and the one word saying why -- is the only signal that stops the
    next plan opening with the introduction you already knew.
    """
    cfg = settings.math.reading
    book = proofbook.load(settings.path("proofbook"))
    topic = settings.math.topic_for(date)
    path = reading_path(settings, date)

    sections = []
    if book is not None:
        sections = [
            {
                "number": s.number,
                "title": f"{s.parent} / {s.title}" if s.parent else s.title,
                "pdf_pages": [s.pdf_page, s.pdf_end_page],
                "printed_pages": [s.page, s.end_page],
                "problems": [s.first_problem, s.last_problem],
            }
            for s in proofbook.sections_for(
                book,
                topic,
                settings.math.area_for(date),
                override=settings.math.section_for(date),
            )
        ]

    history = [
        {
            "date": p.date.isoformat(),
            "topic": p.topic,
            "segments": [s.title for s in p.segments],
            "done": p.done,
            "skipped": [{"index": s.index, "reason": s.reason} for s in p.skipped],
        }
        for p in repo.recent_study_plans()
    ]

    return {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "mode": DayMode.reading.value,
        "enabled": cfg.enabled,
        "topic": topic,
        "output_path": str(path),
        "already_generated": path.exists(),
        "book": book.title if book else None,
        "pdf": str((settings.root / settings.math.proof.pdf).resolve()),
        "sections": sections,
        "recent": history,
        "instructions": READING_BRIEF.format(
            segments=cfg.segments, minutes=cfg.segment_minutes
        ),
        "schema": READING_SCHEMA,
    }


def reading_path(settings: Settings, date: dt.date) -> Path:
    """Beside the math batch, prefixed the way the trick's file is -- one
    directory, one file per day per kind, and the prefix is what keeps a
    reading plan from being read as a ladder."""
    return settings.path("batches") / f"reading-{date.isoformat()}.json"


def validate_reading(
    settings: Settings, raw: str, date: dt.date | None = None
) -> tuple[Any, list[str]]:
    """Parse and check a generated reading plan, for the CLI.

    Strict about the two things a plan is for. Every segment must name pages --
    a segment that says "read about Viete" is one you have to plan before you
    can start, which is the work this is supposed to have done -- and every
    segment must carry problems, because "many easy problems" is the whole
    method of the day and a plan that is only reading is a plan that skipped it.
    """
    errors: list[str] = []
    try:
        parsed = llm.extract_json(raw)
    except json.JSONDecodeError as e:
        return None, [f"not JSON: {e}"]

    try:
        plan = d.StudyPlan(
            **{**parsed, "model": "scheduled-task", "generated_at": dt.datetime.now()}
        )
    except Exception as e:
        return None, [f"does not match the study-plan schema: {e}"]

    if date is not None and plan.date != date:
        errors.append(f"the plan is dated {plan.date}, but was asked for {date}")
    if plan.topic != settings.math.topic_for(plan.date):
        errors.append(
            f"topic {plan.topic!r} is not this week's ({settings.math.topic_for(plan.date)!r})"
        )
    want = settings.math.reading.segments
    if len(plan.segments) != want:
        errors.append(f"expected {want} segments, got {len(plan.segments)}")

    for i, segment in enumerate(plan.segments):
        if not re.search(r"\d", segment.reading):
            errors.append(f"segments[{i}] ({segment.title}): no page or section number to open")
        if not segment.problems:
            errors.append(f"segments[{i}] ({segment.title}): no problems to work")

    return plan, errors


# --- the week --------------------------------------------------------------


def week_start(date: dt.date) -> dt.date:
    """The Sunday the coming week begins on -- `date` itself if it is one.

    Sunday, because that is when the topic rotation turns over
    (`config.week_index`) and when `[math.week]` starts. Run on Saturday night
    this is tomorrow, which is the point: the job generates the week it is
    about to be, not the one that just ended.
    """
    return date + dt.timedelta(days=(6 - date.weekday()) % 7)


WEEK_BRIEF = (
    "Generate the whole week in one sitting, in the order given. The days are "
    "not independent: Sunday's reading is what the proof days are supposed to "
    "make use of, so choose the reading first and then choose problems that "
    "reward having done it. Write each day to its own `output_path`.\n"
    "Friday has no file of its own — it is the second day of Thursday's "
    "problem, and the dashboard serves the carried problem rather than drawing. "
    "Generate a Friday file only if you are told to below."
)


def week_context(settings: Settings, repo: Repository, date: dt.date) -> dict[str, Any]:
    """Every day of the coming week, as one payload.

    A week rather than a night because the thing being planned is week-shaped.
    Sunday's reading should feed Wednesday's problem; the two-day problem spans
    two nights anyway; and the proof days all read the same book, so doing them
    together is one pass over a section instead of four.

    Friday is present but carries no `output_path` work: it continues Thursday's
    problem. Included rather than omitted so the payload is a week and not six
    sevenths of one -- a generator handed six days will wonder about the
    seventh, and the answer is worth writing down once.
    """
    start = week_start(date)
    days = []
    for offset in range(7):
        day = start + dt.timedelta(days=offset)
        if settings.math.is_second_day(day):
            days.append(
                {
                    "date": day.isoformat(),
                    "weekday": day.strftime("%A"),
                    "mode": settings.math.mode_for(day).value,
                    "generate": False,
                    "note": (
                        "Day two of Thursday's problem. Nothing to generate: the dashboard "
                        "serves the problem carried from yesterday, and the two hints written "
                        "for it become available today. If Thursday was solved on the day, "
                        "this is a free day and the panel asks for a problem of your own."
                    ),
                }
            )
            continue
        days.append({**context(settings, repo, day), "generate": True})

    return {
        "week_of": start.isoformat(),
        "topic": settings.math.topic_for(start),
        "brief": WEEK_BRIEF,
        "days": days,
    }


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


def _report_reading(settings: Settings, path: Path, date: dt.date) -> int:
    """`--validate-reading`, printed. Same contract as the reports around it."""
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    plan, errors = validate_reading(settings, path.read_text(encoding="utf-8"))
    if plan is None or errors:
        print(f"REJECTED {path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"OK {path}: {plan.date} — {plan.topic}, {len(plan.segments)} segment(s), {plan.book}")
    for segment in plan.segments:
        print(
            f"  {segment.minutes:>3}m  {segment.title[:38]:<40} {segment.reading[:34]:<36} "
            f"{len(segment.problems)} problem(s)"
        )
    return 0


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

        # Read-write on the *copy*, and migrated. Neither weakens the guarantee
        # above -- the live file is untouched either way -- and skipping the
        # migration cost a real failure: the CLI runs at 3am against whatever
        # schema the server last started with, so a night between a migration
        # landing in the code and the server being restarted found the copy a
        # version behind. Every query against a new table raised, the whole
        # week fell into the degraded path, and the payload said "could not
        # read the database" when the database was fine.
        conn = sqlite3.connect(tmp / db_path.name)
        conn.row_factory = sqlite3.Row
        try:
            migrate(conn)
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
        where = f"problems[{i}] ({p.source})"
        if p.tier not in settings.math.anchors:
            errors.append(
                f"{where}: unknown tier {p.tier!r}; "
                f"expected one of {sorted(settings.math.anchors)}"
            )
        if p.kind not in set(ProblemKind):
            errors.append(f"{where}: unknown kind {p.kind!r}")
            continue

        # Provenance, and it is a different object for each kind. A competition
        # problem is checked against an archive and the URL is the evidence; a
        # textbook problem was read off a page, and the page is. Demanding a URL
        # for both -- which this did -- would have rejected every proof batch
        # ever generated, on the grounds that a book is not a web page.
        if p.kind == ProblemKind.proof:
            if not p.location:
                errors.append(f"{where}: no location — which section and page is it on?")
            if not p.rubric:
                errors.append(f"{where}: no rubric — `partial` has nothing to mean without one")
            if len(p.hints) != 2:
                errors.append(f"{where}: a proof takes exactly 2 hints, got {len(p.hints)}")
        else:
            if not p.verified_url:
                errors.append(f"{where}: no verified_url — was it actually checked?")
            if len(p.hints) != 3:
                errors.append(
                    f"{where}: a compute problem takes exactly 3 hints, got {len(p.hints)}"
                )
            if p.rubric:
                errors.append(f"{where}: a rubric belongs to a proof; there is nothing to itemise")
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
    ap.add_argument("--context", action="store_true", help="print what one day's generator needs")
    ap.add_argument(
        "--week",
        action="store_true",
        help="print the whole coming week, Sunday first — the weekly job's input",
    )
    ap.add_argument("--validate", metavar="FILE", type=Path, help="check a generated batch")
    ap.add_argument(
        "--validate-reading", metavar="FILE", type=Path, help="check a generated reading plan"
    )
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
        args.week,
        args.validate,
        args.validate_reading,
        args.trick_context,
        args.validate_trick,
    ]
    if not any(wanted):
        ap.error(
            "one of --context, --week, --validate, --validate-reading, --trick-context "
            "or --validate-trick is required"
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

    if args.week:
        try:
            with snapshot(settings.path("db")) as repo:
                print(json.dumps(week_context(settings, repo, date), indent=2))
        except (OSError, sqlite3.Error) as e:
            # Degraded like `--context`, and for once genuinely worse: a week
            # generated without the do-not-repeat list can repeat four problems
            # rather than one. Every repeat is still caught on import by the
            # UNIQUE constraint, so the cost is a day with nothing rather than a
            # day with something wrong.
            log.exception("could not read %s", settings.path("db"))
            start = week_start(date)
            days = []
            for offset in range(7):
                day = start + dt.timedelta(days=offset)
                days.append(
                    {
                        **context_without_db(settings, day, str(e)),
                        "generate": not settings.math.is_second_day(day),
                    }
                )
            print(
                json.dumps(
                    {
                        "week_of": start.isoformat(),
                        "topic": settings.math.topic_for(start),
                        "brief": WEEK_BRIEF,
                        "days": days,
                    },
                    indent=2,
                )
            )
        return 0

    if args.validate_reading:
        return _report_reading(settings, args.validate_reading, date)

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
