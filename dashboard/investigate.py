"""The investigation: the pasted list, the file, the prompt, and the ballot.

The class hands out things to look into; one is drawn a day, written up in a
real editor, graded in the Claude app, and pasted back. Nothing in this module
talks to an API -- the expensive half runs on a subscription, and everything
here is a string being assembled or a ballot being read, which is what makes
the feature free.

Four sections, in the order a day moves through them:

* the paste     -- a handout, however it was formatted, into topics
* the file      -- one markdown file per topic, which is the source of truth
* the ballot    -- the rubric, the prompt that quotes it, and the parser
* filing        -- which subject folder the finished write-up belongs in

The paste parser is deliberately not a parser in the `todo.py` sense. That one
has a grammar because a todo line carries a date, a calendar and a repetition;
a topic carries nothing but itself, so this strips the decoration a list picks
up on its way through a slide deck and stops. Anything cleverer would be
guessing at text a human is going to read back verbatim.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from .models import domain as d

log = logging.getLogger(__name__)

MIN_TOPIC = 3
MAX_TOPIC = 300

#: Leading list decoration: bullets, and enumerations of both the "1." and
#: "a)" kinds. Anchored and bounded so a topic that legitimately opens with a
#: number -- "2026 defence budget" -- keeps it: an enumerator has to be short
#: and followed by its punctuation.
_MARKER = re.compile(r"^\s*(?:[-*•·–—]+|\(?[0-9]{1,2}[.)]|\(?[a-zA-Z][.)])\s+")

#: Collapses the runs of whitespace a paste out of a PDF leaves behind, so two
#: copies of one topic differing only in spacing are caught as the duplicate
#: they are rather than admitted as two rows.
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedTopic:
    """One line of the box, and what became of it.

    `topic` is None exactly when `error` is set. Both are carried because the
    box reports per line -- a list of six where the fourth is a stray page
    number should add five things and say what it did with the sixth, not
    refuse the paste.
    """

    raw: str
    topic: str | None = None
    error: str | None = None


def clean(line: str) -> str:
    """One line, stripped of list decoration and collapsed whitespace."""
    return _SPACE.sub(" ", _MARKER.sub("", line)).strip(" \t.;,")


def parse_topics(text: str) -> list[ParsedTopic]:
    """The whole box, in order, with duplicates inside it collapsed.

    Blank lines vanish rather than becoming errors -- a paste is mostly blank
    lines and reporting each one would bury the report. Duplicates *within the
    paste* are caught here; duplicates against the pool are the database's job,
    because only it knows what is already open.
    """
    out: list[ParsedTopic] = []
    seen: set[str] = set()

    for line in text.splitlines():
        if not line.strip():
            continue
        topic = clean(line)
        if len(topic) < MIN_TOPIC:
            out.append(ParsedTopic(raw=line, error="too short to be a topic"))
            continue
        if len(topic) > MAX_TOPIC:
            out.append(ParsedTopic(raw=line, error=f"over {MAX_TOPIC} characters"))
            continue
        if topic.casefold() in seen:
            out.append(ParsedTopic(raw=line, error="listed twice in this paste"))
            continue
        seen.add(topic.casefold())
        out.append(ParsedTopic(raw=line, topic=topic))

    return out


# --- the file --------------------------------------------------------------
#
# One markdown file per topic, opened in a real editor. The database holds a
# snapshot of what was submitted; the file is what you typed and stays yours.

#: Everything a filename may keep. Anything else becomes a hyphen -- and the
#: result is only ever a *component*, never a path: it cannot contain a
#: separator, cannot be `..`, and cannot start with a dot.
_UNSAFE = re.compile(r"[^a-z0-9]+")

MAX_SLUG = 60


def slug(topic: str) -> str:
    """A filename for a topic.

    Deliberately lossy and deliberately not unique. Collisions are resolved by
    the caller against what is already on disk, because two topics that slug
    the same are two topics that a human would also confuse, and the answer to
    that is a visible `-2` rather than a hash nobody can read.

    Accents are folded rather than stripped, so "Côte d'Ivoire sanctions"
    becomes `cote-d-ivoire-sanctions` and not `c-te-d-ivoire-sanctions`.
    """
    folded = unicodedata.normalize("NFKD", topic).encode("ascii", "ignore").decode()
    cleaned = _UNSAFE.sub("-", folded.lower()).strip("-")
    if len(cleaned) > MAX_SLUG:
        # Cut at a hyphen so the name ends on a whole word rather than mid-one.
        cleaned = cleaned[:MAX_SLUG].rsplit("-", 1)[0] or cleaned[:MAX_SLUG]
    return cleaned or "untitled"


def scaffold(topic: str, drawn_on: dt.date) -> str:
    """What a new file starts as: the topic, and nothing else.

    Two lines rather than a template with headings to fill in. A scaffold with
    prompts in it is a scaffold you end up editing around, and `body()` below
    would then have to know which parts were yours.
    """
    return f"# {topic}\n\n<!-- drawn {drawn_on.isoformat()} -->\n\n"


#: The scaffold, as something to recognise: an H1, and the drawn-on comment.
_SCAFFOLD = re.compile(r"\A\s*#[^\n]*\n|\A\s*<!--[^>]*-->\n", re.MULTILINE)


def body(text: str) -> str:
    """What you actually wrote -- the file minus the scaffold.

    Used for one decision only: whether the day has been written up. A file
    holding nothing but the heading the server put there is not a write-up,
    and a panel that called it one would let an empty buffer take the prompt.

    The scaffold is *stripped for this test only*. What gets submitted, quoted
    and graded is the whole file, heading included -- it is a markdown document
    and its title is part of it.
    """
    stripped = text
    while True:
        cut = _SCAFFOLD.sub("", stripped, count=1)
        if cut == stripped:
            return stripped.strip()
        stripped = cut


def file_for(root: Path, topic: str, existing: str | None) -> Path:
    """The file for a topic, resolved under `root`.

    `existing` is the path already recorded for this topic, and it wins
    outright -- renaming a file out from under a topic that has been written up
    would orphan the work. Only a topic that has never had one gets a name
    chosen here, and only then does the collision suffix apply.
    """
    if existing:
        return root / existing

    stem = slug(topic)
    candidate = root / f"{stem}.md"
    n = 2
    while candidate.exists():
        candidate = root / f"{stem}-{n}.md"
        n += 1
    return candidate


def ensure_file(path: Path, topic: str, drawn_on: dt.date) -> Path:
    """Create the file if it is missing. Never touch one that exists.

    [Invariant] The server does not write to a file it did not create. These
    hold hours of typing and there is no undo outside the editor, so "create if
    absent" is the entire write path -- there is no branch here that opens an
    existing file for writing, by design rather than by omission.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(scaffold(topic, drawn_on), encoding="utf-8")
    return path


