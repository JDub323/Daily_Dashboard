"""The weekly topic rotation.

The topic used to be a module constant that only ever changed when someone
edited Python. It is now a list in config.toml indexed by the week, and the
week turns over on Sunday -- in the *browser's* timezone, like every other
date in the app.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from dashboard.config import MathSettings, load_settings, week_index

from .conftest import ROOT

ANCHORS = {"amc": 1100, "aime-late": 1500}
SUNDAY = dt.date(2026, 7, 26)  # the anchor shipped in config.toml


def rotating(*topics, start=SUNDAY):
    return MathSettings(
        topic_of_week="fallback", rotation=list(topics), rotation_start=start, anchors=ANCHORS
    )


# --- the week index --------------------------------------------------------


def test_the_index_steps_on_sunday():
    """date(1, 1, 1) is a Monday, so Sundays are the ordinals divisible by
    seven and floor division lands the boundary there."""
    saturday = dt.date(2026, 7, 25)
    sunday = dt.date(2026, 7, 26)
    monday = dt.date(2026, 7, 27)
    assert week_index(saturday) + 1 == week_index(sunday)
    assert week_index(sunday) == week_index(monday)


def test_every_day_of_one_week_shares_an_index():
    indices = {week_index(SUNDAY + dt.timedelta(days=n)) for n in range(7)}
    assert len(indices) == 1


def test_the_index_is_continuous_across_new_year():
    """The reason this is not `isocalendar().week`: ISO week numbers reset
    every January, so a rotation indexed by them would skip an entry."""
    dec = dt.date(2026, 12, 27)  # a Sunday
    assert dec.weekday() == 6
    for n in range(0, 60, 7):
        assert week_index(dec + dt.timedelta(days=n)) == week_index(dec) + n // 7


# --- topic selection -------------------------------------------------------


def test_the_topic_holds_all_week_and_flips_on_sunday():
    math = rotating("first", "second")
    for n in range(7):
        assert math.topic_for(SUNDAY + dt.timedelta(days=n)) == "first"
    assert math.topic_for(SUNDAY + dt.timedelta(days=7)) == "second"


def test_saturday_is_still_the_old_topic():
    math = rotating("first", "second")
    assert math.topic_for(SUNDAY + dt.timedelta(days=6)) == "first"
    assert math.topic_for(SUNDAY + dt.timedelta(days=7)) == "second"


def test_the_rotation_wraps():
    math = rotating("a", "b", "c")
    assert math.topic_for(SUNDAY + dt.timedelta(weeks=3)) == "a"
    assert math.topic_for(SUNDAY + dt.timedelta(weeks=4)) == "b"


def test_dates_before_the_anchor_wrap_backwards_rather_than_crashing():
    """Python's % is non-negative for a positive modulus, so looking at a week
    before rotation_start walks backwards through the list instead of raising
    an IndexError."""
    math = rotating("a", "b", "c")
    assert math.topic_for(SUNDAY - dt.timedelta(weeks=1)) == "c"
    assert math.topic_for(SUNDAY - dt.timedelta(weeks=4)) == "c"


def test_an_empty_rotation_falls_back_to_the_fixed_topic():
    math = MathSettings(topic_of_week="generating functions", anchors=ANCHORS)
    assert math.topic_for(SUNDAY) == "generating functions"
    assert math.topic_for(SUNDAY + dt.timedelta(weeks=5)) == "generating functions"


# --- validation ------------------------------------------------------------


def test_a_rotation_without_an_anchor_is_rejected():
    with pytest.raises(ValidationError, match="needs a rotation_start"):
        MathSettings(topic_of_week="x", rotation=["a"], anchors=ANCHORS)


def test_an_anchor_that_is_not_a_sunday_is_rejected():
    """Otherwise the flip silently happens on some other weekday and nobody
    notices for a week."""
    with pytest.raises(ValidationError, match="must be a Sunday"):
        rotating("a", start=dt.date(2026, 7, 27))  # a Monday


def test_the_error_names_the_offending_weekday():
    with pytest.raises(ValidationError, match="Monday"):
        rotating("a", start=dt.date(2026, 7, 27))


# --- the shipped configuration ---------------------------------------------


@pytest.fixture
def shipped():
    return load_settings(ROOT / "config.toml").math


def test_the_shipped_rotation_is_anchored_to_a_sunday(shipped):
    assert shipped.rotation_start.weekday() == 6


def test_generating_functions_holds_the_anchor_week(shipped):
    """Kept for one more week by request; the rotation proper starts after."""
    assert shipped.topic_for(SUNDAY) == "generating functions"
    assert shipped.topic_for(SUNDAY + dt.timedelta(days=6)) == "generating functions"
    assert shipped.topic_for(SUNDAY + dt.timedelta(days=7)) != "generating functions"


def test_the_shipped_rotation_has_no_duplicates(shipped):
    assert len(set(shipped.rotation)) == len(shipped.rotation)


def test_the_shipped_rotation_is_a_half_year_cycle(shipped):
    assert len(shipped.rotation) == 25


AREAS = {
    "generating functions": "combinatorics",
    "limits and recursive sequences": "analysis",
    "polynomial roots, Vieta, and symmetric functions": "algebra",
    "bijections, inclusion-exclusion, and double counting": "combinatorics",
    "modular arithmetic and congruences": "number theory",
    "analytic and vector geometry in the plane": "geometry",
    "infinite series - convergence, telescoping, rearrangement": "analysis",
    "determinants and matrix manipulation": "algebra",
    "linear recurrences and difference equations": "combinatorics",
    "divisibility, primes, and factorization": "number theory",
    "discrete probability and expected value": "probability",
    "definite-integral technique": "analysis",
    "polynomial identities, divisibility, and irreducibility": "algebra",
    "pigeonhole and extremal arguments": "combinatorics",
    "digits, base representations, and floor functions": "number theory",
    "convexity, lattice points, and area arguments": "geometry",
    "inequalities - AM-GM, Cauchy-Schwarz, Jensen, rearrangement": "analysis",
    "eigenvalues, characteristic polynomials, and matrix powers": "algebra",
    "combinatorial games and invariants": "combinatorics",
    "multiplicative order, primitive roots, Fermat/Euler": "number theory",
    "continuity, IVT, and the mean value theorems": "analysis",
    "vector spaces, rank, and linear independence": "algebra",
    "derivatives, Taylor expansion, and extrema": "analysis",
    "finite group theory - orders, Lagrange, group actions": "algebra",
    "functional equations": "analysis",
}

# Slots per area, proportional to that area's share of Putnam problems.
EXPECTED_SLOTS = {
    "analysis": 7,  # ~28%
    "algebra": 6,  # ~24%
    "combinatorics": 5,  # ~20%
    "number theory": 4,  # ~16%
    "geometry": 2,  # ~8%
    "probability": 1,  # ~4%
}


def test_the_area_map_covers_the_shipped_rotation(shipped):
    assert set(shipped.rotation) == set(AREAS), "rotation and area map are out of sync"


def test_no_area_repeats_in_consecutive_weeks(shipped):
    """The list is interleaved so you never get seven straight weeks of
    analysis. This pins the property rather than the exact ordering."""
    sequence = [AREAS[t] for t in shipped.rotation]
    repeats = [
        (shipped.rotation[i], shipped.rotation[i + 1])
        for i in range(len(sequence) - 1)
        if sequence[i] == sequence[i + 1]
    ]
    assert repeats == []


def test_the_weighting_matches_the_putnam_shares(shipped):
    counts: dict[str, int] = {}
    for topic in shipped.rotation:
        counts[AREAS[topic]] = counts.get(AREAS[topic], 0) + 1
    assert counts == EXPECTED_SLOTS


def test_analysis_gets_seven_times_the_slots_of_probability():
    """The weighting rule: slot count is proportional to Putnam share, so a
    ~28% area gets ~7x the coverage of a ~4% one."""
    assert EXPECTED_SLOTS["analysis"] == 7 * EXPECTED_SLOTS["probability"]


def test_every_rotation_entry_is_specific_enough_to_study(shipped):
    """ "group theory", not "abstract algebra": a week is not long enough for
    a whole field."""
    too_broad = {"algebra", "analysis", "combinatorics", "number theory", "geometry", "calculus"}
    assert not (too_broad & {t.lower() for t in shipped.rotation})
