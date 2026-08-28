"""The investigation pool: reading a pasted list, and nothing else yet.

The class hands out a list of things to look into. That list arrives as a
paste -- off a slide, out of an email, from a handout retyped in a hurry --
so the only interesting logic in this module is turning whatever shape it
arrives in into topics.

Deliberately not a parser in the `todo.py` sense. That one has a grammar
because a todo line carries a date, a calendar and a repetition; a topic
carries nothing but itself, so this strips the decoration a list picks up on
its way through a slide deck and stops. Anything cleverer would be guessing at
text a human is going to read back verbatim.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

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