def read_file(path: Path) -> str:
    """The file's text, or empty if it is not there.

    A missing file is not an error: it means the topic was drawn and the editor
    never opened, which is exactly the state the panel is drawing.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# --- the ballot ------------------------------------------------------------
#
# Five lines, not the debate's eight. A write-up has fewer moving parts than an
# argument: there is no opponent, so there is nothing to clash with and nothing
# to weigh against a rebuttal. What is left is whether the claims are sourced,
# whether they are true, whether you got past the first page of results,
# whether the reasoning holds, and whether you were honest about what you do
# not know.


class InvestigateError(RuntimeError):
    """A pasted result that cannot be used as-is."""


class Criterion(NamedTuple):
    """One line of the ballot, with the bands that make it checkable rather
    than a matter of the grader's mood."""

    key: str
    label: str
    asks: str
    bands: tuple[str, str, str]
    """What 0, 1 and 2 look like, written into the prompt verbatim. A band the
    grader has to invent is a band that drifts between model versions."""


RUBRIC: tuple[Criterion, ...] = (
    Criterion(
        key="sourcing",
        label="sourcing",
        asks="Can every factual claim be traced to something I named?",
        bands=(
            "claims are asserted; sources are absent, vague, or 'reports say'",
            "the load-bearing claims are attributed, the rest are not",
            "every claim that carries weight names a source specific enough to check",
        ),
    ),
    Criterion(
        key="accuracy",
        label="accuracy",
        asks="Are the claims actually true?",
        bands=(
            "a load-bearing claim is wrong, or badly misstated",
            "broadly right, with an error or an overstatement that does not sink it",
            "everything checkable checks out, including the numbers and the dates",
        ),
    ),
    Criterion(
        key="depth",
        label="depth",
        asks="Did I get past what the first page of results would have told me?",
        bands=(
            "a summary of the obvious; anyone could have written it in five minutes",
            "one layer down — a mechanism, an actor or a constraint the summary omits",
            "the thing a specialist would say is the actual crux, not the headline",
        ),
    ),
    Criterion(
        key="reasoning",
        label="reasoning",
        asks="Do the causal and strategic claims follow from what I established?",
        bands=(
            "conclusions sit next to facts with no link between them",
            "a mechanism is given but a step is missing or an alternative unconsidered",
            "an explicit chain, with the obvious competing explanation addressed",
        ),
    ),
    Criterion(
        key="calibration",
        label="calibration",
        asks="Was I honest about what I do not know, and what would change my mind?",
        bands=(
            "stated flatly throughout, or hedged on everything equally",
            "some uncertainty marked, but not where it actually sits",
            "confidence tracks the evidence, and what would change my mind is named",
        ),
    ),
)

