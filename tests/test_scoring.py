"""Elo, hint penalties, SM-2 and streaks.

These are the functions the plan singled out: the most defect-prone code in
the app, and until now the only way to verify the elo maths was to actually
solve a chess puzzle.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import scoring
from dashboard.models.common import ChessOutcome, MathOutcome, ReviewQuality

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


# --- SM-2 ------------------------------------------------------------------


def test_blank_resets_the_interval_but_keeps_the_card():
    interval, ease = scoring.sm2_review(ReviewQuality.blank, 30, 2.5)
    assert interval == 1
    assert ease == pytest.approx(2.2)


def test_hard_grows_the_interval_slowly_and_lowers_ease():
    interval, ease = scoring.sm2_review(ReviewQuality.hard, 10, 2.5)
    assert ease == pytest.approx(2.35)
    assert interval == 12  # 10 * 1.2, not 10 * ease


def test_good_leaves_ease_alone_and_multiplies_by_it():
    interval, ease = scoring.sm2_review(ReviewQuality.good, 10, 2.5)
    assert ease == pytest.approx(2.5)
    assert interval == 25


def test_easy_raises_ease_and_the_new_ease_drives_the_interval():
    """The ease is updated first — 10 * 2.65, not 10 * 2.5."""
    interval, ease = scoring.sm2_review(ReviewQuality.easy, 10, 2.5)
    assert ease == pytest.approx(2.65)
    assert interval == 27


def test_ease_is_clamped_at_both_ends():
    """The old client clamped only the floor, so a card answered `easy`
    repeatedly drifted past 3.0 — which the theorem_card CHECK now rejects."""
    ease = 2.5
    for _ in range(20):
        _, ease = scoring.sm2_review(ReviewQuality.easy, 1, ease)
    assert ease == scoring.EASE_CEILING

    ease = 2.5
    for _ in range(20):
        _, ease = scoring.sm2_review(ReviewQuality.blank, 1, ease)
    assert ease == scoring.EASE_FLOOR


def test_the_interval_never_falls_below_one_day():
    interval, _ = scoring.sm2_review(ReviewQuality.good, 1, 1.3)
    assert interval >= 1


def test_a_zero_interval_is_treated_as_one():
    interval, _ = scoring.sm2_review(ReviewQuality.good, 0, 2.5)
    assert interval == 3  # max(1, 0) * 2.5


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
