"""The spelling bee: the pool, the draw, and what counts as spelling it right.

Pure over its inputs -- no LLM, no database, no clock. The pool is a file, the
draw takes the history it needs as an argument, and the answer check is a
string comparison. That is the whole feature's cost profile: nothing per day.

The design decision worth knowing before changing anything here is *what the
draw optimises for*. Not novelty. A bee whose only rule was "never repeat"
would ask you the words you already spell correctly and then move on from the
ones you do not, which is the opposite of the point. So a missed word is
preferred once it is eligible again, and only after those are exhausted does
the draw reach for something new.
"""

from __future__ import annotations

import datetime as dt
import json
import unicodedata
from pathlib import Path
from typing import NamedTuple

from . import logs
from .config import SpellingSettings
from .models import domain as d

log = logs.get(__name__)


class PoolWord(NamedTuple):
    """One entry in `spelling.json`, before it becomes a drawn word."""

    word: str
    tier: str
    hint: str


def load_pool(path: Path) -> list[PoolWord]:
    """The curated word pool. Never written by the server.

    Returns an empty list for every failure mode rather than raising: an
    unreadable pool means the bee has nothing to ask, which the panel says, and
    that is a better morning than a dashboard that will not boot.

    Entries are deduplicated on the word, keeping the first. The same word in
    two tiers would otherwise be two different-value rows sharing one primary
    key in `spelling_seen`, and the second would silently overwrite the first's
    history.
    """
    if not path.exists():
        log.warning("no spelling pool at %s", path)
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("could not read the spelling pool at %s", path)
        return []

    entries = raw.get("words") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        log.error("%s should hold a list of words, or an object with a `words` key", path)
        return []

    out: list[PoolWord] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word", "")).strip()
        tier = str(entry.get("tier", "")).strip()
        if not word or not tier or word.casefold() in seen:
            if word and word.casefold() in seen:
                log.warning("spelling pool lists %r more than once; keeping the first", word)
            continue
        seen.add(word.casefold())
        out.append(PoolWord(word=word, tier=tier, hint=str(entry.get("hint", "")).strip()))
    return out


class Seen(NamedTuple):
    """One word's history, as the draw needs it."""

    last_seen: dt.date
    missed: int


def draw(
    pool: list[PoolWord],
    seen: dict[str, Seen],
    cfg: SpellingSettings,
    today: dt.date,
) -> list[d.SpellingWord]:
    """Today's words, easiest first, one per rung of the ladder.

    Each rung takes the best candidate of its tier, where "best" is three
    buckets in order:

    1. **Words you have missed** and whose `relearn_days` have passed, oldest
       miss first. This is the bucket the feature exists for.
    2. **Words never asked**, in pool order.
    3. **Everything else**, least recent first, and only past `cooldown_days`.

    Falling off the end of all three means the tier is exhausted -- every word
    in it is inside its cooldown -- and the rung takes the least recent word
    regardless. That is the "repeats rare, but possible" rule: with a pool this
    size it happens when a thin tier cycles, not otherwise, and asking nothing
    would be worse than asking something twice.

    Deterministic: same pool, same history, same day, same five words. No RNG,
    because there is nothing here a random choice would decide better than
    "the one you have gone longest without".
    """
    picked: list[d.SpellingWord] = []
    used: set[str] = set()

    for tier in cfg.ladder:
        candidates = [w for w in pool if w.tier == tier and w.word.casefold() not in used]
        if not candidates:
            # No word of this tier left. Widening to another tier would quietly
            # change what the rung is worth, so the rung is dropped instead and
            # the day is short -- visible, rather than mis-rated.
            log.warning("spelling pool has no unused %r word for today's ladder", tier)
            continue
        choice = _best(candidates, seen, cfg, today)
        used.add(choice.word.casefold())
        picked.append(
            d.SpellingWord(
                word=choice.word,
                tier=choice.tier,
                hint=choice.hint,
                anchor=cfg.anchor_for(choice.tier),
            )
        )
    return picked


def _best(
    candidates: list[PoolWord], seen: dict[str, Seen], cfg: SpellingSettings, today: dt.date
) -> PoolWord:
    def history(word: PoolWord) -> Seen | None:
        return seen.get(word.word.casefold())

    def age(word: PoolWord) -> int:
        record = history(word)
        return (today - record.last_seen).days if record else 10_000

    due_misses = [
        w
        for w in candidates
        if (record := history(w)) and record.missed and age(w) >= cfg.relearn_days
    ]
    if due_misses:
        # Most missed first, then longest unseen: the word you have got wrong
        # three times outranks the one you got wrong once.
        return max(due_misses, key=lambda w: (history(w).missed, age(w)))

    unseen = [w for w in candidates if history(w) is None]
    if unseen:
        return unseen[0]

    off_cooldown = [w for w in candidates if age(w) >= cfg.cooldown_days]
    return max(off_cooldown or candidates, key=age)


# --- marking ---------------------------------------------------------------


def normalise(text: str) -> str:
    """What two spellings have to share to count as the same.

    Case and surrounding whitespace go, and Unicode is folded to NFC so a
    composed and a decomposed accent compare equal -- a real hazard when one
    side was typed on a phone keyboard and the other came out of a JSON file.

    Nothing else. Internal punctuation stays, because `hors d'oeuvre` and
    `billet-doux` are spelled with it and dropping it would mark a wrong answer
    right. Internal spaces stay for the same reason.
    """
    return unicodedata.normalize("NFC", text.strip().casefold())


def is_correct(answer: str, word: str) -> bool:
    return bool(answer.strip()) and normalise(answer) == normalise(word)


def mark(words: list[d.SpellingWord], answers: list[str]) -> list[bool]:
    """One verdict per word. A short answer list scores the rest wrong.

    Short rather than an error because the panel submits the whole round at
    once and a missing box is a blank answer, which is wrong -- not a malformed
    request. Extra answers are ignored for the matching reason.
    """
    return [
        is_correct(answers[i], word.word) if i < len(answers) else False
        for i, word in enumerate(words)
    ]