RUBRIC_KEYS = {c.key for c in RUBRIC}

MAX_SCORE = 2 * len(RUBRIC)


# --- the prompt ------------------------------------------------------------


def build_prompt(topic: str, write_up: str, folders: list[str]) -> str:
    """The text you copy into the Claude app.

    Assembled here rather than in the browser because it quotes the rubric that
    `parse_ballot` grades against, and two copies of five band descriptors
    would disagree inside a month.

    Long on purpose. Every section is either something the grader cannot get
    right without being told, or a rule that stops it being pleasant to you
    instead of useful -- and the second kind carries the weight here, because
    there is no opponent to be unpleasant on your behalf.
    """
    lines: list[str] = []
    add = lines.append

    add("You are grading a piece of research I wrote for a politics class, and")
    add("checking whether it is true. This is practice; I want it marked hard.")
    add("")
    add("**Housekeeping, first:** do not write anything from this conversation to memory,")
    add("and do not carry anything from it into other conversations. Treat it as scratch.")
    add("")
    add("---")
    add("")
    add(f"## The topic I was given\n\n> {topic}")
    add("")
    add("## What I wrote")
    add("")
    add("````markdown")
    add(write_up.strip())
    add("````")
    add("")
    add("## What to do")
    add("")
    add("1. **Check the claims.** Search for the load-bearing factual ones. I would")
    add("   rather be told I am wrong than told I am thorough. Every correction goes in")
    add("   `corrections` with a source I can go and read.")
    add("2. **Grade the five lines below**, 0, 1 or 2 each, against the bands as written.")
    add("   Do not invent a sixth line and do not rescale.")
    add("3. **Write the feedback**, addressed to me, in prose. This is the part I will")
    add("   actually read twice: what specifically to do differently next time.")
    add("")
    add("Two rules about the grading:")
    add("")
    add("- A 2 on **accuracy** is not available if you found anything for `corrections`.")
    add("  The server enforces this, so awarding it anyway only makes your ballot")
    add("  disagree with the score I am shown.")
    add("- Grade what is on the page. Do not credit what I clearly know but did not")
    add("  write down, and do not mark me up for effort.")
    add("")
    add("## The rubric")
    add("")
    for c in RUBRIC:
        add(f"**{c.label}** (`{c.key}`) — {c.asks}")
        add(f"- 0: {c.bands[0]}")
        add(f"- 1: {c.bands[1]}")
        add(f"- 2: {c.bands[2]}")
        add("")

    add("## What to send back")
    add("")
    add("A single ```json block, last thing in your reply, exactly this shape:")
    add("")
    add("```json")
    add("{")
    add('  "items": [')
    for i, c in enumerate(RUBRIC):
        comma = "," if i < len(RUBRIC) - 1 else ""
        add(f'    {{"key": "{c.key}", "score": 0, "note": "one line, why"}}{comma}')
    add("  ],")
    add('  "corrections": [')
    add('    {"claim": "what I said", "correction": "what is actually so", '
        '"source": "where you checked"}')
    add("  ],")
    add('  "feedback": "Prose, addressed to me. The part worth reading twice.",')
    add('  "patterns": ["named reasoning moves worth learning, at most three"],')
    add('  "verdict": "one line"')
    add("}")
    add("```")
    add("")
    add("`corrections` may be empty. `feedback` may not — if the write-up was good, say")
    add("what specifically made it good and what the next hard version would add.")

    # The folder list rides along so the classifier and the grader cannot
    # disagree about what the subject areas *are*. Cheap: it is one short list,
    # and it is the same one `classify_folder` chooses from.
    if folders:
        add("")
        add("For reference, the subject areas this gets filed under: " + ", ".join(folders) + ".")

    return "\n".join(lines)


