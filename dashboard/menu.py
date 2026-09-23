"""The meal menu: which names are on it, in what order, and what they fill in.

Every answer here is read off `meal_log` at request time. Nothing about a
name's standing is stored, so there is no status column to drift from the meals
that decide it, and deleting a mis-logged meal quietly un-says whatever it
said.

The rules, all of them his choices (2026-09-23):

- **A name earns its place** by being eaten three times inside three weeks.
  Until then it exists -- the phone's name box finds it -- but the tree does
  not draw it, so a one-off does not cost a slot on a screen you read twice a
  day.
- **It loses its place** after thirty days uneaten, and eating it once more puts
  it straight back. Earned is for good; only *recent* is lost.
- **What it fills in is learned**, never set: the answers given most often over
  its last ten meals. Tap a name, correct the quality, log it, and the next tap
  is a little more right. A variation with no meals of its own borrows its
  parent's.
- **The order moves once a week.** See `rank_anchor`.

Pure, like `health.py`, and like `health.py` it never feeds a name into a
score. It reads `size`, `quality` and `flags` only to hand them back as
suggestions.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Sequence
from typing import TypeVar

from .models import api
from .models import domain as d
from .models.common import MealFlag

EARN_LOGS = 3
EARN_DAYS = 21
HIDE_DAYS = 30
FILL_LOGS = 10
HOUR_DAYS = 30
RANK_DAYS = 30

T = TypeVar("T", bound=Hashable)


def rank_anchor(today: dt.date) -> dt.date:
    """The Sunday on or before `today` -- the day the order was last decided.

    The menu is ranked by how often each thing was eaten in the thirty days up
    to this date rather than up to today, so the order holds still for a week
    at a time. That is the one finding the adaptive-menu research is unanimous
    about (split menus, Sears & Shneiderman; Findlater & McGrenere 2004): a
    list that reorders itself costs more than it saves once a thumb has
    learned where things are. The part that *should* move with the day is the
    phone's "likely now" row, and that is a separate row for exactly this
    reason.
    """
    return today - dt.timedelta(days=(today.weekday() + 1) % 7)


def earned(dates: Sequence[dt.date]) -> bool:
    """Whether any three consecutive meals fall inside one 21-day window.

    `dates` sorted. Consecutive is enough: if any three are inside a window, the
    three nearest each other are."""
    span = dt.timedelta(days=EARN_DAYS - 1)
    return any(
        dates[i + EARN_LOGS - 1] - dates[i] <= span for i in range(len(dates) - EARN_LOGS + 1)
    )


def _mode(values: Sequence[T]) -> T:
    """The commonest value, ties going to whichever was seen last -- the plate
    as it is now beats the plate as it was."""
    counts = Counter(values)
    latest = {value: i for i, value in enumerate(values)}
    return max(counts, key=lambda value: (counts[value], latest[value]))


def learned(meals: Sequence[d.MealLog]) -> api.MealFills | None:
    """What these meals, oldest first, say the name fills in.

    Size and quality are the mode. A flag is on if it was on for more than half
    of them, and at exactly half it follows the latest -- so a name logged
    twice, once as a snack and once not, fills in whatever it was last time.
    """
    recent = list(meals)[-FILL_LOGS:]
    if not recent:
        return None
    flags = [
        flag
        for flag in MealFlag
        if (on := sum(flag in meal.flags for meal in recent)) * 2 > len(recent)
        or (on * 2 == len(recent) and flag in recent[-1].flags)
    ]
    return api.MealFills(
        size=_mode([meal.size for meal in recent]),
        quality=_mode([meal.quality for meal in recent]),
        flags=flags,
    )


def meal_menu(
    names: Iterable[tuple[str, str, dt.date | None]],
    meals: Sequence[d.MealLog],
    today: dt.date,
) -> list[api.MealName]:
    """Every name, with its standing as of `today`.

    `names` is `Repository.meal_names`; `meals` is every meal with a name,
    oldest first. A meal naming a slug with no row is still counted -- under
    its slug spelled out -- and so is a variation's missing parent, because the
    write path makes both impossible and a menu that dropped them would be
    hiding the bug.
    """
    by_slug: dict[str, list[d.MealLog]] = defaultdict(list)
    for meal in meals:
        if meal.preset and meal.date <= today:
            by_slug[meal.preset].append(meal)

    table = {slug: (label, given) for slug, label, given in names}
    for slug in [*by_slug, *(s.partition(".")[0] for s in [*by_slug, *table] if "." in s)]:
        table.setdefault(slug, (slug.rpartition(".")[2].replace("_", " "), None))

    anchor = rank_anchor(today)
    window = dt.timedelta(days=RANK_DAYS - 1)

    def weight(slug: str) -> int:
        """Meals in the thirty days up to the anchor. A base meal counts its
        variations too: chicken and rice eaten five ways is eaten a lot."""
        return sum(
            anchor - window <= meal.date <= anchor
            for other, rows in by_slug.items()
            if other == slug or other.startswith(f"{slug}.")
            for meal in rows
        )

    # Rank within each level: by weight, then by label so that ties -- every
    # new name starts at zero -- sit still too.
    siblings: dict[str | None, list[str]] = defaultdict(list)
    for slug in table:
        parent, dot, _ = slug.partition(".")
        siblings[parent if dot else None].append(slug)
    rank: dict[str, int] = {}
    for group in siblings.values():
        ordered = sorted(group, key=lambda slug: (-weight(slug), table[slug][0].lower(), slug))
        rank.update({slug: i for i, slug in enumerate(ordered)})

    out = []
    for slug, (label, given) in table.items():
        rows = by_slug.get(slug, [])
        dates = [meal.date for meal in rows]
        parent, dot, _ = slug.partition(".")
        latest = max([*dates[-1:], *([given] if given else [])], default=None)
        live = (
            (given is not None or earned(dates))
            and latest is not None
            and (today - latest).days < HIDE_DAYS
        )
        hours = [0] * 24
        for meal in rows:
            if (today - meal.date).days < HOUR_DAYS:
                hours[meal.hour] += 1
        fills = learned(rows)
        if fills is None and dot:
            fills = learned(by_slug.get(parent, []))
        out.append(
            api.MealName(
                slug=slug,
                label=label,
                parent=parent if dot else None,
                live=live,
                rank=rank[slug],
                fills=fills,
                hours=hours,
                count=len(rows),
                last=dates[-1] if dates else None,
            )
        )
    return sorted(out, key=lambda name: name.slug)
