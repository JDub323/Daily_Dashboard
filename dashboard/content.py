"""LLM-backed daily content: the math problem, the word, the theorem card,
and the topic's reference links.

This is where the prompts live. It is deliberately the only module that both
talks to `llm` and writes through `Repository`, so that "what a generation
costs" and "what it is allowed to persist" are answerable by reading one file.

Every paid call goes through `_generate_problem` or `_json_generation`, and
both append a line to metrics.jsonl. That is the invariant that makes the log
worth reading: if a call is not in the file, it did not happen.

`problems.json` is resolved here. It was tracked in git *and* appended to on
every generation, so it churned on every commit while claiming to be
hand-editable. It now has exactly one role: a hand-curated pool of problems to
serve before paying for a generation. The server never writes to it. Generated
problems live in the `math_problem` table, which is the append-only log the
file was pretending to be.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import llm, logs
from .config import Settings
from .db.repository import Repository
from .llm import LlmError
from .models import domain as d
from .models.api import TopicResponse

log = logs.get(__name__)

HAND_CURATED = "hand-curated"
"""The `model` recorded for a problem taken from the pool rather than
generated. Distinguishes a free problem from a paid one in the log."""

SEEN_WINDOW = 200
"""How many recent sources to name in the do-not-repeat list. The full list
grows without bound and the prompt is charged by the token."""


class ContentError(RuntimeError):
    """Generated content that cannot be used as-is."""


# --- the math problem ------------------------------------------------------

PROBLEM_SYSTEM = (
    "You select real competition math problems and VERIFY them with web search before "
    "answering. Steps: pick a candidate problem, then search (Art of Problem Solving wiki "
    "or official archives) to confirm (a) the problem really appeared in the cited "
    "competition, (b) your statement matches the original exactly, and (c) the answer is "
    "correct. If verification fails, pick another problem and verify again. After "
    "verifying, respond ONLY with JSON:\n"
    '{"source":"e.g. Putnam 1998 B3","tier":one of the tier keys,'
    '"statement":"problem in LaTeX ($..$ inline, $$..$$ display), matching the original",'
    '"answer":"final answer, LaTeX ok",'
    '"hint1":"direction, under five words","hint2":"minor hint","hint3":"major hint",'
    '"verified_url":"the URL where you confirmed the problem"}'
)


def _problem_prompt(settings: Settings, rating: int, date: dt.date, seen: list[str]) -> str:
    return (
        f"Topic of the week: {settings.math.topic_for(date)}. Today is weekday "
        f"{date.weekday()} (0=Mon: ramp difficulty through the week, but vary sub-skills "
        f"to teach the topic broadly). My rating: {rating}. Tier anchors: "
        f"{json.dumps(settings.math.anchors)}. Pick a tier whose anchor gives me a "
        f"30-70% solve chance, adjusted for weekday. Must be from a real past "
        f"competition. Do NOT use any of these already-seen problems: "
        f"{json.dumps(seen[-SEEN_WINDOW:])}"
    )


def load_pool(path: Path) -> list[dict[str, Any]]:
    """The hand-curated problem pool. Never written by the server.

    Tolerates the legacy shape: entries carry `textbook` and `theorems` keys
    that the current prompt no longer produces, and hints as hint1/hint2/hint3.
    Unknown keys are ignored rather than rejected, because this file is edited
    by a human and a stray key should not take the panel down.
    """
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("could not read the problem pool at %s", path)
        return []
    if not isinstance(entries, list):
        log.error("%s should hold a JSON list of problems", path)
        return []
    return entries


def to_problem(raw: dict[str, Any], settings: Settings, model: str) -> d.MathProblem:
    """Validate one generated or hand-written problem into the domain model.

    `MathProblem` forbids extra keys, so the mapping is explicit: this is the
    boundary where a malformed generation becomes a loud error instead of a
    half-populated dict that fails later in the browser.
    """
    missing = [k for k in ("source", "statement", "answer") if not raw.get(k)]
    if missing:
        raise ContentError(f"the model omitted {', '.join(missing)}")

    tier = raw.get("tier")
    if tier not in settings.math.anchors:
        log.warning("unknown tier %r; falling back to %s", tier, settings.math.default_tier)
        tier = settings.math.default_tier

    hints = raw.get("hints") or [raw.get(f"hint{i}") or "" for i in (1, 2, 3)]
    if len(hints) != 3:
        raise ContentError(f"expected 3 hints, got {len(hints)}")

    return d.MathProblem(
        source=raw["source"],
        tier=tier,
        anchor=settings.math.anchors[tier],
        statement=raw["statement"],
        answer=str(raw["answer"]),
        hints=[str(h) for h in hints],
        verified_url=raw.get("verified_url") or None,
        model=model,
        generated_at=dt.datetime.now(),
    )


def next_problem(
    settings: Settings,
    repo: Repository,
    date: dt.date,
    on_status: llm.StatusFn = None,
) -> d.MathProblem:
    """Today's problem, from the cheapest source that has one.

    Three sources, tried in order, and the ordering is the cost control:

    1. The overnight batch, dropped by the scheduled task. Free -- it was
       generated against a subscription rather than the API.
    2. The hand-curated pool in problems.json. Free.
    3. A paid generation, $0.15-0.80. (The old figure here was $0.02-0.04,
       taken from a metrics log that was undercounting web-search input
       tokens by ~5x.)

    Every step degrades to the next rather than failing, so a night the task
    did not run is a more expensive morning, not a broken one.
    """
    seen = set(repo.seen_sources())

    batched = _from_batch(settings, date, seen, repo.current_rating("math"))
    if batched is not None:
        log.info("serving %s from the overnight batch (no LLM call)", batched.source)
        return _store(repo, batched, date)

    for raw in load_pool(settings.path("problems")):
        if raw.get("source") and raw["source"] not in seen:
            log.info("serving %s from the hand-curated pool (no LLM call)", raw["source"])
            problem = to_problem(raw, settings, HAND_CURATED)
            return _store(repo, problem, date)

    return _store(repo, _generate_problem(settings, repo, date, sorted(seen), on_status), date)


BATCHED = "scheduled-task"
"""The `model` recorded for a problem that came from the overnight batch.
Distinguishes it in the log from a paid generation and from the pool."""


def _from_batch(
    settings: Settings, date: dt.date, seen: set[str], rating: int
) -> d.MathProblem | None:
    """The rung of today's ladder closest to the rating, or None.

    Returns None rather than raising for every failure mode -- no file, bad
    JSON, every rung already seen. A batch is an optimisation, and an
    optimisation that can take the math panel down is not one. Anything
    surprising goes to the log with a traceback; the caller just falls
    through to the pool.

    The ladder is ordered by the generator from easiest to hardest and each
    rung carries its own tier, so "closest to the rating" is a lookup against
    the configured anchors rather than anything the generator decided.
    """
    path = settings.path("batches") / f"{date.isoformat()}.json"
    if not path.exists():
        return None

    # Imported here rather than at module scope: `batch` imports this module
    # for `to_problem`, and the models package is the shared bottom layer.
    from .models.batch import DailyBatch

    try:
        batch = DailyBatch(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        log.exception("unusable batch at %s; falling through to the pool", path)
        return None

    usable = [p for p in batch.problems if p.source not in seen]
    if not usable:
        log.warning(
            "every problem in %s has been served already (%d rung(s)); falling through",
            path.name,
            len(batch.problems),
        )
        return None

    def distance(p: Any) -> int:
        anchor = settings.math.anchors.get(p.tier)
        # An unknown tier sorts last rather than crashing; `to_problem` will
        # substitute the default tier and log the substitution.
        return abs(anchor - rating) if anchor is not None else 10_000

    try:
        return to_problem(min(usable, key=distance).model_dump(), settings, BATCHED)
    except ContentError:
        log.exception("batch at %s did not validate; falling through to the pool", path)
        return None


def _store(repo: Repository, problem: d.MathProblem, date: dt.date) -> d.MathProblem:
    try:
        stored = repo.add_problem(problem)
    except sqlite3.IntegrityError as e:
        # `source` is UNIQUE: the model ignored the do-not-repeat list.
        # Not retried automatically -- a retry is another paid generation,
        # and the click that asks for one should be the user's.
        raise ContentError(
            f"the model returned a problem you have already seen ({problem.source})"
        ) from e
    assert stored.id is not None
    repo.open_math_attempt(date, stored.id)
    return stored


def _generate_problem(
    settings: Settings,
    repo: Repository,
    date: dt.date,
    seen: list[str],
    on_status: llm.StatusFn,
) -> d.MathProblem:
    rating = repo.current_rating("math")
    started = time.time()
    # Owned here rather than taken from the return value: a generation that
    # dies on its third web search has still been paid for, and the old code
    # left `metrics` as None on that path and logged the spend as nothing.
    metrics = llm.Metrics()
    problem: d.MathProblem | None = None
    error: str | None = None
    try:
        text, _ = llm.call(
            settings.llm,
            PROBLEM_SYSTEM,
            _problem_prompt(settings, rating, date, seen),
            tools=llm.web_search_tool(settings.llm.web_search_max_uses),
            on_status=on_status,
            metrics=metrics,
        )
        problem = to_problem(llm.extract_json(text), settings, settings.llm.model)
        return problem
    except json.JSONDecodeError as e:
        error = f"unparseable JSON: {e}"
        log.exception("math generation returned unusable JSON")
        raise ContentError("the model did not return usable JSON") from e
    except (LlmError, ContentError) as e:
        error = str(e)
        log.exception("math generation failed")
        raise
    finally:
        llm.log_generation(
            settings.path("metrics"),
            llm.record(
                kind="math",
                date=date,
                model=settings.llm.model,
                started=started,
                settings=settings.llm,
                metrics=metrics,
                ok=problem is not None,
                error=error,
                topic=settings.math.topic_for(date),
                rating=rating,
                source=problem.source if problem else None,
                tier=problem.tier if problem else None,
            ),
        )
        if on_status and problem is not None:
            on_status("done ✓")


# --- the smaller generations -----------------------------------------------


def _json_generation(
    settings: Settings,
    *,
    kind: str,
    date: dt.date,
    system: str,
    user: str,
    **extra: Any,
) -> Any:
    """One JSON generation, with its cost appended to metrics.jsonl.

    The math problem was logged from the day it was written. The word, the
    grader, the theorem card and the topic links called `llm.json_call`
    directly and were never recorded at all. They bill to the same API key and
    land on the same Anthropic invoice, so the log answered "what did today
    cost?" with one line item and silently dropped the rest -- worse than not
    answering, because it looks like an answer.

    Every paid call from this module now goes through here or through
    `_generate_problem`. There is deliberately no third path to `llm`.
    """
    started = time.time()
    metrics = llm.Metrics()  # owned here, so a failure is still logged with its cost
    error: str | None = None
    ok = False
    try:
        raw = llm.json_call(settings.llm, system, user, metrics=metrics)
        ok = True
        return raw
    except Exception as e:
        error = str(e) or type(e).__name__
        log.exception("%s generation failed", kind)
        raise
    finally:
        llm.log_generation(
            settings.path("metrics"),
            llm.record(
                kind=kind,
                date=date,
                model=settings.llm.model,
                started=started,
                settings=settings.llm,
                metrics=metrics,
                ok=ok,
                error=error,
                **extra,
            ),
        )


# --- word of the day -------------------------------------------------------


def word_of_day(settings: Settings, date: dt.date) -> d.WordOfDay:
    raw = _json_generation(
        settings,
        kind="word",
        date=date,
        system='Respond ONLY JSON: {"word":"...","pos":"...","definition":"..."}',
        user=f"Pick an uncommon-but-usable English word of the day for {date.isoformat()}. "
        f"Deterministic-ish: base the choice on that date.",
    )
    try:
        return d.WordOfDay(
            date=date, word=raw["word"], pos=raw["pos"], definition=raw["definition"]
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the word generation was missing {e}") from e


def grade_sentences(
    settings: Settings, date: dt.date, word: str, sentences: list[str]
) -> d.WordGrade:
    """Grade the five practice sentences.

    Takes `date` only so the grading can be attributed to a day in the cost
    log -- it plays no part in the grading itself.
    """
    raw = _json_generation(
        settings,
        kind="word-grade",
        date=date,
        system="You grade vocabulary practice. Respond ONLY JSON: "
        '{"distinct":bool,"per_sentence":[{"ok":bool,"note":"short"} x5],'
        '"verdict":"one line"}',
        user=f'Word: "{word}". Are these five sentences each correct usages AND semantically '
        f"distinct from each other (different senses/contexts, not rewordings)?\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences)),
    )
    try:
        return d.WordGrade(**raw)
    except TypeError as e:
        raise ContentError("the grader did not return the expected shape") from e


# --- theorem cards ---------------------------------------------------------


_THEOREM_SYSTEM = 'Respond ONLY JSON: {"name":"...","statement":"LaTeX ok","topic":"..."}'


def _theorem(settings: Settings, date: dt.date, topic: str, user: str) -> d.TheoremCard:
    raw = _json_generation(
        settings, kind="theorem", date=date, topic=topic, system=_THEOREM_SYSTEM, user=user
    )
    try:
        return d.TheoremCard(
            name=raw["name"],
            statement=raw["statement"],
            topic=raw.get("topic") or topic,
            added=date,
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the theorem generation was missing {e}") from e


def theorem_card(settings: Settings, date: dt.date) -> d.TheoremCard:
    """A theorem the model picks, from the week's rotating topic."""
    topic = settings.math.topic_for(date)
    return _theorem(
        settings,
        date,
        topic,
        f"Give one important theorem or definition from the topic '{topic}' "
        f"suitable for a recall card.",
    )


