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
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import llm, logs, tricks
from .config import Settings
from .db.repository import Repository
from .llm import LlmError
from .models import domain as d
from .models.api import TopicResponse
from .models.common import Deck

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


SHOWCASE_WEEKDAY = 5
"""Saturday, in `date.weekday()` terms (0 = Monday).

The last day of the topic week -- the rotation turns over on Sunday -- so it
falls where a capstone belongs rather than interrupting the ramp.
"""


def is_showcase(date: dt.date) -> bool:
    """Is this a day for a famous problem rather than a calibrated one?"""
    return date.weekday() == SHOWCASE_WEEKDAY


def _problem_prompt(settings: Settings, rating: int, date: dt.date, seen: list[str]) -> str:
    topic = settings.math.topic_for(date)
    do_not_use = (
        f"Must be from a real past competition. Do NOT use any of these "
        f"already-seen problems: {json.dumps(seen[-SEEN_WINDOW:])}"
    )

    if is_showcase(date):
        # Saturday asks a different question. Every other day the target is a
        # 30-70% solve chance, which is the right target for *learning* and the
        # wrong one for the thing this is for: the problems worth meeting are
        # famous because of the idea in them, and how hard they happen to be is
        # not something they can be selected on without losing most of them.
        #
        # The rating is deliberately absent from this prompt. Naming it would
        # invite the model to reach for it, and "beautiful, at about 1500" is a
        # much smaller set than "beautiful" -- usually an empty one.
        #
        # The tier is still reported honestly, so the result still rates: a
        # famous olympiad problem anchored at 1900 is a cheap loss and a large
        # win against a 1500 rating, which is the correct shape for a stretch.
        return (
            f"Topic of the week: {topic}. Today is Saturday, which is the showcase "
            f"day: give me a CLASSIC, BEAUTIFUL problem on this topic — one of the "
            f"famous ones, the kind with a named trick or a one-line solution that "
            f"is remembered decades later. Ignore difficulty entirely and ignore how "
            f"hard the rest of the week has been; pick on elegance and fame alone. "
            f"Olympiad, Putnam and IMO shortlist problems are all fair game. Report "
            f"the tier that honestly matches its difficulty — do not soften it to "
            f"make the problem look approachable. {do_not_use}"
        )

    return (
        f"Topic of the week: {topic}. Today is weekday "
        f"{date.weekday()} (0=Mon: ramp difficulty through the week, but vary sub-skills "
        f"to teach the topic broadly; Saturday is a separate showcase day and is not "
        f"part of this ramp). My rating: {rating}. Tier anchors: "
        f"{json.dumps(settings.math.anchors)}. Pick a tier whose anchor gives me a "
        f"30-70% solve chance, adjusted for weekday. {do_not_use}"
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


def to_problem(
    raw: dict[str, Any], settings: Settings, model: str, date: dt.date | None = None
) -> d.MathProblem:
    """Validate one generated or hand-written problem into the domain model.

    `MathProblem` forbids extra keys, so the mapping is explicit: this is the
    boundary where a malformed generation becomes a loud error instead of a
    half-populated dict that fails later in the browser.

    `date` resolves the rotation topic and its rating area, both frozen onto
    the row here. It is optional because the batch CLI validates problems
    without serving them; those get their topic from the batch file instead.
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

    topic = raw.get("topic") or (settings.math.topic_for(date) if date else None)
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
        topic=topic,
        area=settings.math.areas.get(topic) if topic else None,
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
    rating = target_rating(settings, repo, date)

    batched = _from_batch(settings, date, seen, rating)
    if batched is not None:
        log.info("serving %s from the overnight batch (no LLM call)", batched.source)
        return _store(repo, batched, date)

    for raw in load_pool(settings.path("problems")):
        if raw.get("source") and raw["source"] not in seen:
            log.info("serving %s from the hand-curated pool (no LLM call)", raw["source"])
            problem = to_problem(raw, settings, HAND_CURATED, date)
            return _store(repo, problem, date)

    return _store(repo, _generate_problem(settings, date, sorted(seen), rating, on_status), date)


def target_rating(settings: Settings, repo: Repository, date: dt.date) -> int:
    """The rating today's problem is pitched against.

    The *area* rating for this week's topic, not the pooled one. This is the
    whole point of rating by area: someone at 1700 overall who is 1450 at
    geometry should get a geometry problem aimed at 1450, because a problem
    pitched at 1700 teaches them nothing except that they are bad at geometry,
    which they already knew.

    Falls back to pooled when the rotation is off or the topic has no area --
    and an area with no history *is* the pooled rating (see
    `Repository.current_rating`), so week one of a new area pitches exactly
    where the old behaviour did and only diverges once there is evidence.
    """
    area = settings.math.area_for(date)
    rating = repo.current_rating("math", area)
    if area is not None:
        log.info("pitching today's problem at the %s rating (%d)", area, rating)
    return rating


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

    On a showcase day the rule inverts to "hardest rung". The overnight task is
    told to make Saturday's ladder classics rather than a difficulty spread, so
    picking the rung nearest the rating would take the tamest of them -- which
    is precisely the selection the day exists to stop making.
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

    def hardest(p: Any) -> int:
        return settings.math.anchors.get(p.tier, 0)

    pick = max(usable, key=hardest) if is_showcase(date) else min(usable, key=distance)

    try:
        return to_problem(pick.model_dump(), settings, BATCHED, date)
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
    date: dt.date,
    seen: list[str],
    rating: int,
    on_status: llm.StatusFn,
) -> d.MathProblem:
    """`rating` is passed in rather than read here: `next_problem` resolves it
    once so the batch, the pool and this all pitch at the same number."""
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
        problem = to_problem(llm.extract_json(text), settings, settings.llm.model, date)
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
    # Resolved once and used for both the call and the log line: pricing is
    # per model, so a record naming a different one from the request would
    # cost out the wrong rate.
    model = settings.llm.model_for(kind)
    try:
        raw = llm.json_call(settings.llm, system, user, model=model, metrics=metrics)
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
                model=model,
                started=started,
                settings=settings.llm,
                metrics=metrics,
                ok=ok,
                error=error,
                **extra,
            ),
        )


# --- word of the day -------------------------------------------------------


WORD_MEMORY_DAYS = 365
"""How far back a word counts as already had.

The whole list goes into the prompt, uncapped, unlike `SEEN_WINDOW` for math
sources. A year of words is a few hundred short strings where a year of problem
sources is a few hundred long ones, and truncating this one would quietly mean
"no repeats, except the ones from eleven months ago" -- which is exactly the
repeat you would not remember making and would be most annoyed to meet.
"""

WORD_ATTEMPTS = 2
"""Generations to spend before giving up on a non-repeat.

The model is handed the list and still occasionally picks off it. One resample
is cheap and usually lands -- nothing here pins the temperature, so a second
call is a genuinely different draw. Beyond that this stops rather than looping,
on the same principle as `_store`: spending your money in a retry loop is not a
decision this module gets to make on its own.
"""


def word_of_day(settings: Settings, repo: Repository, date: dt.date) -> d.WordOfDay:
    """Today's word, chosen against the last year of them.

    Takes the repository for the same reason `next_problem` does: the
    do-not-repeat list is not an optional refinement a caller might forget to
    pass, it is part of what picking a word *means*.
    """
    recent = repo.recent_words(date, WORD_MEMORY_DAYS)
    had = {word.casefold() for word in recent}

    for attempt in range(WORD_ATTEMPTS):
        word = _one_word(settings, date, recent)
        if word.word.casefold() not in had:
            return word
        log.warning(
            "the model offered %r, which was the word of the day in the last %d days "
            "(attempt %d of %d)",
            word.word,
            WORD_MEMORY_DAYS,
            attempt + 1,
            WORD_ATTEMPTS,
        )

    raise ContentError(
        f"the model kept returning words you have already had ({word.word}) — press again to retry"
    )


def _one_word(settings: Settings, date: dt.date, recent: list[str]) -> d.WordOfDay:
    """One generation. The repeat check is the caller's."""
    # The old prompt asked for the choice to be derived from the date --
    # "deterministic-ish" -- which bought nothing, since the word is fixed by
    # being stored on the day it is drawn, and worked directly against this: a
    # date-derived pick has no reason to avoid what the same rule picked last
    # year, and rerolling it would land on the same word again.
    do_not_use = (
        f" You have already had these in the last {WORD_MEMORY_DAYS} days, so do NOT "
        f"pick any of them: {json.dumps(recent)}"
        if recent
        else ""
    )
    raw = _json_generation(
        settings,
        kind="word",
        date=date,
        system="Respond ONLY JSON: "
        '{"word":"...","pos":"...","definition":"...",'
        '"pronunciation":"IPA between slashes, e.g. /səˈsʌrəs/",'
        '"respelling":"the same sound without IPA, hyphenated, stressed '
        'syllable in CAPITALS, e.g. suh-SUR-us"}',
        user=f"Pick an uncommon-but-usable English word of the day for {date.isoformat()}."
        + do_not_use,
    )
    try:
        return d.WordOfDay(
            date=date,
            word=raw["word"],
            pos=raw["pos"],
            definition=raw["definition"],
            # Optional, unlike the three above. A word with no pronunciation is
            # a slightly poorer card; a word with no definition is not a card
            # at all, and only the second is worth failing the day over.
            pronunciation=raw.get("pronunciation") or None,
            respelling=raw.get("respelling") or None,
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
    n = len(sentences)
    raw = _json_generation(
        settings,
        kind="word-grade",
        date=date,
        system="You grade vocabulary practice. Respond ONLY JSON: "
        f'{{"distinct":bool,"per_sentence":[{{"ok":bool,"note":"short"}} x{n}],'
        '"verdict":"one line"}',
        # Counted from the list rather than written into the string. The
        # sentence count is a number that has already changed once, and the
        # grader returning five verdicts for two sentences is the kind of
        # mismatch that validates fine and reads as nonsense.
        user=f'Word: "{word}". Are these {n} sentences each correct usages AND semantically '
        f"distinct from each other (different senses/contexts, not rewordings)?\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences)),
    )
    try:
        return d.WordGrade(**raw)
    except TypeError as e:
        raise ContentError("the grader did not return the expected shape") from e


def grade_theorem(
    settings: Settings,
    date: dt.date,
    card: d.TheoremCard,
    explanation: str,
    application: str,
) -> d.TheoremGrade:
    """Grade a written recall against the statement already on file.

    The grader is told what the theorem actually says, so this is a comparison
    rather than a recollection of its own -- which is why it can run on a small
    model.

    The bar is deliberately stated as "in your own words". Recall of a theorem
    is knowing what it claims, not reproducing its typography, and a grader
    that wanted LaTeX would be marking the wrong thing: "the dot product's
    magnitude is at most the product of the magnitudes" *is* Cauchy-Schwarz.
    The failure it should catch is a statement that is wrong or so vague it
    would not identify the theorem -- missing hypotheses, a flipped
    inequality, "it's about inner products".

    The application half is graded on whether the problem is one the theorem
    genuinely bears on, named or not. "The polynomial that agreed with a
    rational function on [0,1]" earns it; "an olympiad problem" does not.
    """
    areas = settings.math.area_names
    raw = _json_generation(
        settings,
        kind="theorem-grade",
        date=date,
        system=(
            "You grade a recalled theorem against its real statement. Be generous about "
            "wording, phrasing and notation -- plain English is expected and LaTeX is not "
            "wanted -- and strict about content: wrong direction, dropped hypotheses, or "
            "so vague it could be any theorem all fail. Respond ONLY with JSON: "
            '{"explained":bool,"applied":bool,"area":one of the listed areas,'
            '"note":"one line, name what was missing or confirm what was right"}'
        ),
        user=(
            f"Theorem: {card.name}\n"
            f"Its actual statement: {card.statement}\n\n"
            f"They explained it as:\n{explanation}\n\n"
            f"They said they needed it for:\n{application}\n\n"
            "`explained`: does their explanation state what this theorem actually claims?\n"
            "`applied`: is that a problem this theorem genuinely applies to? They do not "
            "have to name it -- a recognisable description of the situation counts. A "
            "generic answer with no specifics does not.\n"
            f"`area`: which of these the theorem belongs to -- {', '.join(areas)}. "
            "Pick the single best fit."
        ),
    )
    try:
        return d.TheoremGrade(**raw)
    except TypeError as e:
        raise ContentError("the theorem grader did not return the expected shape") from e


# --- theorem cards ---------------------------------------------------------


_THEOREM_SYSTEM = (
    "Respond ONLY with JSON:\n"
    '{"name":"the standard name of the theorem, plain text, no LaTeX",'
    '"statement":"the statement, with EVERY mathematical expression wrapped in '
    "LaTeX delimiters -- $..$ inline, $$..$$ display. Never emit a bare formula: "
    'a statement that is only maths still needs its $$..$$ around it.",'
    '"topic":"the mathematical field, one to three words"}'
)
"""Why the delimiters are spelled out rather than left as "LaTeX ok".

The renderer converts what is *between* `$..$` or `$$..$$` and leaves
everything else as text, so an undelimited formula reaches the card as
literal backslashes -- which is exactly what happened to Vandermonde's
Identity: `\\sum_{k=0}^{r} \\binom{m}{k}...` stored with no `$` anywhere.

The math problem prompt has always named the delimiters explicitly and has
never produced this, which is the evidence that the wording is the variable.
`topic` is bounded for the same reason: unprompted, one card came back filed
under "Combinatorics / Generating Functions (Complex Analysis applications in
competition mathematics)", which is a sentence, not a topic.
"""

# Anything that looks like a LaTeX control sequence: a backslash followed by
# letters, or a lone subscript/superscript brace group.
_LATEX_HINT = re.compile(r"\\[a-zA-Z]+|[_^]\{")


def _ensure_delimited(statement: str) -> str:
    """Wrap a bare formula the model forgot to delimit.

    The prompt above is the fix; this is the guarantee. A prompt shifts the
    odds, and the failure mode it guards against is silent -- the card stores
    fine, passes validation, and only looks wrong weeks later when it comes up
    in a draw. One `$` anywhere means the model made a decision about
    delimiting and it is not this function's business to second-guess it.
    """
    if "$" in statement or not _LATEX_HINT.search(statement):
        return statement
    log.warning("theorem statement arrived undelimited; wrapping: %.80s", statement)
    return f"$${statement.strip()}$$"


def _theorem(settings: Settings, date: dt.date, topic: str, user: str) -> d.TheoremCard:
    raw = _json_generation(
        settings, kind="theorem", date=date, topic=topic, system=_THEOREM_SYSTEM, user=user
    )
    try:
        return d.TheoremCard(
            name=raw["name"],
            statement=_ensure_delimited(raw["statement"]),
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


# --- the trick of the day --------------------------------------------------
#
# One generation a day produces the whole cell: the trick, and every problem
# that teaches it. That is the cost decision, and it is the reason the answers
# are checked by string comparison rather than by a grader -- eight gradings a
# day would cost more than the generation that produced the problems.
#
# The overnight batch is the other half of the same decision. Unlike the math
# problem there is no curated pool to fall back on, so `batches/trick-<date>.json`
# is the only free source there is; the paid path below exists for the mornings
# the 3am task did not run.

TRICK_MEMORY = 300
"""How many past trick names go into the do-not-repeat list.

Bounded, unlike `word_of_day`'s year of words, because these accumulate at one
a day forever and the list is charged by the token. Three hundred is roughly
ten months, which is longer than the rotation's ~six-month cycle -- so a topic
coming round for the second time still knows what it taught the first.
"""

TRICK_RULES = (
    "You design a single day of deliberate practice on ONE mathematical trick: an "
    "identity, a shortcut, or a reusable technique. The user learns it by working the "
    "problems, not by reading the card, so the problems are the deliverable and the "
    "statement is the reference.\n\n"
    "Rules that decide whether this is usable:\n"
    "- The trick must be a *move*, not a fact. 'Multiply by the conjugate', "
    "'differentiate under the integral sign', 'roots of unity filter', 'complete the "
    "square in the exponent' — something that shortens work. Never a definition.\n"
    "- Every problem must be solvable BY HAND in under five minutes using the trick, "
    "and must have ONE unambiguous final answer.\n"
    "- `answer` is a bare value, never a sentence and never a restatement of the "
    "question. `accept` lists the other spellings a correct answer would plausibly be "
    "typed as — decimal and fraction, LaTeX and plain, factored and expanded. This is "
    "checked by string comparison, so an alternative you omit marks a right answer "
    "wrong.\n"
    "- The `challenge` problem must need more than applying the trick: a case where it "
    "does not directly fit, has to be set up first, applied twice, or combined with "
    "something else. If it is one more application of the identity, it is a medium.\n"
    "- Wrap EVERY mathematical expression in LaTeX delimiters — $..$ inline, $$..$$ "
    "display — in prompts, answers, statements and solutions alike. Never emit a bare "
    "formula."
)
"""The rules, without the output schema.

Public and separated from `_TRICK_SYSTEM` below because the overnight task
needs exactly these instructions with a *different* schema attached -- the
batch file carries the date and topic back for validation, the live response
does not. Keeping one copy is what stops the free path and the paid path from
slowly becoming two features. See `batch.trick_context`.
"""

_TRICK_SYSTEM = (
    f"{TRICK_RULES}\n\n"
    "Respond ONLY with JSON:\n"
    '{"name":"the standard name of the trick, plain text, no LaTeX",'
    '"statement":"what it says, stated precisely, LaTeX delimited",'
    '"idea":"when to reach for it and what it buys you — two or three sentences, and '
    'name the situation that should trigger it",'
    '"kind":"trick or technique",'
    '"problems":[{"band":"easy|medium|callback|challenge","prompt":"the problem, LaTeX '
    'delimited","answer":"the bare final answer","accept":["other spellings of the same '
    'answer"],"solution":"one or two lines: the trick applied, and the answer"}]}'
)


def trick_instructions(
    topic: str,
    wanted: dict[str, int],
    seen: list[str],
    callback: d.Trick | None,
) -> str:
    """What today's set has to be, in words.

    Public and built from primitives so the overnight task can be handed the
    identical instructions -- `batch.trick_context` puts this in its payload,
    which is what keeps the free path and the paid path asking for the same
    thing rather than drifting into two features.
    """
    lines = [
        f"Topic of the week: {topic}. Give me today's trick from this topic, and its "
        f"practice set.",
        # The technique route, and the reason the coupling to the rotation is
        # survivable. Stated as a fallback rather than an alternative so the
        # model reaches for it only when it has to.
        f"If '{topic}' genuinely has no computational trick in it — some topics are "
        f"structural rather than computational — then give a TECHNIQUE instead: the "
        f"standard opening move on problems of this kind, the canonical construction, "
        f"the thing an experienced solver does first. Set `kind` to 'technique' and say "
        f"so plainly. Do NOT invent a shortcut to fill the slot; a made-up identity is "
        f"the one outcome worse than a technique.",
        f"Exactly {wanted['easy']} problems with band 'easy' (one-line applications, "
        f"the trick handed to you) and {wanted['medium']} with band 'medium' (the trick "
        f"has to be recognised first, or applied to something that does not look like "
        f"the identity).",
        "Exactly 1 problem with band 'challenge'.",
    ]
    if wanted.get("callback"):
        assert callback is not None  # `wanted_bands` only asks for one when there is one
        lines.append(
            f"Exactly 1 problem with band 'callback': a medium-difficulty problem on a "
            f"DIFFERENT, older trick — {callback.name}, which says: {callback.statement} "
            f"It is a spaced-repetition problem on that trick and has nothing to do with "
            f"today's; do not try to combine the two."
        )
    if seen:
        lines.append(
            f"Do NOT give any trick you have already taught: {json.dumps(seen[-TRICK_MEMORY:])}"
        )
    return "\n".join(lines)


def _to_trick(
    raw: dict[str, Any], settings: Settings, date: dt.date
) -> tuple[d.Trick, list[d.TrickProblem]]:
    """Validate one generated set into the two models it becomes.

    The same boundary `to_problem` is, and for the same reason: `extra="forbid"`
    turns a drifted prompt into a loud error here rather than a half-drawn panel
    in the browser.
    """
    try:
        problems = [d.TrickProblem(**p) for p in raw["problems"]]
    except (KeyError, TypeError) as e:
        raise ContentError(f"the trick generation was missing {e}") from e

    if not tricks.marked_problems(problems):
        # Every problem is the challenge, or there are none. A set with nothing
        # to mark cannot be finished, so it cannot be served.
        raise ContentError("the trick generation produced no gradeable problems")

    try:
        trick = d.Trick(
            name=raw["name"],
            statement=_ensure_delimited(raw["statement"]),
            idea=raw.get("idea") or "",
            topic=raw.get("topic") or settings.math.topic_for(date),
            kind=raw.get("kind") if raw.get("kind") in ("trick", "technique") else "trick",
            added=date,
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the trick generation was missing {e}") from e
    return trick, problems


def generate_trick(
    settings: Settings,
    date: dt.date,
    wanted: dict[str, int],
    seen: list[str],
    callback: d.Trick | None,
) -> tuple[d.Trick, list[d.TrickProblem]]:
    """One paid generation of the day's trick and its problems.

    No web search, unlike the math problem: nothing here is a claim about a
    real past competition that could be got factually wrong, so there is
    nothing to verify and no reason to pay for four searches. What can go
    wrong is a stated identity that is false, and a search would not catch
    that either -- the problems are the check, since a wrong identity produces
    answers that do not survive being worked by hand.
    """
    raw = _json_generation(
        settings,
        kind="trick",
        date=date,
        topic=settings.math.topic_for(date),
        system=_TRICK_SYSTEM,
        user=trick_instructions(settings.math.topic_for(date), wanted, seen, callback),
    )
    if not isinstance(raw, dict):
        raise ContentError("the trick generation did not return an object")
    trick, problems = _to_trick(raw, settings, date)

    # Logged rather than rejected. A set with three easy problems instead of
    # four is a slightly short morning; refusing it would mean paying for a
    # second generation over a missing rung, and `_store`'s rule applies --
    # spending your money on a retry is not a decision this module makes on
    # its own.
    for complaint in tricks.check_bands(problems, wanted):
        log.warning("today's trick set is misshapen: %s", complaint)
    return trick, problems


def _trick_from_batch(
    settings: Settings, date: dt.date
) -> tuple[d.Trick, list[d.TrickProblem], str | None] | None:
    """Tonight's generated set, or None.

    None rather than an exception for every failure mode -- no file, bad JSON,
    a set that does not validate -- on `_from_batch`'s reasoning: a batch is an
    optimisation, and an optimisation that can take the panel down is not one.
    The caller falls through to a paid generation.
    """
    path = trick_batch_path(settings, date)
    if not path.exists():
        return None

    # Imported here rather than at module scope, exactly as `_from_batch` does:
    # `batch` imports this module, and the models package is the shared bottom.
    from .models.batch import TrickSet

    try:
        batch = TrickSet(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        log.exception("unusable trick batch at %s; falling through to a generation", path)
        return None

    try:
        trick, problems = _to_trick(
            {
                "name": batch.name,
                "statement": batch.statement,
                "idea": batch.idea,
                "kind": batch.kind,
                "topic": batch.topic,
                "problems": [p.model_dump() for p in batch.problems],
            },
            settings,
            date,
        )
    except ContentError:
        log.exception("trick batch at %s did not validate; falling through", path)
        return None
    return trick, problems, batch.callback


def trick_batch_path(settings: Settings, date: dt.date) -> Path:
    """Where the overnight task drops the day's set.

    Prefixed rather than sharing `<date>.json` with the math ladder: they are
    two files from the same night for the same day, and one of them is not the
    other's shape.
    """
    return settings.path("batches") / f"trick-{date.isoformat()}.json"


def next_trick(settings: Settings, repo: Repository, date: dt.date) -> d.TrickRound:
    """Today's trick round, from the cheapest source that has one.

    Idempotent: a day that already has a round gets it back untouched. That is
    load-bearing here in a way it is not for the theorem draw -- rerolling this
    one spends money.

    The callback is chosen *before* the generation, because the generator has to
    be told which old trick to write a problem on. On the batch path it was
    chosen at 3am and is resolved back by name, so the set and the card it
    reaches back to cannot disagree.
    """
    existing = repo.trick_round(date)
    if existing is not None:
        return existing

    callback = tricks.pick_callback(
        repo.tricks(),
        repo.callback_dates(),
        date,
        cooldown=settings.tricks.callback_cooldown_days,
        exponent=settings.tricks.callback_exponent,
    )

    batched = _trick_from_batch(settings, date)
    if batched is not None:
        trick, problems, name = batched
        log.info("serving the trick %r from the overnight batch (no LLM call)", trick.name)
        # The batch's own callback wins: its problem was written against that
        # trick, and re-picking here would label it with a different one.
        resolved = repo.trick_named(name) if name else None
        if name and resolved is None:
            log.warning(
                "the batch's callback trick %r is not in the deck; leaving it unlabelled", name
            )
        return _store_trick(repo, date, trick, problems, resolved)

    wanted = tricks.wanted_bands(
        settings.tricks.easy, settings.tricks.medium, callback=callback is not None
    )
    trick, problems = generate_trick(
        settings, date, wanted, repo.trick_names(), callback
    )
    return _store_trick(repo, date, trick, problems, callback)


def _store_trick(
    repo: Repository,
    date: dt.date,
    trick: d.Trick,
    problems: list[d.TrickProblem],
    callback: d.Trick | None,
) -> d.TrickRound:
    try:
        stored = repo.add_trick(trick)
    except sqlite3.IntegrityError as e:
        # `name` is UNIQUE: the model ignored the do-not-repeat list. Not
        # retried automatically, on `_store`'s rule -- a retry is another paid
        # generation and the click that asks for one should be the user's.
        raise ContentError(
            f"the model returned a trick you have already had ({trick.name}) — press again to retry"
        ) from e
    assert stored.id is not None
    return repo.open_trick_round(
        date, stored.id, problems, callback.id if callback else None
    )


def grade_challenge(
    settings: Settings,
    date: dt.date,
    trick: d.Trick,
    problem: d.TrickProblem,
    answer: str,
) -> d.TrickGrade:
    """Grade the optional problem, which is the one that cannot be compared.

    The other eight are marked by string comparison against a canonical answer.
    This one is deliberately a problem where applying the trick is not enough,
    so the interesting failures are in the reasoning rather than in the final
    value -- and a bare comparison would mark a right answer reached by luck
    the same as a right answer reached by the argument the day was about.

    The grader is told the reference answer and the worked solution, so this is
    a comparison rather than a solve of its own, which is what lets it run on a
    small model.
    """
    raw = _json_generation(
        settings,
        kind="trick-grade",
        date=date,
        topic=trick.topic,
        system=(
            "You grade one answer to a mathematics problem against a reference answer "
            "and its worked solution. Be generous about form -- an equivalent expression, "
            "a decimal instead of a fraction, plain English instead of LaTeX are all "
            "fine -- and strict about the mathematics: a different value, a dropped case, "
            "or an answer with no argument behind it fails. Respond ONLY with JSON: "
            '{"correct":bool,"note":"one line — name what was missing, or confirm what '
            'was right and say what the intended route was"}'
        ),
        user=(
            f"The trick of the day: {trick.name} — {trick.statement}\n\n"
            f"Problem: {problem.prompt}\n"
            f"Reference answer: {problem.answer}\n"
            f"Worked solution: {problem.solution or '(none given)'}\n\n"
            f"They answered:\n{answer}\n\n"
            "`correct`: does their answer agree with the reference? If they showed "
            "working, does it hold up? A right value reached by a wrong argument is "
            "not correct; a right value with no working shown is."
        ),
    )
    try:
        return d.TrickGrade(correct=bool(raw["correct"]), note=str(raw.get("note") or ""))
    except (KeyError, TypeError) as e:
        raise ContentError("the challenge grader did not return the expected shape") from e


# --- argument patterns -----------------------------------------------------
#
# The second deck. Same table, same draw, same two boxes, same +/-3 -- only the
# prompts differ, because what is being recalled is a pattern of reasoning
# rather than a theorem, and the failure modes are not the same ones.

_PATTERN_SYSTEM = (
    "You write recall cards for argument patterns: the reasoning structures and "
    "failure modes that recur across policy debates -- principal-agent problems, "
    "tragedy of the commons, base-rate neglect, motte and bailey, regulatory capture. "
    "Respond ONLY with JSON:\n"
    '{"name":"the standard name, plain text, title case, no quotation marks",'
    '"statement":"what the pattern claims and when it applies, in two or three '
    "sentences of plain English. Say what recognising it buys you in an argument, and "
    'name the way it is most often misused. No LaTeX, no markdown.",'
    '"topic":"the family it belongs to -- one or two words, e.g. incentives, '
    'statistics, rhetoric, ethics, economics, institutions, reasoning"}'
)
"""Why this is not `_THEOREM_SYSTEM` with a word changed.

Three differences, each load-bearing. No LaTeX, because a pattern has no
notation and the theorem prompt's insistence on delimiters would produce
`$$principal-agent$$`. A *usable* statement rather than a precise one -- the
point of the card is recognising the pattern in the wild under time pressure,
not reciting its definition. And the misuse clause, which has no theorem
analogue: every one of these is a tool that is over-applied at least as often
as it is applied, and a card that does not say so teaches you to reach for it.
"""


def pattern_from_prompt(settings: Settings, date: dt.date, prompt: str) -> d.TheoremCard:
    """A pattern card from a name or a description.

    The flow this exists for: finish a debate, read the ballot, notice that the
    opponent used something you did not have a name for, and type the name --
    or, when the ballot named it for you, press the button beside it.
    """
    raw = _json_generation(
        settings,
        kind="pattern",
        date=date,
        topic=prompt[:60],
        system=_PATTERN_SYSTEM,
        user=(
            f"The user wants a recall card for: {prompt!r}. Identify the argument "
            f"pattern, fallacy or reasoning structure they mean, give its standard "
            f"name, and state it. If the phrase is ambiguous, take the reading most "
            f"useful in a policy debate."
        ),
    )
    try:
        return d.TheoremCard(
            name=raw["name"],
            statement=raw["statement"],
            topic=raw.get("topic") or "reasoning",
            added=date,
            deck=Deck.argument,
        )
    except (KeyError, TypeError) as e:
        raise ContentError(f"the pattern generation was missing {e}") from e


def grade_pattern(
    settings: Settings,
    date: dt.date,
    card: d.TheoremCard,
    explanation: str,
    application: str,
) -> d.TheoremGrade:
    """Grade a recalled argument pattern against the card on file.

    The same two halves as `grade_theorem` and the same generosity about
    wording, but the second half asks a different question. A theorem's
    application is a problem you needed it on; a pattern's is an argument where
    it *decided* something -- one you made, one made at you, or one you read.

    The bar on the second half is deliberately low on specificity and high on
    concreteness: "someone argued the agency would just enforce it, and the
    agency's incentives are not the public's" earns it without naming a debate.
    "In politics" does not.
    """
    raw = _json_generation(
        settings,
        kind="pattern-grade",
        date=date,
        system=(
            "You grade a recalled argument pattern against its real statement. Be "
            "generous about wording -- plain English is the point -- and strict about "
            "content: a statement that would not let someone recognise the pattern in "
            "an argument, or that describes a different pattern, fails. Respond ONLY "
            'with JSON: {"explained":bool,"applied":bool,'
            '"note":"one line, name what was missing or confirm what was right"}'
        ),
        user=(
            f"Pattern: {card.name}\n"
            f"Its actual statement: {card.statement}\n\n"
            f"They explained it as:\n{explanation}\n\n"
            f"They said it showed up in:\n{application}\n\n"
            "`explained`: would their explanation let someone spot this pattern in an "
            "argument, and is it this pattern rather than a neighbouring one?\n"
            "`applied`: is that a real argument where this pattern actually decided "
            "something -- one they made, one made at them, or one they read? They do "
            "not have to name the debate; a concrete description of the move counts. "
            "A generic gesture at a subject area does not."
        ),
    )
    try:
        # `area` is absent from the prompt on purpose: an argument recall rates
        # into one fixed bucket, so there is nothing for the grader to choose
        # and asking would invite it to invent one.
        return d.TheoremGrade(**raw)
    except TypeError as e:
        raise ContentError("the pattern grader did not return the expected shape") from e


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
