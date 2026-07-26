"""The one-time state.json -> SQLite import.

This runs once against irreplaceable data, so the two rules it encodes get
tested against a fixture that reproduces the shape of the real file:

PROVENANCE -- a history row is `measured` iff it carries a `delta` key, which
is an exact witness because the old client wrote `delta` only when it had
actually computed an elo update.

PRESERVE -- a measured row chains off whatever the previous row recorded,
*including when that number was interpolated*. Re-chaining off the seeds
instead would silently move the ratings.
"""

from __future__ import annotations

import pytest

from dashboard.db.importer import import_state, verify
from dashboard.models.common import Provenance, Track

STATE = {
    "math": {
        "rating": 1534,
        "seen": ["AIME 1993 Problem 9", "Putnam 1998 B3"],
        "current": None,
        # Two fabricated rows (no delta) followed by two measured ones.
        "history": [
            {"date": "2026-07-23", "source": "AIME 1993 Problem 9", "score": 1.0, "rating": 1512},
            {"date": "2026-07-23", "source": "Putnam 1998 B3", "score": 0.0, "rating": 1490},
            {
                "date": "2026-07-24",
                "source": "AIME 1993 Problem 9",
                "score": 0.85,
                "hints": 1,
                "rating": 1521,
                "delta": 31,
            },
            {
                "date": "2026-07-25",
                "source": "Putnam 1998 B3",
                "score": 1.0,
                "hints": 0,
                "rating": 1534,
                "delta": 13,
            },
        ],
    },
    "chess": {
        "rating": 1462,
        "current": {
            "id": "YpJ9s",
            "rating": 1500,
            "themes": ["fork"],
            "url": "https://lichess.org/training/YpJ9s",
            "date": "2026-07-25",
            "done": True,
            "score": 1.0,
        },
        "history": [
            {"date": "2026-07-24", "id": "aaaa1", "opp": 1450, "score": 1.0, "rating": 1420},
            {
                "date": "2026-07-25",
                "id": "YpJ9s",
                "opp": 1500,
                "score": 1.0,
                "rating": 1462,
                "delta": 42,
            },
        ],
    },
    "word": {
        "byDate": {
            "2026-07-24": {
                "word": "susurrus",
                "pos": "noun",
                "definition": "a whispering sound",
                "sentences": ["a", "b", "c", "d", "e"],
                "result": {
                    "distinct": True,
                    "verdict": "good",
                    "per_sentence": [{"ok": True, "note": "fine"}] * 5,
                },
            }
        }
    },
    "games": {
        # The puzzle number was stored as the string "1,861".
        "wordle": [{"date": "2026-07-25", "n": "1,861", "guesses": 4, "won": True, "grid": []}],
        "pokedoku": [{"date": "2026-07-25", "correct": 7, "score": 41, "grid": []}],
    },
    "theorems": {
        "cards": [
            {
                "name": "OGF",
                "statement": "$\\sum a_n x^n$",
                "topic": "generating functions",
                "due": "2026-07-25",
                "interval": 1,
                "ease": 2.5,
            }
        ]
    },
    "daylog": {"2026-07-25": "shipped something"},
    "papers": {
        "current": {
            "title": "A Paper",
            "link": "http://arxiv.org/abs/1",
            "summary": "s",
            "published": "2026-07-24",
            "date": "2026-07-25",
            "marked": "queued",
        },
        "queue": [{"title": "Queued", "link": "http://arxiv.org/abs/2", "added": "2026-07-24"}],
        "archive": [{"title": "Old", "link": "http://arxiv.org/abs/3", "added": "2026-07-20"}],
    },
    "health": {"weight": [{"date": "2026-07-25", "v": 172.4}], "sleep": []},
    "completion": {"2026-07-25": {"done": 8, "total": 8, "pct": 1.0}},
    "topics": {},
}

