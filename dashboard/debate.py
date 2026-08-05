"""The daily debate: the ballot, the prompt, the ballot's parser, the dossier.

Everything in here is pure string work over config and stored rows. There is
deliberately **no LLM call in this module and no import of `llm`** -- the
argument itself happens in the Claude app on a subscription, and the dashboard's
whole job is to assemble a prompt going out and parse a ballot coming back. That
is what lets a feature the size of the math problem cost nothing per day.

Three things live here that are worth reading before changing:

* `RUBRIC` -- eight banded criteria, lifted from real ballots rather than
  invented. Their provenance is on each entry.
* `PROMPT` assembly -- what the opponent is told, including the instruction not
  to write anything to memory. Use a dedicated Claude *Project*: project memory
  is isolated from global chat memory, and that boundary is the real control.
  The line in the prompt is a courtesy on top of it.
* `parse_scorecard` + `apply_quote_rule` -- the ballot is re-scored here, never
  trusted. A judge that hands back its own total is a judge whose scale you
  have to trust across model versions, and the elo has to outlive those.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, NamedTuple

from . import logs
from .config import DAYS, DebateAngle, DebateIssue, DebateSettings
from .models import domain as d
from .models.api import NewsItem
from .models.common import DebateSide, FactStance

log = logs.get(__name__)


class DebateError(RuntimeError):
    """A pasted ballot that cannot be used as-is."""


# --- the ballot ------------------------------------------------------------


class Criterion(NamedTuple):
    """One line of the ballot, with the band descriptors that make it
    checkable rather than a matter of taste."""

    key: str
    label: str
    asks: str
    bands: tuple[str, str, str]
    """What 0, 1 and 2 look like. Written into the prompt verbatim -- a band
    the judge has to invent is a band that drifts."""

    source: str
    """Which real ballot this came from. Kept so that changing one is a
    decision about a rubric rather than a change of mood."""


RUBRIC: tuple[Criterion, ...] = (
    Criterion(
        key="framing",
        label="issue framing",
        asks="Did I state what is actually in dispute and what would settle it?",
        bands=(
            "the issue is mentioned but the dispute is vague or the burden unstated",
            "the dispute is stated and most of the relevant context given",
            "the dispute is stated precisely, with the burden named and the context that "
            "makes it a real question",
        ),
        source="AAC&U Critical Thinking VALUE rubric, 'Explanation of issues'",
    ),
    Criterion(
        key="evidence",
        label="evidence",
        asks="Were my claims carried by specific sourced facts rather than assertion?",
        bands=(
            "claims are asserted; figures are vague, absent or unattributed",
            "some specific figures, attribution incomplete",
            "specific and relevant throughout, attributed to a source I can name",
        ),
        source="AAC&U Critical Thinking VALUE rubric, 'Evidence'",
    ),
    Criterion(
        key="warrant",
        label="warrant",
        asks="Did I connect evidence to conclusion with a stated mechanism?",
        bands=(
            "facts are placed next to a conclusion with no link between them",
            "a mechanism is gestured at but has a step missing",
            "an explicit causal chain from the evidence to the claim, each step statable",
        ),
        source="Toulmin: the warrant, and its backing",
    ),
    Criterion(
        key="assumptions",
        label="assumptions",
        asks="Did I name the unstated premises — mine as well as yours?",
        bands=(
            "assumptions on both sides go unexamined",
            "an assumption is identified, usually the opponent's",
            "own and opposing premises both named, with what turns on each",
        ),
        source="AAC&U Critical Thinking VALUE rubric, 'Context and assumptions'",
    ),
    Criterion(
        key="clash",
        label="clash",
        asks="Did I answer the strongest thing said against me, or the weakest?",
        bands=(
            "no real engagement — restatement of my own case, or an attack on the "
            "weakest point available",
            "engaged, but with a softened version of the argument",
            "took on the strongest form of the opposing argument directly",
        ),
        source="NSDA Lincoln-Douglas: clash on the major arguments is required",
    ),
    Criterion(
        key="coverage",
        label="dropped arguments",
        asks="Did anything put to me go unanswered?",
        bands=(
            "a central point was dropped and never returned to",
            "everything central was answered; something minor was dropped",
            "nothing put to me went unanswered",
        ),
        source="NSDA: a dropped argument is a conceded argument",
    ),
    Criterion(
        key="weighing",
        label="weighing",
        asks="Did I say why my argument outweighs theirs, on a named metric?",
        bands=(
            "no comparison — two cases left side by side",
            "a claim that mine matters more, without a metric",
            "explicit weighing on a stated metric: magnitude, probability, "
            "reversibility, timeframe or who bears it",
        ),
        source="WSDC: 'strategy' — how the matter is utilised",
    ),
    Criterion(
        key="qualification",
        label="qualification and concession",
        asks="Did I mark the limits of my own case, and concede what landed?",
        bands=(
            "no limits acknowledged; nothing conceded, including the points that landed",
            "some hedging, or a concession made grudgingly and late",
            "the boundary of the claim stated up front, and anything that landed conceded plainly",
        ),
        source="AAC&U 'Conclusions … limitations'; Toulmin: the qualifier",
    ),
)

RUBRIC_KEYS = tuple(c.key for c in RUBRIC)


class FoulKind(NamedTuple):
    key: str
    description: str


FOULS: tuple[FoulKind, ...] = (
    FoulKind("fabrication", "stated a fact, figure, quotation or citation that is not real"),
    FoulKind(
        "goalpost",
        "changed the claim after conceding a point, instead of accepting the loss",
    ),
    FoulKind(
        "new-in-closing",
        "introduced a brand-new argument in the final speech, where it cannot be answered",
    ),
    FoulKind("motive", "attacked motives, character or identity in place of the argument"),
)

FOUL_KEYS = tuple(f.key for f in FOULS)

MIN_QUOTE = 12
"""How much verbatim quotation an item must carry to score above zero.