def theorem_from_prompt(settings: Settings, date: dt.date, prompt: str) -> d.TheoremCard:
    """A theorem *you* pick, named in as few words as you like.

    The whole flow this exists for is: finish a problem, read the cleaner
    proof, notice the trick that made it clean, and type its name. So the
    prompt is a fragment -- "Vandermonde's", "triple roots of unity filter" --
    and the model's job is to resolve the fragment and write the statement.

    `topic` is asked for rather than taken from the weekly rotation: a
    theorem you met today is filed under what it *is*, not under whatever the
    calendar happened to be showing when you added it.
    """
    return _theorem(
        settings,
        date,
        settings.math.topic_for(date),
        f"The user wants a recall card for: {prompt!r}. Identify the theorem, "
        f"technique or definition they mean, give its standard name, state it "
        f"precisely, and name the mathematical topic it belongs to. If the "
        f"phrase is ambiguous, take the reading most common in competition "
        f"mathematics.",
    )


# --- topic reference links -------------------------------------------------


def _link(raw: dict[str, Any]) -> d.Link:
    """`Link` forbids extra keys, and the model reaches for `name` about as
    often as `title`. Normalise rather than fail on a synonym."""
    return d.Link(title=raw.get("title") or raw["name"], url=raw["url"])


def topic_info(settings: Settings, repo: Repository, date: dt.date) -> TopicResponse:
    """Theorem list + textbook for the week's topic.

    Hand-curated from `[topic_library]` when possible, generated once and
    cached in SQLite otherwise. With a rotation in play the generated path is
    the common one -- but it is paid for once per topic ever, not per week,
    because `topic_info` is keyed by the topic name rather than the date.
    """
    topic = settings.math.topic_for(date)

    entry = settings.topic_library.get(topic)
    if entry:
        return TopicResponse(topic=topic, textbook=entry.textbook, theorems=entry.theorems)

    cached = repo.topic(topic)
    if cached:
        return TopicResponse(**cached.model_dump())

    raw = _json_generation(
        settings,
        kind="topic",
        date=date,
        topic=topic,
        system='Respond ONLY JSON: {"textbook":{"title":"...","url":"link to a FREE online '
        'textbook on the topic"},"theorems":[{"title":"...","url":"..."} x6, the most '
        "important theorems/techniques for the topic]}",
        user=f"Topic: {topic}. Give the canonical free textbook and six key theorems with "
        f"reference links.",
    )
    try:
        info = d.TopicInfo(
            topic=topic,
            textbook=_link(raw["textbook"]),
            theorems=[_link(t) for t in raw["theorems"]],
        )
    except (KeyError, TypeError) as e:
        raise ContentError("the topic generation did not return links") from e
    repo.upsert_topic(info)
    return TopicResponse(**info.model_dump())
