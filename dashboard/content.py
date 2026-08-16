"""LLM-backed daily content: the math problem, the word, the theorem card,
and the topic's reference links.

This is where the prompts live. It is deliberately the only module that both
talks to `llm` and writes through `Repository`, so that "what a generation
costs" and "what it is allowed to persist" are answerable by reading one file.

Every paid call goes through `_json_generation`, which appends a line to
metrics.jsonl. That is the invariant that makes the log worth reading: if a
call is not in the file, it did not happen.

The math problem is not among them. It used to have a paid path of its own --
an agentic run with web search, $0.15-0.80 a morning, reached by a button in
the panel. It is now served from the overnight batch or the curated pool or
not at all: there is no prompt in this file that produces a problem, and
therefore no way for opening the dashboard to bill for one.

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

SHOWCASE_WEEKDAY = 5
"""Saturday, in `date.weekday()` terms (0 = Monday).

The last day of the topic week -- the rotation turns over on Sunday -- so it
falls where a capstone belongs rather than interrupting the ramp.
"""


def is_showcase(date: dt.date) -> bool:
    """Is this a day for a famous problem rather than a calibrated one?"""
    return date.weekday() == SHOWCASE_WEEKDAY


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


def next_problem(settings: Settings, repo: Repository, date: dt.date) -> d.MathProblem:
    """Today's problem. Both sources are free, and there is no third one.

    1. The overnight batch, dropped by the scheduled task. Free -- it was
       generated against a subscription rather than the API.
    2. The hand-curated pool in problems.json. Free, and never written by the
       server.

    There used to be a third step: a paid generation, $0.15-0.80, run when the
    other two had nothing. It is gone. The morning after a night the task did
    not run is now a morning with no problem rather than a billed one, which
    is the trade this is for -- the failure is visible and costs nothing,
    where the old one was invisible and cost the most of any call in the app.

    So this raises where it used to spend. `ContentError` says what to do
    about it, because the answer is a real one: run the batch by hand.
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

    raise ContentError(
        f"no problem for {date.isoformat()}: the overnight batch has not landed and "
        f"every problem in the curated pool has been served. Run the batch for today "
        f"in the Claude app and import it — the dashboard no longer generates one."
    )


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
        # `source` is UNIQUE. From the batch this means the generator ignored
        # the do-not-repeat list it was handed; from the pool, that the file
        # lists a problem the log already has.
        raise ContentError(
            f"the problem for today has been served already ({problem.source})"
        ) from e
    assert stored.id is not None
    repo.open_math_attempt(date, stored.id)
    return stored


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

    Every paid call from this module now goes through here. There is
    deliberately no second path to `llm`.
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
    that *required* LaTeX would be marking the wrong thing: "the dot product's
    magnitude is at most the product of the magnitudes" *is* Cauchy-Schwarz.
    The failure it should catch is a statement that is wrong or so vague it
    would not identify the theorem -- missing hypotheses, a flipped
    inequality, "it's about inner products".

    Notation is welcome, though, and the prompt says so: the boxes render
    LaTeX now, and someone writing `$\\langle x,y\\rangle^2 \\le \\|x\\|^2
    \\|y\\|^2$` is being *more* precise, not less. The grader must read the
    source rather than penalise it -- "generous about notation" was ambiguous
    enough to mark a delimited answer down for having delimiters in it.

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
            "wording, phrasing and notation -- plain English and LaTeX are equally "
            "acceptable, and an answer written in LaTeX source ($..$ or $$..$$) is to be "
            "read as the mathematics it denotes, never marked down for its markup or for "
            "a typo in it -- and strict about content: wrong direction, dropped "
            "hypotheses, or so vague it could be any theorem all fail. Respond ONLY "
            "with JSON: "
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
# That generation happens overnight, on a subscription, and `batches/trick-<date>.json`
# is where it lands. It is the only source there is. There used to be two more --
# a prompt the panel handed you to carry into the Claude app and paste back, and
# a paid API generation behind a button -- and both are gone, on `next_problem`'s
# reasoning: the morning after a night the task did not run should be a morning
# with no set, visibly, rather than a morning that quietly costs something or
# asks you to go and do by hand what the night was for.

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