The one structural defence against an opponent grading the person it has spent
half an hour being agreeable to. It does not make fabrication impossible -- the
transcript is not here to check against -- it makes it effortful, and it makes
a judge that wanted to be generous produce evidence it can be caught on.
"""


# --- what is being argued today --------------------------------------------


def week_start(cfg: DebateSettings, date: dt.date) -> dt.date:
    """The reading day the week containing `date` opened on.

    The week's reading list is keyed by this, so Wednesday can still see what
    Sunday was given. Derived from `reading_day` rather than hardcoded to
    Sunday: moving the reading day has to move the key with it, or a week's
    list becomes unreachable the moment the config changes.
    """
    wanted = DAYS.index(cfg.reading_day.lower())
    return date - dt.timedelta(days=(date.weekday() - wanted) % 7)


def motion_for(issue: DebateIssue, subarea: str) -> str:
    """The frame the resolution is sharpened from.

    Deliberately not a resolution. Writing "This house would deport…" from a
    template produces a motion that is about nothing in particular, and the one
    thing this is for is being in tune with *current* events -- so the frame is
    stored and the opponent, which has web search in the app, is told to name
    the live instance of it before anything starts. Phase 2's overnight batch
    resolves this to a specific motion ahead of time; until then the sharpening
    happens in the first thirty seconds of the conversation, at no cost.
    """
    return f"US policy on {subarea} ({issue.name})"


def side_for(cfg: DebateSettings, date: dt.date, angle: DebateAngle | None) -> DebateSide:
    """Which side the dashboard assigns.

    `pro` means arguing for the position you hold; `con` means arguing against
    it. Not "for the resolution" -- the resolution is not written yet when this
    is decided, and tying the assignment to a sentence the opponent has not
    produced would make it meaningless.

    Two ways to land on `con`: a configured contra day, or the steelman angle,
    which is *defined* as arguing the case you do not hold and would be
    incoherent on the other side.
    """
    if angle is not None and angle.key == "steelman":
        return DebateSide.con
    return DebateSide.con if cfg.is_contra_day(date) else DebateSide.pro


# --- what the 3am task left ------------------------------------------------
#
# Both readers return None for every failure mode -- no file, bad JSON, a file
# describing a different day. `content._from_batch` set the rule and it holds
# here: a batch is an optimisation, and an optimisation that can take the panel
# down is not one. Anything surprising goes to the log with a traceback and the
# caller falls through to the template frame or to RSS.


def motion_path(directory: Path, date: dt.date) -> Path:
    return directory / f"debate-{date.isoformat()}.json"


def reading_path(directory: Path, week: dt.date) -> Path:
    return directory / f"reading-{week.isoformat()}.json"


def _load(path: Path, model: Any) -> Any | None:
    if not path.exists():
        return None
    try:
        return model(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        log.exception("unusable batch file at %s; falling through", path)
        return None


def motion_from_batch(directory: Path, date: dt.date, issue: str, subarea: str) -> Any | None:
    """Tonight's resolved motion, or None.

    The issue and sub-area are checked rather than trusted. The rotation is
    editable and the file was written eight hours ago against whatever the
    config said then; serving a motion about semiconductors under an
    immigration heading would be worse than serving the frame, because it would
    look deliberate.
    """
    from .models.batch import DebateMotion

    motion = _load(motion_path(directory, date), DebateMotion)
    if motion is None:
        return None
    if motion.issue != issue or motion.subarea != subarea:
        log.warning(
            "batch motion for %s is about %s/%s, not %s/%s; using the frame instead",
            date,
            motion.issue,
            motion.subarea,
            issue,
            subarea,
        )
        return None
    return motion


def readings_from_batch(directory: Path, week: dt.date, issue: str, subarea: str) -> Any | None:
    """This week's reading list, or None. Checked the same way, for the same
    reason -- a week's list about the wrong sub-area is a week of wrong facts.
    """
    from .models.batch import ReadingList

    listing = _load(reading_path(directory, week), ReadingList)
    if listing is None:
        return None
    if listing.issue != issue or listing.subarea != subarea:
        log.warning(
            "reading list for week %s is about %s/%s, not %s/%s; ignoring it",
            week,
            listing.issue,
            listing.subarea,
            issue,
            subarea,
        )
        return None
    return listing


def to_readings(listing: Any, week: dt.date) -> list[d.DebateReading]:
    """A validated `ReadingList` -> the rows that go in the table."""
    rows = [
        d.DebateReading(
            week_start=week,
            issue=listing.issue,
            subarea=listing.subarea,
            kind="article",
            title=article.title,
            url=article.url,
            publisher=article.publisher or None,
            published=article.published,
            summary=article.summary or None,
            required=article.required,
        )
        for article in listing.articles
    ]
    rows += [
        d.DebateReading(
            week_start=week,
            issue=listing.issue,
            subarea=listing.subarea,
            kind="book",
            title=book.title,
            url=book.url,
            author=book.author or None,
            summary=book.why or None,
            lean=book.lean,
            # Never required, and the schema enforces it too. A hundred-odd
            # book recommendations over a six-month cycle is already the part
            # most likely to go unread; one that could block a day would be
            # worse than one that is ignored.
            required=False,
        )
        for book in listing.books
    ]
    return rows


# --- the readings ----------------------------------------------------------


def keyword_matcher(keywords: list[str]) -> re.Pattern[str] | None:
    """One pattern matching any of `keywords` as a word.

    Three rules, each of which this code learned against real headlines on the
    day it was written:

    **Word boundaries, not substrings.** `interests.py` matches substrings,
    which is right for arXiv abstracts -- long research phrases, where the cost
    of "search" hitting "research" is a paper ranked one place too high. Here
    the terms are short, and substring matching offered "gas prices", "Spokane
    police" and "cherry ice cream" as immigration reading, all three because
    `ICE` sits inside them.

    **A trailing plural, and nothing else.** A closing `\b` on its own loses
    "green cards", "tariffs" and "immigrants" -- most of the forms anyone
    actually writes -- while dropping the closing boundary entirely puts `AI`
    back inside "aid" and "airline". `(?:e?s)?` keeps the plurals and none of
    the accidents.

    **An ALL-CAPS keyword is an acronym and matches case-sensitively.** Word
    boundaries alone still let `ICE` match "cherry ice cream", because there
    `ice` genuinely is a word -- just not the agency. Case is the only signal
    separating them, and it is one the config already carries: nobody writes the
    agency in lower case or the frozen water in upper. Everything else stays
    case-insensitive, so "Immigration" and "immigration" are one keyword.

    Returns None for an empty list, which is the honest answer to "which
    headlines are about this issue" when nothing has said what the issue is.
    """
    terms = []
    for keyword in keywords:
        stripped = keyword.strip()
        if not stripped:
            continue
        escaped = re.escape(stripped)
        # Scoped inline flags rather than two compiled patterns: the caller
        # wants one score per headline, and two match sets would have to be
        # merged and re-deduped to produce it.
        terms.append(escaped if stripped.isupper() else f"(?i:{escaped})")
    if not terms:
        return None
    # \b around the whole alternation rather than around each branch: a keyword
    # may be a phrase, and the boundaries that matter are its outer edges.
    return re.compile(rf"\b(?:{'|'.join(terms)})(?:e?s)?\b")


def pick_readings(
    candidates: list[NewsItem], issue: DebateIssue, limit: int = 4
) -> list[d.Reading]:
    """The day's articles for one issue, best match first.

    Scored by how many *distinct* keywords a headline hits, so a story naming
    both "asylum" and "border" outranks one that says "visa" three times.
    Stable, so equally-matching headlines keep feed order, which is roughly
    newest first.

    Only items that actually match are returned. An issue with nothing in
    today's feeds should produce an empty list and a panel that says so; padding
    it with the least-bad headline available would be worse than nothing,
    because it looks like an answer.
    """
    pattern = keyword_matcher(issue.keywords)
    if pattern is None:
        return []
    # Deduped on the singular, so a headline saying "tariff" once and "tariffs"
    # once has hit one keyword rather than two.
    scored = [
        (len({m.casefold().rstrip("s") for m in pattern.findall(item.title)}), item)
        for item in candidates
    ]
    return [
        d.Reading(title=item.title, link=item.link)
        for score, item in sorted(scored, key=lambda pair: -pair[0])
        if score > 0
    ][:limit]


# --- the prompt ------------------------------------------------------------

SIDE_BRIEF = {
    DebateSide.pro: (
        "your own position, the one you stated above. Practising the case you actually "
        "hold is half the point of this"
    ),
    DebateSide.con: (
        "AGAINST your own stated position. You do not have to believe it; you have to "
        "make it well enough that someone who does would endorse it"
    ),
}


def build_prompt(
    session: d.DebateSession,
    angle: DebateAngle,
    issue_name: str,
    facts: list[d.DebateFact],
    week_reading: list[d.DebateReading] | None = None,
) -> str:
    """The text you copy into the Claude app.

    Assembled here rather than in the browser because it embeds stored rows and
    because the rubric it quotes is the same object `score_card` grades against
    -- two copies of eight band descriptors would drift within a month.

    It is long on purpose. Every section is either something the opponent cannot
    get right without being told (the angle, the side, what you read) or a rule
    that stops it being pleasant to you instead of useful.
    """
    lines: list[str] = []
    add = lines.append
    resolved = session.motion_source == "batch"

    add("You are my debate opponent and, at the end, my judge. This is practice.")
    add("")
    add("**Housekeeping, first:** do not write anything from this conversation to memory,")
    add("and do not carry anything from it into other conversations. Treat it as scratch.")
    add("")
    add("---")
    add("")
    add("## Today")
    add("")
    add(f"- **Issue:** {issue_name}")
    add(f"- **This week's sub-area:** {session.subarea}")
    add(f"- **{'Resolution' if resolved else 'Frame'}:** {session.motion}")
    if session.motion_note:
        add(f"- **Why it is live:** {session.motion_note}")
    if session.motion_url:
        add(f"- **Source:** {session.motion_url}")
    add(f"- **Today's angle — {angle.label}:** {' '.join(angle.brief.split())}")
    add(f"- **Format:** {session.format}")
    add(f"- **I am arguing:** {SIDE_BRIEF[DebateSide(session.side)]}.")
    if session.contra:
        add(
            "- The dashboard assigned this side; I did not choose it. Do not go easy on "
            "that account — it is rated as the harder job already."
        )
    add("")

    if session.position:
        confidence = f" (confidence {session.confidence}/100)" if session.confidence else ""
        add("## What I believe going in")
        add("")
        add(f"> {session.position}{confidence}")
        add("")

    # Two lists, and the distinction matters to the comprehension check below.
    # The week's reading was assigned on the reading day and is what I am
    # supposed to have absorbed; today's headlines are whatever the feeds
    # happened to carry this morning. Only the first is fair to quiz me on.
    assigned = [r for r in (week_reading or []) if r.kind == "article" and r.read_on]
    if assigned:
        add("## The week's reading, which I have marked as read")
        add("")
        for reading in assigned:
            add(f"- {reading.title} — {reading.url}")
        add("")

    if session.readings:
        add("## Headlines I saw today")
        add("")
        for reading in session.readings:
            add(f"- {reading.title} — {reading.link}")
        add("")

    if facts:
        add("## The facts I collected")
        add("")
        add("These are mine, gathered before we started. Hold me to them: if I argue from")
        add("something that is not here and cannot source it on the spot, that is the")
        add("`evidence` line on the ballot, and inventing one is a foul.")
        add("")
        for fact in facts:
            add(f"- [{FactStance(fact.stance).value}] {fact.claim} — *{fact.source}*")
        add("")

    add("---")
    add("")
    add("## Step 0 — comprehension check")
    add("")
    if assigned:
        add("Before any arguing: ask me exactly three short questions about")
        add("**the week's reading** above — the pieces I said I read, not the headlines.")
        add("What a specific claim was, what a number was, or what the strongest point on")
        add("the other side in them was. One at a time. Tell me plainly which I got wrong,")
        add("and do not move on until all three are answered. Claiming to have read")
        add("something is a claim like any other; this is where it gets tested.")
    else:
        add("Before any arguing: ask me exactly three short questions about the articles")
        add("listed above — what a specific claim was, what a number was, or what the")
        add("strongest point on the other side in them was. One at a time. Tell me plainly")
        add("which I got wrong. Do not move on until all three are answered.")
    add("")
    if resolved:
        add("## Step 1 — confirm the resolution")
        add("")
        add("The resolution above was drawn up overnight against the news. Check it is")
        add("still accurate — one search is enough — and say so if it has moved. Then")
        add("confirm which side I am on and begin.")
    else:
        add("## Step 1 — the resolution")
        add("")
        add("Search for the most live instance of this sub-area in roughly the last three")
        add("months — a bill, rule, ruling, or event actually in play — and state the")
        add("resolution in one sentence. Make it specific enough to be false. Then confirm")
        add("which side I am on and begin.")
    add("")
    add("## How to run it")
    add("")
    add("- One question at a time. Wait for my answer.")
    add("- Do not let me dodge. If I answer a question I was not asked, say so and ask")
    add("  the original one again.")
    add("- Attack the strongest version of my case, never a caricature of it.")
    add("- Do not concede to be agreeable, and do not praise an argument to soften a")
    add("  point you are about to make. If I am winning a point, say so once and move on.")
    add("- Web search is fine and encouraged for facts, on both our sides.")
    add("- Run for as long as it takes to test the case — roughly 30-40 minutes of my")
    add("  time. When you are done, or when I say `ballot`, go to Step 3.")
    add("")
    add("## Step 3 — the ballot")
    add("")
    add("Score me on these eight lines. Each is 0, 1 or 2, and the bands are fixed:")
    add("")
    for i, criterion in enumerate(RUBRIC, 1):
        add(f"{i}. **{criterion.key}** — {criterion.asks}")
        for band, text in enumerate(criterion.bands):
            add(f"   - `{band}` — {text}")
    add("")
    add("Then any hard fouls, each of which costs a point outright:")
    add("")
    for foul in FOULS:
        add(f"- `{foul.key}` — {foul.description}")
    add("")
    add("**Rules for you as judge — these matter more than the scores:**")
    add("")
    add("- Every line scoring 1 or 2 must carry a `quote`: my own words, verbatim, at")
    add(f"  least {MIN_QUOTE} characters, showing what earned it. **If you cannot quote")
    add("  it, score it 0.** An unquoted point is discarded automatically at my end, so")
    add("  padding the ballot only makes it less informative, never higher.")
    add("- Judge the debate that happened, not the one I meant to have.")
    add("- You have spent the last half hour being conversational with me. That is not")
    add("  evidence about the quality of my arguments. A 2 is 'nothing to add here',")
    add("  not 'good effort'; most lines in a real practice round are 1s.")
    add("- Do not total the scores or state a rating. Report the lines; the arithmetic")
    add("  happens elsewhere and is not yours to shade.")
    add("")
    add("Output **exactly one** fenced JSON block, and nothing after it:")
    add("")
    add("```json")
    add(
        json.dumps(
            {
                "items": [
                    {
                        "key": RUBRIC[0].key,
                        "score": 1,
                        "quote": "my words, verbatim",
                        "note": "one line: what was missing, or what earned it",
                    },
                    {"key": "…one object per line above, all eight…"},
                ],
                "fouls": [{"kind": FOULS[0].key, "quote": "my words, verbatim"}],
                "verdict": "one or two sentences: the single thing to fix next time",
            },
            indent=2,
        )
    )
    add("```")
    return "\n".join(lines)


# --- reading the ballot back -----------------------------------------------

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _candidates(text: str) -> list[str]:
    """Every substring of a paste that might be the ballot, best guess first.

    A paste is whatever came out of a chat window: usually a fenced block, but
    sometimes the block plus a paragraph of commentary, sometimes two blocks
    because the model showed its working, and occasionally just the braces. The
    fences are tried last-first because when a model emits two, the second is
    the real one and the first is an illustration of the format.
    """
    fenced = [m.group(1).strip() for m in _FENCE.finditer(text)]
    out = list(reversed(fenced))
    out.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        out.append(text[start : end + 1])
    return out


def parse_scorecard(text: str) -> d.Scorecard:
    """A pasted ballot -> a validated `Scorecard`, or `DebateError`.

    Unknown item keys are dropped with a warning rather than rejected: the
    rubric is allowed to change, and a judge that invented a ninth line should
    cost that line, not the whole evening. A key appearing twice keeps the
    *lowest* score, which is the only tie-break that cannot be exploited by
    listing a line again with a better number.
    """
    if not text.strip():
        raise DebateError("nothing pasted")

    raw: Any = None
    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and "items" in parsed:
            raw = parsed
            break
    if raw is None:
        raise DebateError(
            "no ballot found in that paste — look for the ```json block at the end "
            "of the debate and copy it whole"
        )

    items: dict[str, d.RubricItem] = {}
    for entry in raw.get("items") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip().casefold()
        if key not in RUBRIC_KEYS:
            log.warning("ballot named an unknown rubric line %r; dropped", entry.get("key"))
            continue
        try:
            item = d.RubricItem(
                key=key,
                # Clamped rather than validated: a judge that wrote 3 meant
                # "full marks", and failing the whole paste over it would throw
                # away a debate that already happened.
                score=max(0, min(2, int(entry.get("score", 0)))),
                quote=str(entry.get("quote") or "")[:600],
                note=str(entry.get("note") or "")[:400],
            )
        except (TypeError, ValueError):
            log.warning("ballot line %r had an unusable score; dropped", key)
            continue
        existing = items.get(key)
        if existing is None or item.score < existing.score:
            items[key] = item

    if not items:
        raise DebateError("that ballot scored none of the eight lines")

    fouls = [
        d.Foul(kind=kind, quote=str(entry.get("quote") or "")[:600])
        for entry in (raw.get("fouls") or [])
        if isinstance(entry, dict)
        and (kind := str(entry.get("kind", "")).strip().casefold()) in FOUL_KEYS
    ]

    return d.Scorecard(
        # Rubric order, not the order the judge happened to write them in, so
        # the panel reads the same way every day.
        items=[items[key] for key in RUBRIC_KEYS if key in items],
        fouls=fouls,
        verdict=str(raw.get("verdict") or "")[:600],
    )


def apply_quote_rule(card: d.Scorecard) -> d.Scorecard:
    """Zero every line that claimed a point without quoting for it.

    Enforced here rather than trusted to the prompt, because a rule that lives
    only in a prompt is a rule that holds until the model has a reason not to
    follow it -- and the model's reason, at the end of a friendly half hour, is
    that it would rather be encouraging. The prompt says this happens so the
    judge knows padding is pointless; this is what makes that true.
    """
    voided: list[str] = []
    items: list[d.RubricItem] = []
    for item in card.items:
        if item.score > 0 and len(item.quote.strip()) < MIN_QUOTE:
            voided.append(item.key)
            items.append(item.model_copy(update={"score": 0}))
        else:
            items.append(item)
    if voided:
        log.info("voided unquoted ballot lines: %s", ", ".join(voided))
    return card.model_copy(update={"items": items, "voided": voided})


def score_card(card: d.Scorecard) -> float:
    """The ballot's score in [0, 1]. Always out of the full rubric."""
    from . import scoring

    return scoring.debate_score(
        [item.score for item in card.items], fouls=len(card.fouls), items=len(RUBRIC)
    )


