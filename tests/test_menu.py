"""The meal menu's rules (`dashboard/menu.py`): earn, fade, learn, hold still."""

from __future__ import annotations

import datetime as dt
import itertools

from dashboard import menu
from dashboard.models import domain as d

TODAY = dt.date(2026, 10, 14)  # a Wednesday
_ids = itertools.count(1)


def meal(slug, days_ago=0, hour=12, size="large", quality=5, flags=()):
    return d.MealLog(
        id=next(_ids),
        date=TODAY - dt.timedelta(days=days_ago),
        hour=hour,
        size=size,
        quality=quality,
        preset=slug,
        flags=list(flags),
    )


def menu_of(meals, names=(), today=TODAY):
    ordered = sorted(meals, key=lambda m: (m.date, m.hour, m.id))
    return {n.slug: n for n in menu.meal_menu(names, ordered, today)}


# --- earning a place ----------------------------------------------------------


def test_three_meals_inside_three_weeks_earn_a_place():
    got = menu_of([meal("sushi", 20), meal("sushi", 10), meal("sushi", 0)])
    assert got["sushi"].live


def test_three_meals_spread_wider_than_three_weeks_do_not():
    got = menu_of([meal("sushi", 21), meal("sushi", 10), meal("sushi", 0)])
    assert not got["sushi"].live


def test_a_one_off_exists_but_is_not_drawn():
    got = menu_of([meal("sushi")])
    assert got["sushi"].count == 1 and not got["sushi"].live


# --- losing it ------------------------------------------------------------------


def test_thirty_days_uneaten_hides_an_earned_name():
    three = [meal("sushi", 40), meal("sushi", 39), meal("sushi", 38)]
    assert not menu_of(three)["sushi"].live
    assert menu_of(three, today=TODAY - dt.timedelta(days=9))["sushi"].live


def test_one_meal_brings_an_earned_name_straight_back():
    """Earned is for good; only *recent* is lost."""
    got = menu_of([meal("sushi", 90), meal("sushi", 89), meal("sushi", 88), meal("sushi", 0)])
    assert got["sushi"].live


def test_a_given_name_gets_thirty_days_and_then_the_same_terms():
    given = [("apple", "apple", TODAY - dt.timedelta(days=29))]
    assert menu_of([], given)["apple"].live
    given = [("apple", "apple", TODAY - dt.timedelta(days=30))]
    assert not menu_of([], given)["apple"].live
    assert menu_of([meal("apple", 0)], given)["apple"].live


def test_a_meal_after_today_does_not_count():
    """The browser's today is the only today: a row filed tomorrow (the clock
    was wrong) must not keep a name alive or teach it anything."""
    got = menu_of([meal("sushi", -1)], [("sushi", "sushi", None)])
    assert got["sushi"].count == 0


# --- what a name fills in ----------------------------------------------------------


def test_fills_are_the_answers_given_most_often():
    got = menu_of(
        [
            meal("eggs", 3, size="medium", quality=4),
            meal("eggs", 2, size="medium", quality=3),
            meal("eggs", 1, size="large", quality=4),
        ]
    )
    fills = got["eggs"].fills
    assert (fills.size, fills.quality) == ("medium", 4)


def test_a_tie_goes_to_the_plate_as_it_is_now():
    got = menu_of([meal("eggs", 2, quality=3), meal("eggs", 1, quality=4)])
    assert got["eggs"].fills.quality == 4


def test_a_flag_is_filled_when_it_was_on_more_than_half_the_time():
    got = menu_of(
        [
            meal("shake", 3, flags=["protein", "snack"]),
            meal("shake", 2, flags=["protein"]),
            meal("shake", 1, flags=["protein", "snack"]),
        ]
    )
    assert got["shake"].fills.flags == ["protein", "snack"]
    got = menu_of(
        [
            meal("shake", 3, flags=["protein"]),
            meal("shake", 2, flags=["protein"]),
            meal("shake", 1, flags=["protein", "snack"]),
        ]
    )
    assert got["shake"].fills.flags == ["protein"]


def test_only_the_last_ten_meals_teach():
    old = [meal("eggs", 30 - i, quality=2) for i in range(10)]
    new = [meal("eggs", 10 - i, quality=5) for i in range(10)]
    assert menu_of(old + new)["eggs"].fills.quality == 5


def test_a_variation_with_no_meals_borrows_its_parents_fills():
    got = menu_of(
        [meal("chicken_rice", 1, size="large", quality=5, flags=["protein"])],
        [("chicken_rice.salsa", "salsa", None)],
    )
    assert got["chicken_rice.salsa"].fills == got["chicken_rice"].fills
    assert got["chicken_rice.salsa"].parent == "chicken_rice"


def test_a_name_never_eaten_fills_nothing():
    assert menu_of([], [("apple", "apple", TODAY)])["apple"].fills is None


# --- order ------------------------------------------------------------------------


def test_rank_is_by_how_often_then_by_name():
    got = menu_of(
        [meal("eggs", 5), meal("eggs", 6), meal("shake", 5)],
        [("apple", "apple", TODAY), ("banana", "banana", TODAY)],
    )
    assert [s for s, _ in sorted(((s, n.rank) for s, n in got.items()), key=lambda p: p[1])] == [
        "eggs",
        "shake",
        "apple",
        "banana",
    ]


def test_a_base_meal_counts_its_variations():
    got = menu_of(
        [
            meal("chicken_rice.salsa", 5),
            meal("chicken_rice.salsa", 6),
            meal("eggs", 5),
        ]
    )
    assert got["chicken_rice"].rank < got["eggs"].rank


def test_the_order_holds_still_until_sunday():
    """Wednesday's meals do not move Wednesday's menu -- the anchor is the
    Sunday before, so a thumb that learned the order on Monday keeps it."""
    assert menu.rank_anchor(TODAY) == dt.date(2026, 10, 11)
    assert menu.rank_anchor(dt.date(2026, 10, 11)) == dt.date(2026, 10, 11)
    got = menu_of([meal("eggs", 10), meal("shake", 0), meal("shake", 1)])
    assert got["eggs"].rank < got["shake"].rank


def test_variations_are_ranked_among_their_siblings_only():
    got = menu_of([meal("eggs", 5), meal("chicken_rice.salsa", 5)])
    assert got["chicken_rice.salsa"].rank == 0


# --- when it is eaten ---------------------------------------------------------------


def test_hours_count_the_last_thirty_days():
    got = menu_of([meal("eggs", 1, hour=9), meal("eggs", 2, hour=9), meal("eggs", 40, hour=9)])
    assert got["eggs"].hours[9] == 2
    assert sum(got["eggs"].hours) == 2
