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

import re
from dataclasses import dataclass

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