# --- the dossier -----------------------------------------------------------

DOSSIER_HEADER = (
    "<!-- Rendered from the `debate_fact` and `debate_session` tables by the\n"
    "     dashboard. Every write rewrites this file, so edits here are lost.\n"
    "     It exists to be read and grepped; the database is the record. -->\n"
)


def _fact_lines(facts: list[d.DebateFact], stance: FactStance) -> list[str]:
    return [
        f"- **{fact.date}** — {fact.claim}  \n  *{fact.source}*"
        for fact in facts
        if FactStance(fact.stance) is stance
    ]


def render_dossier(
    issue: DebateIssue,
    facts: list[d.DebateFact],
    sessions: list[d.DebateSession],
) -> str:
    """One issue's dossier, as markdown.

    The section list is the one from the original sketch, minus the three that
    need synthesis rather than accumulation -- background, common fallacies, and
    open questions. Those are phase 2, and rendering them as empty headings now
    would be a promise the file does not keep.
    """
    lines = [DOSSIER_HEADER, f"# {issue.name}", ""]
    add = lines.append

    add(f"`{issue.slug}` · {len(facts)} fact(s) · {len(sessions)} debate(s)")
    add("")

    add("## Where I stand")
    add("")
    stances = [s for s in sessions if s.position]
    if stances:
        add("Newest first. This is the only thing here that tracks being *right*")
        add("rather than being persuasive.")
        add("")
        for session in reversed(stances):
            confidence = f" ({session.confidence}/100)" if session.confidence else ""
            add(f"- **{session.date}**{confidence} — {session.position}")
            if session.position_after and session.position_after != session.position:
                after = f" ({session.confidence_after}/100)" if session.confidence_after else ""
                add(f"  - after arguing{after}: {session.position_after}")
    else:
        add("*Nothing recorded yet.*")
    add("")

    for stance, heading in (
        (FactStance.for_, "Evidence for"),
        (FactStance.against, "Evidence against"),
        (FactStance.context, "Context"),
    ):
        add(f"## {heading}")
        add("")
        body = _fact_lines(facts, stance)
        lines.extend(body or ["*Nothing yet.*"])
        add("")

    add("## Debates")
    add("")
    if sessions:
        add("| date | sub-area | angle | side | score |")
        add("| --- | --- | --- | --- | --- |")
        for session in reversed(sessions):
            score = "—" if session.score is None else f"{session.score:.2f}"
            add(
                f"| {session.date} | {session.subarea} | {session.angle} "
                f"| {DebateSide(session.side).value} | {score} |"
            )
    else:
        add("*None yet.*")
    add("")
    return "\n".join(lines)


def write_dossier(directory: Path, issue: DebateIssue, text: str) -> Path | None:
    """Write one dossier, returning its path, or None if it could not be.

    Never raises. This is an export of data that is already safely in SQLite,
    and a full disk or a read-only directory must not be able to fail the write
    that produced the row -- the fact is recorded either way, and the worst case
    is a stale markdown file with a warning in the log beside it.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{issue.slug}.md"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        log.exception("could not write the dossier for %s", issue.slug)
        return None