Public because the overnight task is what uses them: `batch.trick_context`
prints these, `trick_instructions` below and `TRICK_SCHEMA` as one payload, so
the night's generation is asking for exactly what this module will import.
Nothing in the app generates a set, so this is the only place they are needed
-- and keeping them here, next to the parser that has to accept the result, is
what stops the asking and the reading from drifting apart.
"""


def trick_instructions(
    topic: str,
    wanted: dict[str, int],
    seen: list[str],
    callback: d.Trick | None,
) -> str:
    """What today's set has to be, in words.

    Public and built from primitives so the overnight task can be handed the
    day's shape rather than having to know how to work it out -- which old
    trick to call back to, how many of each band, what has already been taught.
    `batch.trick_context` puts this in its payload.
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


def parse_trick_set(
    settings: Settings, raw: str, date: dt.date | None = None
) -> tuple[Any | None, list[str], list[str]]:
    """Read a written trick set, and say what is wrong with it.

    One parser for both ends of the same file -- `./batch.sh --validate-trick`
    checking it at 3am, and `_trick_from_batch` importing it at 8am -- because
    a second reading of one document is a second set of rules to keep in step.

    Returns `(set, errors, warnings)`, and the split is what lets the two
    callers disagree about strictness without disagreeing about the rules.
    **Errors** make the set unusable: it will not parse, it is for another week,
    it repeats a problem. **Warnings** are shape complaints -- one easy problem
    short, a missing worked line. The CLI treats both as fatal, because at 3am
    regenerating is free and the task is still there to do it; the serving path
    logs the warnings and serves anyway, because by morning the alternative to a
    slightly thin set is no set at all.

    Runs `extract_json`, LaTeX repair and all: a model asked for JSON full of
    LaTeX writes `\\frac` whether it is answering over HTTPS, writing a file, or
    being copied out of a chat window. See EXPLAINER §5.7.
    """
    # Imported here rather than at module scope, exactly as `_from_batch` does:
    # `batch` imports this module, and the models package is the shared bottom.
    from .models.batch import TrickSet

    try:
        parsed = llm.extract_json(raw)
    except json.JSONDecodeError as e:
        return None, [f"not JSON: {e}"], []
    if not isinstance(parsed, dict):
        return None, ["expected a JSON object"], []

    try:
        trick = TrickSet(**parsed)
    except Exception as e:
        return None, [f"does not match the trick schema: {e}"], []

    errors: list[str] = []
    warnings: list[str] = []

    expected = settings.math.topic_for(trick.date)
    if trick.topic.strip().casefold() != expected.casefold():
        errors.append(f"topic {trick.topic!r}: {trick.date} is a {expected!r} week")
    if date is not None and trick.date != date:
        errors.append(f"dated {trick.date}, but this is for {date}")

    has_callback = any(p.band == "callback" for p in trick.problems)
    if has_callback and not trick.callback:
        # Without the name the import cannot link the problem to the card it is
        # reinforcing, and the row renders as a problem that is inexplicably not
        # about today's trick.
        errors.append("a callback problem, but `callback` does not name the trick it is on")

    prompts = [p.prompt.strip() for p in trick.problems]
    if len(set(prompts)) != len(prompts):
        errors.append("the same problem appears twice")
    if not any(p.band != tricks.CHALLENGE for p in trick.problems):
        errors.append("nothing to mark — every problem is the challenge")

    warnings += tricks.check_bands(
        [d.TrickProblem(**p.model_dump()) for p in trick.problems],
        tricks.wanted_bands(settings.tricks.easy, settings.tricks.medium, callback=has_callback),
    )
    for i, p in enumerate(trick.problems):
        if not p.solution:
            # The challenge's worked line is what the grader reads; the others'
            # are what you read when you got it wrong. Both are worth having and
            # neither is worth refusing a set over.
            warnings.append(f"problems[{i}] ({p.band}): no worked solution")
        if p.answer.strip().casefold() == p.prompt.strip().casefold():
            warnings.append(f"problems[{i}]: the answer restates the problem")

    return trick, errors, warnings


def _from_trick_set(
    settings: Settings, trick: Any, date: dt.date
) -> tuple[d.Trick, list[d.TrickProblem]]:
    """One parsed `TrickSet` -> the two models it becomes."""
    return _to_trick(
        {
            "name": trick.name,
            "statement": trick.statement,
            "idea": trick.idea,
            "kind": trick.kind,
            "topic": trick.topic,
            "problems": [p.model_dump() for p in trick.problems],
        },
        settings,
        date,
    )