PROBLEMS = [
    {
        "source": "AIME 1993 Problem 9",
        "tier": "aime-late",
        "anchor": 1500,
        "statement": "Find $n$.",
        "answer": "196",
        "hint1": "a",
        "hint2": "b",
        "hint3": "c",
        "date": "2026-07-23",
        # Stale keys the current prompt no longer produces; must be ignored.
        "textbook": {"title": "x", "url": "https://example.com"},
        "theorems": [{"name": "y", "url": "https://example.com"}],
    },
    {
        "source": "Putnam 1998 B3",
        "tier": "putnam-early",
        "anchor": 1750,
        "statement": "Evaluate it.",
        "answer": "42",
        "hint1": "a",
        "hint2": "b",
        "hint3": "c",
        "date": "2026-07-24",
    },
]


@pytest.fixture
def imported(repo):
    report = import_state(repo, STATE, PROBLEMS)
    return repo, report


def test_the_import_reports_what_it_wrote(imported):
    _, report = imported
    assert report.counts["math_problem"] == 2
    assert report.counts["rating_event"] == 6
    assert report.counts["rating_event.measured"] == 3
    assert report.counts["rating_event.reconstructed"] == 3


def test_verification_passes_end_to_end(imported):
    repo, report = imported
    assert verify(repo, STATE, PROBLEMS, report).ok, report.failures


def test_the_current_ratings_are_unchanged(imported):
    """The whole point of the migration: the numbers must survive it."""
    repo, _ = imported
    assert repo.current_rating(Track.math) == STATE["math"]["rating"]
    assert repo.current_rating(Track.chess) == STATE["chess"]["rating"]


def test_a_row_without_a_delta_is_flagged_reconstructed(imported):
    repo, _ = imported
    events = repo.rating_history(Track.math)
    assert [e.provenance for e in events[:2]] == [Provenance.reconstructed] * 2
    assert all(e.rating_after is None for e in events[:2])


def test_a_measured_row_keeps_its_recorded_numbers(imported):
    repo, _ = imported
    events = repo.rating_history(Track.math)
    assert events[2].provenance is Provenance.measured
    assert events[2].rating_after == 1521
    assert events[2].delta == 31


def test_a_measured_row_chains_off_a_fabricated_predecessor(imported):
    """PRESERVE. The fabrication turned out to be load-bearing: re-chaining
    off the seed instead would move the imported trajectory."""
    repo, _ = imported
    third = repo.rating_history(Track.math)[2]
    assert third.rating_before == 1490  # the interpolated row before it
    assert third.rating_before != 1500  # not the seed


def test_reconstructed_rows_still_count_toward_k(imported):
    repo, _ = imported
    assert repo.event_count(Track.math) == 4


def test_the_wordle_number_is_normalised_to_an_integer(imported):
    import datetime as dt

    repo, _ = imported
    assert repo.wordle(dt.date(2026, 7, 25)).puzzle_no == 1861


def test_the_free_reference_links_stop_costing_a_hint(imported):
    """hints[0] used to be the topic/textbook links, so a stored count of 1
    meant "opened the reference material, revealed nothing"."""
    import datetime as dt

    repo, _ = imported
    assert repo.math_attempt(dt.date(2026, 7, 24)).hints_revealed == 0


def test_the_three_paper_lists_become_one_table(imported):
    repo, _ = imported
    assert len(repo.queue()) == 2  # the queued one plus the triaged current
    assert repo.cleared_count() == 1


def test_stale_problem_keys_are_ignored(imported):
    repo, _ = imported
    problem = repo.problem_by_source("AIME 1993 Problem 9")
    assert problem.hints == ["a", "b", "c"]
    assert not hasattr(problem, "textbook")


def test_a_chess_event_whose_puzzle_was_never_stored_is_noted_not_dropped(imported):
    """chess.current only ever held the latest puzzle. rating_event.ref is
    free text with no FK precisely so the history survives without them."""
    repo, report = imported
    assert repo.event_count(Track.chess) == 2
    assert any("no stored detail" in note for note in report.notes)


def test_verification_catches_a_rating_that_did_not_survive(repo):
    """The gate has to be able to fail, or it is not a gate."""
    report = import_state(repo, STATE, PROBLEMS)
    tampered = {**STATE, "math": {**STATE["math"], "rating": 9999}}
    result = verify(repo, tampered, PROBLEMS, report)
    assert not result.ok
    assert any("math rating" in f for f in result.failures)
