"""LLM-backed daily content: the math problem, the word, the theorem card,
and the topic's reference links.

This is where the prompts live. It is deliberately the only module that both
talks to `llm` and writes through `Repository`, so that "what a generation
costs" and "what it is allowed to persist" are answerable by reading one file.

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
        f"Topic of the week: {settings.math.topic_of_week}. Today is weekday "
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


def _to_problem(raw: dict[str, Any], settings: Settings, model: str) -> d.MathProblem:
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
    """Today's problem: from the pool if one is unseen, else generated.

    Serving the pool first is the cost-conscious path -- a hand-written
    problem is free, a verified generation is roughly $0.02-0.04.
    """
    seen = set(repo.seen_sources())
    for raw in load_pool(settings.path("problems")):
        if raw.get("source") and raw["source"] not in seen:
            log.info("serving %s from the hand-curated pool (no LLM call)", raw["source"])
            problem = _to_problem(raw, settings, HAND_CURATED)
            return _store(repo, problem, date)

    return _store(repo, _generate_problem(settings, repo, date, sorted(seen), on_status), date)


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
    metrics: llm.Metrics | None = None
    problem: d.MathProblem | None = None
    error: str | None = None
    try:
        text, metrics = llm.call(
            settings.llm,
            PROBLEM_SYSTEM,
            _problem_prompt(settings, rating, date, seen),
            tools=llm.web_search_tool(settings.llm.web_search_max_uses),
            on_status=on_status,
        )
        problem = _to_problem(llm.extract_json(text), settings, settings.llm.model)
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
                topic=settings.math.topic_of_week,
                rating=rating,
                source=problem.source if problem else None,
                tier=problem.tier if problem else None,
            ),
        )
        if on_status and problem is not None:
            on_status("done ✓")


# --- word of the day -------------------------------------------------------


def word_of_day(settings: Settings, date: dt.date) -> d.WordOfDay:
    raw = llm.json_call(
        settings.llm,
        'Respond ONLY JSON: {"word":"...","pos":"...","definition":"..."}',
        f"Pick an uncommon-but-usable English word of the day for {date.isoformat()}. "
        f"Deterministic-ish: base the choice on that date.",
    )
    try:
        return d.WordOfDay(
            date=date, word=raw["word"], pos=raw["pos"], definition=raw["definition"]
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the word generation was missing {e}") from e


def grade_sentences(settings: Settings, word: str, sentences: list[str]) -> d.WordGrade:
    raw = llm.json_call(
        settings.llm,
        "You grade vocabulary practice. Respond ONLY JSON: "
        '{"distinct":bool,"per_sentence":[{"ok":bool,"note":"short"} x5],'
        '"verdict":"one line"}',
        f'Word: "{word}". Are these five sentences each correct usages AND semantically '
        f"distinct from each other (different senses/contexts, not rewordings)?\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences)),
    )
    try:
        return d.WordGrade(**raw)
    except TypeError as e:
        raise ContentError("the grader did not return the expected shape") from e


# --- theorem cards ---------------------------------------------------------


def theorem_card(settings: Settings, date: dt.date) -> d.TheoremCard:
    topic = settings.math.topic_of_week
    raw = llm.json_call(
        settings.llm,
        'Respond ONLY JSON: {"name":"...","statement":"LaTeX ok","topic":"..."}',
        f"Give one important theorem or definition from the topic '{topic}' "
        f"suitable for a spaced-repetition recall card.",
    )
    try:
        return d.TheoremCard(
            name=raw["name"],
            statement=raw["statement"],
            topic=raw.get("topic") or topic,
            due=date,
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the theorem generation was missing {e}") from e


# --- topic reference links -------------------------------------------------


def _link(raw: dict[str, Any]) -> d.Link:
    """`Link` forbids extra keys, and the model reaches for `name` about as
    often as `title`. Normalise rather than fail on a synonym."""
    return d.Link(title=raw.get("title") or raw["name"], url=raw["url"])


def topic_info(settings: Settings, repo: Repository) -> TopicResponse:
    """Theorem list + textbook for the current topic.

    Hand-curated from `[topic_library]` when possible, generated once and
    cached in SQLite otherwise. The hand-curated path is the common one and
    costs nothing.
    """
    topic = settings.math.topic_of_week

    entry = settings.topic_library.get(topic)
    if entry:
        return TopicResponse(topic=topic, textbook=entry.textbook, theorems=entry.theorems)

    cached = repo.topic(topic)
    if cached:
        return TopicResponse(**cached.model_dump())

    raw = llm.json_call(
        settings.llm,
        'Respond ONLY JSON: {"textbook":{"title":"...","url":"link to a FREE online '
        'textbook on the topic"},"theorems":[{"title":"...","url":"..."} x6, the most '
        "important theorems/techniques for the topic]}",
        f"Topic: {topic}. Give the canonical free textbook and six key theorems with "
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