def _trick_from_batch(
    settings: Settings, date: dt.date
) -> tuple[d.Trick, list[d.TrickProblem], str | None] | None:
    """Tonight's generated set, or None.

    None rather than an exception for every failure mode -- no file, bad JSON,
    a set that does not validate -- so the caller decides what a missing set
    means. It used to mean "fall through to the paid path"; it now means one
    `ContentError` naming the fix, and the distinction between the four ways of
    having nothing is in the log rather than in the panel.
    """
    path = trick_batch_path(settings, date)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        log.exception("could not read the trick batch at %s; falling through", path)
        return None

    parsed, errors, warnings = parse_trick_set(settings, raw, date)
    if parsed is None or errors:
        log.warning("unusable trick batch at %s; falling through: %s", path, "; ".join(errors))
        return None
    for complaint in warnings:
        log.warning("the batch at %s is misshapen: %s", path, complaint)

    try:
        trick, problems = _from_trick_set(settings, parsed, date)
    except ContentError:
        log.exception("trick batch at %s did not validate; falling through", path)
        return None
    return trick, problems, parsed.callback


def trick_batch_path(settings: Settings, date: dt.date) -> Path:
    """Where the overnight task drops the day's set.

    Prefixed rather than sharing `<date>.json` with the math ladder: they are
    two files from the same night for the same day, and one of them is not the
    other's shape.
    """
    return settings.path("batches") / f"trick-{date.isoformat()}.json"


def today_callback(settings: Settings, repo: Repository, date: dt.date) -> d.Trick | None:
    """The trick today's set reaches back to. One answer, however often asked.

    Chosen at 3am, because the generator has to be *told* which old trick to
    write a callback problem on. `tricks.day_rng` is what makes a second run of
    `--trick-context` on the same night -- a retry, a task that was interrupted
    -- name the same trick as the first: the file echoes the name back, and the
    import resolves it, so the set and the card it reinforces cannot disagree.
    """
    return tricks.pick_callback(
        repo.tricks(),
        repo.callback_dates(),
        date,
        cooldown=settings.tricks.callback_cooldown_days,
        exponent=settings.tricks.callback_exponent,
    )


def next_trick(settings: Settings, repo: Repository, date: dt.date) -> d.TrickRound:
    """Today's trick round, from the overnight file and from nowhere else.

    Idempotent: a day that already has a round gets it back untouched. It is
    what makes this safe to call on every page load, which is now how it is
    reached -- the panel gathers itself at boot rather than waiting to be asked,
    since there is nothing left here that a reload could spend.

    The `ContentError` is the whole of the failure path, and it names the fix,
    because the answer is a real one: run the night's job by hand. Nothing is
    generated on demand -- see `next_problem`, which made this trade first.
    """
    existing = repo.trick_round(date)
    if existing is not None:
        return existing

    batched = _trick_from_batch(settings, date)
    if batched is None:
        raise ContentError(
            f"no trick set for {date.isoformat()}: the overnight batch has not landed. "
            f"Run `./batch.sh --trick-context` for today, write the file it describes to "
            f"batches/trick-{date.isoformat()}.json and check it with "
            f"`./batch.sh --validate-trick` — the dashboard no longer generates one."
        )

    trick, problems, name = batched
    log.info("serving the trick %r from the overnight batch (no LLM call)", trick.name)
    # The batch's own callback wins over a fresh pick: its problem was written
    # against that trick at 3am, and choosing again here would label it with a
    # different one.
    resolved = repo.trick_named(name) if name else None
    if name and resolved is None:
        log.warning(
            "the batch's callback trick %r is not in the deck; leaving it unlabelled", name
        )
    return _store_trick(repo, date, trick, problems, resolved)


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
        # `name` is UNIQUE: the night's generation ignored the do-not-repeat
        # list it was given. Nothing to retry from here any more -- the fix is
        # a new file, so this says which trick collided and stops.
        raise ContentError(
            f"tonight's file gives a trick you have already had ({trick.name}); "
            f"regenerate it — `--trick-context` lists what has been taught"
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
