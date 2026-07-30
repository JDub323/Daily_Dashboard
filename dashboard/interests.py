"""What you want to read, stated rather than inferred.

The alternative was learning a preference from clicks. Two things killed
that. Clicks are unobservable here without either a fragile JS handler --
middle-click, cmd-click and "open in new tab" produce different events or
none -- or routing every link through a local redirect, which puts this
server in the browser history for every story you read. And a model learned
from a handful of triage decisions is a confident guess dressed as
personalisation: at one surfaced paper a day, "enough examples to beat
recency" is months away.

A file you write yourself needs no history, costs nothing per day, is wrong
only in ways you can see and fix, and answers the question the same way on
day one as on day four hundred. It also stays useful if a learned ranker is
ever added: this is the profile such a thing would need as context anyway.

Format is markdown, and deliberately loose. Bullet lines under a heading
containing "not" (or "avoid", or "exclude") are things to push down; bullets
under any other heading push up. Prose outside bullets is ignored by the
scorer and kept for a reader -- including, eventually, a model.

    # Interested in
    - mechanistic interpretability
    - evaluation and benchmarks

    ## Not interested in
    - federated learning
    - medical imaging
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import logs

log = logs.get(__name__)

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")

NEGATIVE_HEADINGS = ("not", "avoid", "exclude", "uninterested", "skip")

EXCLUDE_WEIGHT = 2.0
"""How much one unwanted term outweighs one wanted term. Above 1 so that a
paper matching something you explicitly rejected sinks below one matching
nothing at all -- an opt-out you have taken the trouble to write should be
stronger than an opt-in you have not."""


@dataclass
class Interests:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.include and not self.exclude

    def score(self, *texts: str) -> float:
        """Wanted terms found, minus unwanted ones weighted heavier.

        Substring matching on a casefolded haystack, not word matching:
        "interpretability" should hit "interpretable" and the terms are
        phrases as often as words. The cost is that a short term can match
        inside an unrelated word, which is why the file wants phrases.
        """
        hay = " ".join(texts).casefold()
        hits = sum(1 for t in self.include if t in hay)
        misses = sum(1 for t in self.exclude if t in hay)
        return hits - EXCLUDE_WEIGHT * misses


def parse(text: str) -> Interests:
    """Bullets, bucketed by the heading above them."""
    interests = Interests()
    negative = False
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            words = heading.group(1).casefold()
            negative = any(w in words for w in NEGATIVE_HEADINGS)
            continue
        bullet = BULLET.match(line)
        if not bullet:
            continue
        term = bullet.group(1).strip().casefold()
        # Strip the markdown a person naturally types around a phrase.
        term = re.sub(r"[*_`]", "", term).strip()
        if term:
            (interests.exclude if negative else interests.include).append(term)
    return interests


def load(path: Path) -> Interests:
    """Read the file, or an empty profile if it is absent or unreadable.

    Absent is the normal case and not a warning: the file is optional, and a
    dashboard with no interests file simply keeps arXiv's own ordering.
    """
    if not path.exists():
        return Interests()
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        log.exception("could not read the interests file at %s", path)
        return Interests()


def rank(candidates: list, interests: Interests, *, text_of) -> list:
    """Reorder by score, most wanted first. Stable, so anything the profile
    is silent about keeps the order it arrived in -- which for arXiv is
    newest first, the behaviour this replaced."""
    if interests.empty:
        return list(candidates)
    return sorted(candidates, key=lambda c: -interests.score(*text_of(c)))