# --- reading the ballot back -----------------------------------------------

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _candidates(text: str) -> list[str]:
    """Every substring of a paste that might be the ballot, best guess first.

    A paste is whatever came out of a chat window: usually a fenced block, but
    sometimes the block plus a paragraph of commentary, sometimes two blocks
    because the model showed the shape first, and occasionally just the braces.
    Fences are tried last-first, because when a model emits two the second is
    the real one and the first is an illustration of the format.
    """
    fenced = [m.group(1).strip() for m in _FENCE.finditer(text)]
    out = list(reversed(fenced))
    out.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        out.append(text[start : end + 1])
    return out


def parse_ballot(text: str) -> d.InvestigationBallot:
    """A pasted result -> a validated ballot, or `InvestigateError`.

    Unknown item keys are dropped with a warning rather than rejected: the
    rubric is allowed to change, and a grader that invented a sixth line should
    cost that line rather than the whole afternoon. A key appearing twice keeps
    the *lowest* score, the one tie-break that cannot be exploited by listing a
    line again with a better number.
    """
    if not text.strip():
        raise InvestigateError("nothing pasted")

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
        raise InvestigateError(
            "no ballot found in that paste — look for the ```json block at the end "
            "of the reply and copy it whole"
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
            # Clamped rather than rejected: a grader that wrote 3 meant "full
            # marks", and failing the paste over it would throw away work that
            # has already happened.
            score = max(0, min(2, int(entry.get("score", 0))))
        except (TypeError, ValueError):
            log.warning("ballot line %r had an unusable score; dropped", key)
            continue
        item = d.RubricItem(key=key, score=score, note=str(entry.get("note") or "")[:400])
        if key in items:
            item = min(items[key], item, key=lambda i: i.score)
        items[key] = item

    if not items:
        raise InvestigateError("that ballot had no usable rubric lines in it")

    corrections = [
        d.Correction(
            claim=str(c.get("claim") or "")[:600],
            correction=str(c.get("correction") or "")[:600],
            source=str(c.get("source") or "")[:400],
        )
        for c in (raw.get("corrections") or [])
        if isinstance(c, dict) and (c.get("claim") or c.get("correction"))
    ]

    return apply_accuracy_rule(
        d.InvestigationBallot(
            # Rubric order, not the order the grader happened to write them in,
            # so the panel's five lines are always in the same places.
            items=[items[c.key] for c in RUBRIC if c.key in items],
            corrections=corrections,
            feedback=str(raw.get("feedback") or "").strip()[:6000],
            patterns=[str(p)[:120] for p in (raw.get("patterns") or [])][:3],
            verdict=str(raw.get("verdict") or "").strip()[:400],
        )
    )


def apply_accuracy_rule(ballot: d.InvestigationBallot) -> d.InvestigationBallot:
    """Full marks for accuracy are not available once something was corrected.

    Enforced here rather than trusted to the prompt: a rule that lives only in a
    prompt holds until the model has a reason not to follow it, and a grader's
    standing reason is that it would rather be encouraging. The prompt says this
    happens so the ballot is not surprised by it, but this is what makes it true.
    """
    if not ballot.corrections:
        return ballot
    capped = [
        item.model_copy(update={"score": min(item.score, 1)})
        if item.key == "accuracy" and item.score > 1
        else item
        for item in ballot.items
    ]
    if capped == ballot.items:
        return ballot
    return ballot.model_copy(update={"items": capped, "capped": ["accuracy"]})


def score_ballot(ballot: d.InvestigationBallot) -> float:
    """The ballot's score in [0, 1], always out of the full rubric.

    Re-scored here rather than taken from the grader. A judge that hands back
    its own total is a judge whose scale you have to trust across model
    versions, and this rating is meant to outlive several.
    """
    return sum(item.score for item in ballot.items) / MAX_SCORE


# --- filing --------------------------------------------------------------


def known_folders(root: Path, seeded: list[str]) -> list[str]:
    """The folders to choose from: the config's seed, plus whatever is on disk.

    The union, and in that order, so a folder you created by hand is offered to
    the classifier immediately and a seed you deleted from config stops being
    offered without anything having to be migrated. De-duplicated on the slug,
    because `national security and defense` and `National Security and Defense`
    are one directory on a case-insensitive filesystem and must be one entry
    here too.
    """
    seen: dict[str, str] = {}
    for name in seeded:
        seen.setdefault(slug(name), name)
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                seen.setdefault(slug(child.name), child.name)
    return list(seen.values())


def _write_ups_in(directory: Path) -> int:
    """Markdown files directly in a folder. Sub-folders are not counted --
    a folder that has already been split is not still overflowing."""
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.iterdir() if p.is_file() and p.suffix == ".md")


