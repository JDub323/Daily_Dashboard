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

    Bucket 1 is capped at `max_repeats` rungs per round. Uncapped it took every
    rung it was due on, and a round could be five words you had already been
    asked -- which is the relearn rule working exactly as written and reading,
    from the other side of the screen, as a bee that had run out of ideas. The
    allowance goes to the strongest claim on it rather than to whichever tier
    the ladder lists first, so the one repeat you get is the word you have
    missed most, not the easiest word you happen to have missed once.

    Falling off the end of all three means the tier is exhausted -- every word
    in it is inside its cooldown -- and the rung takes the least recent word
    regardless. A rung with no allowance and no unseen word does the same, and
    for the same reason: asking nothing is worse than asking something twice.
    That also means the cap is best-effort and stops binding once the pool has
    cycled, when there is no such thing as a word you have not had.

    Deterministic: same pool, same history, same day, same five words. No RNG,
    because there is nothing here a random choice would decide better than
    "the one you have gone longest without".
    """
    rungs = [[w for w in pool if w.tier == tier] for tier in cfg.ladder]
    spenders = _allowance(rungs, seen, cfg, today)

    picked: list[d.SpellingWord] = []
    used: set[str] = set()

    for i, candidates in enumerate(rungs):
        free = [w for w in candidates if w.word.casefold() not in used]
        if not free:
            # No word of this tier left. Widening to another tier would quietly
            # change what the rung is worth, so the rung is dropped instead and
            # the day is short -- visible, rather than mis-rated.
            log.warning("spelling pool has no unused %r word for today's ladder", cfg.ladder[i])
            continue
        choice = _best(free, seen, cfg, today, relearn=i in spenders)
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


def _allowance(
    rungs: list[list[PoolWord]],
    seen: dict[str, Seen],
    cfg: SpellingSettings,
    today: dt.date,
) -> set[int]:
    """Which rungs may ask a word you have missed before.

    Ranked across the whole ladder by how strong each rung's claim is -- the
    word missed most, then longest unseen -- so the day's one relearn slot goes
    where it is worth most. Ties break towards the easier rung, which is the
    ladder's own order.

    Two rungs of the same tier draw on the same candidates and therefore make
    the same claim; only the first can win it, and the second is left to pick
    something new. That is deliberate: two rungs of the same tier are two
    chances to meet a word, and spending both on one word you already know you
    cannot spell is not what the second rung is for.
    """
    if cfg.max_repeats <= 0:
        return set()
    claims = []
    for i, candidates in enumerate(rungs):
        due = _due_misses(candidates, seen, cfg, today)
        if due:
            best = max(due, key=lambda w: _claim(w, seen, today))
            missed, age = _claim(best, seen, today)
            claims.append((-missed, -age, i))
    claims.sort()
    return {i for _, _, i in claims[: cfg.max_repeats]}


def _claim(word: PoolWord, seen: dict[str, Seen], today: dt.date) -> tuple[int, int]:
    """How much a missed word deserves to come back: misses, then age."""
    record = seen[word.word.casefold()]
    return record.missed, (today - record.last_seen).days


def _due_misses(
    candidates: list[PoolWord], seen: dict[str, Seen], cfg: SpellingSettings, today: dt.date
) -> list[PoolWord]:
    """Words here that were missed and whose `relearn_days` have passed."""
    return [
        w
        for w in candidates
        if (record := seen.get(w.word.casefold()))
        and record.missed
        and (today - record.last_seen).days >= cfg.relearn_days
    ]


def _best(
    candidates: list[PoolWord],
    seen: dict[str, Seen],
    cfg: SpellingSettings,
    today: dt.date,
    *,
    relearn: bool = True,
) -> PoolWord:
    def history(word: PoolWord) -> Seen | None:
        return seen.get(word.word.casefold())

    def age(word: PoolWord) -> int:
        record = history(word)
        return (today - record.last_seen).days if record else 10_000

    unseen = [w for w in candidates if history(w) is None]
    if not relearn and unseen:
        # This rung is over the day's allowance, and there is something new to
        # ask instead. Where there is not, it falls through to the order below
        # and repeats -- if the day is going to repeat anyway, it should at
        # least repeat something you got wrong.
        return unseen[0]

    due_misses = _due_misses(candidates, seen, cfg, today)
    if due_misses:
        # Most missed first, then longest unseen: the word you have got wrong
        # three times outranks the one you got wrong once.
        return max(due_misses, key=lambda w: (history(w).missed, age(w)))

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
