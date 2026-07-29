"""Elo, hint penalties, the theorem draw and streaks.

These are the functions the plan singled out: the most defect-prone code in
the app, and until now the only way to verify the elo maths was to actually
solve a chess puzzle.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter

import pytest

from dashboard import scoring
from dashboard.models.common import ChessOutcome, MathOutcome

# --- K factor --------------------------------------------------------------


def test_k_factor_matches_the_documented_curve():
    """K = max(24, 96/(1 + n/8)), preserved exactly from app.js:87."""
    assert scoring.k_factor(0) == 96
    assert scoring.k_factor(8) == 48
    assert scoring.k_factor(16) == 32
    assert scoring.k_factor(24) == 24


def test_k_factor_floors_at_24_and_never_drops_below():
    assert scoring.k_factor(32) == 24
    assert scoring.k_factor(1000) == 24


def test_k_factor_decreases_monotonically():
    """High variance early, stabilising over time — the whole philosophy."""
    values = [scoring.k_factor(n) for n in range(0, 60)]
    assert values == sorted(values, reverse=True)


def test_k_factor_rejects_a_negative_count():
    with pytest.raises(ValueError):
        scoring.k_factor(-1)


# --- rounding --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 1), (1.5, 2), (2.5, 3), (-1.5, -1), (-0.5, 0), (0.49, 0)],
)
def test_rounding_matches_javascript_not_python(value, expected):
    """`Math.round`, not banker's rounding.

    Python's builtin round(0.5) is 0 and round(2.5) is 2. Porting the elo
    update to the server without this would shift ratings by a point at every
    half-integer — often enough to confuse, rare enough to look like drift.
    """
    assert scoring._round_half_up(value) == expected


# --- expected score / elo update -------------------------------------------


def test_equal_ratings_expect_a_draw():
    assert scoring.expected_score(1500, 1500) == pytest.approx(0.5)


def test_a_400_point_gap_is_ten_to_one():
    assert scoring.expected_score(1900, 1500) == pytest.approx(10 / 11, abs=1e-6)


def test_beating_an_equal_opponent_gains_half_of_k():
    # n=0 -> K=96, score 1.0 against an even opponent -> +48.
    assert scoring.elo_update(1500, 1500, 1.0, 0) == 1548


def test_losing_to_an_equal_opponent_loses_half_of_k():
    assert scoring.elo_update(1500, 1500, 0.0, 0) == 1452


def test_a_draw_against_an_equal_opponent_is_a_no_op():
    assert scoring.elo_update(1500, 1500, 0.5, 0) == 1500


def test_upsets_move_the_rating_further_than_expected_results():
    upset = scoring.elo_update(1500, 1900, 1.0, 8) - 1500
    expected = scoring.elo_update(1900, 1500, 1.0, 8) - 1900
    assert upset > expected


def test_later_results_move_the_rating_less():
    early = scoring.elo_update(1500, 1500, 1.0, 0) - 1500
    late = scoring.elo_update(1500, 1500, 1.0, 40) - 1500
    assert early == 48
    assert late == 12


# --- math score ------------------------------------------------------------


def test_a_clean_solve_is_worth_one():
    assert scoring.math_score(MathOutcome.solved, 0) == 1.0


@pytest.mark.parametrize(("hints", "score"), [(0, 1.0), (1, 0.7), (2, 0.4), (3, 0.1)])
def test_each_hint_costs_thirty_points(hints, score):
    assert scoring.math_score(MathOutcome.solved, hints) == pytest.approx(score)


def test_a_failure_scores_zero_regardless_of_hints():
    assert scoring.math_score(MathOutcome.failed, 0) == 0.0
    assert scoring.math_score(MathOutcome.failed, 3) == 0.0


def test_a_plain_string_outcome_is_coerced_not_silently_mistaken_for_solved():
    """`"failed" is MathOutcome.failed` is False — a StrEnum member is not the
    same object as an equal str, so the enum must be constructed, not compared
    by identity."""
    assert scoring.math_score("failed", 0) == 0.0


def test_hints_out_of_range_are_rejected():
    with pytest.raises(ValueError):
        scoring.math_score(MathOutcome.solved, 4)
    with pytest.raises(ValueError):
        scoring.math_score(MathOutcome.solved, -1)


# --- chess score -----------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "score"),
    [(ChessOutcome.clean, 1.0), (ChessOutcome.messy, 0.4), (ChessOutcome.failed, 0.0)],
)
def test_the_three_self_report_buttons(outcome, score):
    assert scoring.chess_score(outcome) == score


def test_chess_score_accepts_the_raw_string_from_the_wire():
    assert scoring.chess_score("messy") == 0.4


# --- the theorem draw ------------------------------------------------------


def test_a_theorem_seen_yesterday_cannot_come_up_today():
    """The rule the whole draw is built around."""
    assert scoring.draw_weight(1) == 0.0


def test_the_cooldown_covers_today_as_well_as_yesterday():
    assert scoring.draw_weight(0) == 0.0


def test_weight_is_measured_from_the_first_eligible_day():
    """(days - cooldown) ** exponent, so the first eligible day weighs 1 —
    not 2 ** exponent, which would make the cooldown edge a cliff."""
    assert scoring.draw_weight(2) == 1.0
    assert scoring.draw_weight(3) == 4.0
    assert scoring.draw_weight(11) == 100.0


def test_the_long_tail_dominates_at_the_default_exponent():
    """A month unseen against two days unseen, which is the ratio that keeps
    a large deck from collapsing onto its most recent few cards."""
    assert scoring.draw_weight(30) / scoring.draw_weight(2) == pytest.approx(841.0)


def test_a_zero_exponent_is_a_uniform_draw_over_everything_off_cooldown():
    assert scoring.draw_weight(2, exponent=0.0) == 1.0
    assert scoring.draw_weight(300, exponent=0.0) == 1.0


def test_a_longer_cooldown_silences_more_recent_cards():
    assert scoring.draw_weight(5, cooldown=7) == 0.0
    assert scoring.draw_weight(9, cooldown=7) == 4.0


def test_a_zero_cooldown_still_refuses_a_card_drawn_today():
    """`days_since <= cooldown` rather than `< cooldown`: with the cooldown
    turned off, yesterday is fair game but the same day is not."""
    assert scoring.draw_weight(0, cooldown=0) == 0.0
    assert scoring.draw_weight(1, cooldown=0) == 1.0


def test_the_draw_is_proportional_to_the_weights():
    rng = random.Random(0)
    counts = Counter(scoring.weighted_draw([1.0, 9.0], rng) for _ in range(4000))
    assert counts[1] / 4000 == pytest.approx(0.9, abs=0.02)


def test_a_zero_weight_card_is_never_drawn_when_another_is_eligible():
    rng = random.Random(0)
    assert {scoring.weighted_draw([0.0, 1.0, 0.0], rng) for _ in range(200)} == {1}


def test_an_all_zero_deck_falls_back_to_uniform_rather_than_refusing():
    """Reachable with a one-card deck drawn yesterday. Showing the card again
    is the only honest answer; showing nothing reads as a broken feature."""
    rng = random.Random(0)
    assert scoring.weighted_draw([0.0], rng) == 0
    assert {scoring.weighted_draw([0.0, 0.0], rng) for _ in range(200)} == {0, 1}


def test_an_empty_deck_draws_nothing():
    assert scoring.weighted_draw([]) == -1


# --- streaks ---------------------------------------------------------------

TODAY = dt.date(2026, 7, 26)


def days(*offsets):
    return [TODAY - dt.timedelta(days=n) for n in offsets]


def test_an_empty_history_has_no_streak():
    assert scoring.streak([], TODAY) == 0


def test_consecutive_days_ending_today_count():
    assert scoring.streak(days(0, 1, 2), TODAY) == 3


def test_today_not_done_yet_does_not_break_the_streak():
    """The whole point of the yesterday fallback: opening the dashboard in the
    morning must not show a zero."""
    assert scoring.streak(days(1, 2, 3), TODAY) == 3


def test_a_gap_ends_the_streak():
    assert scoring.streak(days(0, 1, 3, 4), TODAY) == 2


def test_a_two_day_gap_means_no_streak_at_all():
    assert scoring.streak(days(2, 3, 4), TODAY) == 0


def test_duplicate_dates_are_counted_once():
    assert scoring.streak([*days(0, 1), *days(1)], TODAY) == 2


def test_the_streak_reads_today_from_the_argument_not_the_clock():
    """`today` is passed in because the browser owns it. Computing it from the
    server's timezone is how a streak silently broke near midnight."""
    assert scoring.streak(days(0, 1), TODAY - dt.timedelta(days=1)) == 1