def subfolders_of(root: Path, folder: str) -> list[str]:
    directory = root / slug(folder)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))


def needs_splitting(root: Path, folder: str, split_at: int) -> bool:
    """Has this folder got too bulky to keep filing into flat?

    [Invariant] This decides where the *next* file goes and nothing else.
    Crossing the threshold never moves anything already written -- a bulk
    re-file of a directory of your own prose, decided by a model, is not
    something the server does unasked. So the folder stays as it is and grows
    sub-folders alongside its existing files.
    """
    return _write_ups_in(root / slug(folder)) >= split_at


def filed_path(root: Path, folder: str, sub: str | None, current: Path) -> Path:
    """Where a write-up belongs once its folder is known.

    The filename is kept, not recomputed. It was chosen when the topic was
    first opened and it is what the `path` column has been pointing at through
    however many editing sessions -- rebuilding it here would rename a file for
    no reason at the one moment it matters least.
    """
    directory = root / slug(folder)
    if sub:
        directory = directory / slug(sub)
    return directory / current.name


def move_write_up(current: Path, target: Path) -> Path:
    """Move a write-up into its folder, refusing to clobber anything.

    A collision means two topics slugged the same and were filed together,
    which is rare and recoverable; overwriting would lose one of them, which is
    neither. The suffix is visible on purpose -- a `-2` you can see beats a
    hash you cannot read.
    """
    if current == target:
        return current
    target.parent.mkdir(parents=True, exist_ok=True)

    final, n = target, 2
    while final.exists():
        final = target.with_name(f"{target.stem}-{n}{target.suffix}")
        n += 1

    if current.exists():
        current.rename(final)
    return final
