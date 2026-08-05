"""Phase 3: the theorem deck's machinery, pointed at a second deck.

Most of what matters here is that the two decks stay *separate* while sharing
one table — a draw, a rating or a count leaking across is the failure this
design invites, and every one of them is silent.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import state
from dashboard.db.repository import ARGUMENT_AREA
from dashboard.models import domain as d
from dashboard.models.common import Deck, Provenance, Track
from dashboard.server import _draw_card, _seed_patterns

TODAY = dt.date(2026, 8, 5)


def card(name: str, deck: Deck = Deck.argument, **overrides) -> d.TheoremCard:
    base = dict(
        name=name,
        statement=f"What {name} claims, at enough length to be a real card.",
        topic="incentives",
        added=TODAY,
        deck=deck,
    )
    return d.TheoremCard(**{**base, **overrides})


def grade(explained: bool = True, applied: bool = True) -> d.TheoremGrade:
    return d.TheoremGrade(explained=explained, applied=applied, note="a note")


# --- the two decks stay apart ----------------------------------------------


def test_cards_are_listed_per_deck(repo):
    repo.add_card(card("Tragedy of the commons"))
    repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    assert [c.name for c in repo.cards(Deck.argument)] == ["Tragedy of the commons"]
    assert [c.name for c in repo.cards(Deck.math)] == ["Cauchy-Schwarz"]
    assert repo.deck_size(Deck.argument) == 1
    assert repo.deck_size(Deck.math) == 1


def test_both_decks_can_draw_on_the_same_day(repo):
    """The reason migration 12 widened the primary key. With the old (date) key
    the second deck's insert would lose silently to the first and the argument
    card would simply never appear."""
    math = repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    argument = repo.add_card(card("Motte and bailey"))

    repo.record_draw(TODAY, math.id, Deck.math)
    repo.record_draw(TODAY, argument.id, Deck.argument)

    assert repo.draw(TODAY, Deck.math).card_id == math.id
    assert repo.draw(TODAY, Deck.argument).card_id == argument.id


def test_a_second_draw_on_the_same_day_and_deck_keeps_the_first(repo):
    first = repo.add_card(card("First"))
    second = repo.add_card(card("Second"))
    repo.record_draw(TODAY, first.id, Deck.argument)
    assert repo.record_draw(TODAY, second.id, Deck.argument).card_id == first.id


def test_recency_and_counts_do_not_leak_across_decks(repo):
    math = repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    argument = repo.add_card(card("Base-rate neglect"))
    repo.record_draw(TODAY, math.id, Deck.math)
    repo.record_draw(TODAY, argument.id, Deck.argument)

    assert list(repo.last_drawn(Deck.math)) == [math.id]
    assert list(repo.last_drawn(Deck.argument)) == [argument.id]
    assert list(repo.recall_counts(Deck.math)) == [math.id]
    assert list(repo.recall_counts(Deck.argument)) == [argument.id]


def test_the_draw_only_considers_its_own_deck(settings, repo):
    """A math-only deck must not put a theorem in front of the argument
    panel."""
    repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    _draw_card(settings, repo, TODAY, Deck.argument)
    assert repo.draw(TODAY, Deck.argument) is None

    repo.add_card(card("Chesterton's fence"))
    _draw_card(settings, repo, TODAY, Deck.argument)
    assert repo.card(repo.draw(TODAY, Deck.argument).card_id).name == "Chesterton's fence"


def test_an_archived_card_is_not_drawn(settings, repo):
    retired = repo.add_card(card("Retired"))
    repo.set_archived(retired.id, True)
    _draw_card(settings, repo, TODAY, Deck.argument)
    assert repo.draw(TODAY, Deck.argument) is None


def test_never_seen_cards_drain_in_the_order_added(settings, repo):
    for name in ("First", "Second", "Third"):
        repo.add_card(card(name))
    for offset, expected in enumerate(("First", "Second", "Third")):
        day = TODAY + dt.timedelta(days=offset)
        _draw_card(settings, repo, day, Deck.argument)
        assert repo.card(repo.draw(day, Deck.argument).card_id).name == expected


# --- rating -----------------------------------------------------------------


def test_an_argument_recall_rates_on_the_debate_track(repo):
    """Not the math track. A pattern recall moving a math area rating is the
    single most damaging way the shared table could go wrong."""
    drawn = repo.add_card(card("Principal-agent problem"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)
    _, event = repo.record_recall(
        TODAY,
        explanation="x",
        application="y",
        grade=grade(),
        area=ARGUMENT_AREA,
        deck=Deck.argument,
    )
    assert event.track is Track.debate
    assert event.area == ARGUMENT_AREA
    assert event.provenance is Provenance.theorem


def test_a_theorem_recall_still_rates_on_the_math_track(repo):
    drawn = repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    repo.record_draw(TODAY, drawn.id, Deck.math)
    _, event = repo.record_recall(
        TODAY, explanation="x", application="y", grade=grade(), area="analysis"
    )
    assert event.track is Track.math
    assert event.area == "analysis"


def test_both_halves_right_is_plus_three_and_neither_is_minus_three(repo):
    for offset, (g, expected) in enumerate(
        [(grade(True, True), 3), (grade(False, False), -3), (grade(True, False), 0)]
    ):
        drawn = repo.add_card(card(f"Pattern {offset}"))
        day = TODAY + dt.timedelta(days=offset)
        repo.record_draw(day, drawn.id, Deck.argument)
        _, event = repo.record_recall(
            day,
            explanation="x",
            application="y",
            grade=g,
            area=ARGUMENT_AREA,
            deck=Deck.argument,
        )
        assert event.rating_after - event.rating_before == expected


def test_the_pattern_bucket_is_not_one_of_the_issues(settings):
    """It is read in one place. Being outside the issue list is what keeps it
    out of the panel strip and the trends chart, both of which iterate
    `issue_slugs`."""
    assert ARGUMENT_AREA not in settings.debate.issue_slugs


def test_pattern_recalls_do_not_move_the_pooled_debate_rating(repo):
    """The theorem deck's rule, for its reason: the opponent is your own
    rating, so nothing pulls a run of good recalls back, and applying that walk
    to the headline figure would corrupt what it means."""
    before = repo.current_rating(Track.debate)
    drawn = repo.add_card(card("Selection effect"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)
    repo.record_recall(
        TODAY,
        explanation="x",
        application="y",
        grade=grade(),
        area=ARGUMENT_AREA,
        deck=Deck.argument,
    )
    assert repo.current_rating(Track.debate) == before
    assert repo.current_rating(Track.debate, ARGUMENT_AREA) == before + 3


def test_a_recall_does_not_make_the_debate_rating_more_confident(repo):
    """`theorem` is in NOT_ATTEMPTS, so it stays out of `n` — one card a day
    would otherwise drive K to its floor and stop debates moving the rating."""
    drawn = repo.add_card(card("Regulatory capture"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)
    repo.record_recall(
        TODAY,
        explanation="x",
        application="y",
        grade=grade(),
        area=ARGUMENT_AREA,
        deck=Deck.argument,
    )
    assert repo.event_count(Track.debate) == 0


def test_a_double_submit_is_refused(repo):
    drawn = repo.add_card(card("Baptists and bootleggers"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)
    kwargs = dict(
        explanation="x", application="y", grade=grade(), area=ARGUMENT_AREA, deck=Deck.argument
    )
    repo.record_recall(TODAY, **kwargs)
    with pytest.raises(ValueError, match="already graded"):
        repo.record_recall(TODAY, **kwargs)


def test_grading_with_nothing_drawn_raises(repo):
    with pytest.raises(LookupError, match="no card drawn"):
        repo.record_recall(
            TODAY,
            explanation="x",
            application="y",
            grade=grade(),
            area=ARGUMENT_AREA,
            deck=Deck.argument,
        )


# --- seeding ----------------------------------------------------------------


def test_the_starter_deck_seeds_once(settings, repo):
    _seed_patterns(settings, repo, TODAY)
    first = repo.deck_size(Deck.argument)
    assert first == len(settings.debate.patterns)

    _seed_patterns(settings, repo, TODAY)
    assert repo.deck_size(Deck.argument) == first


def test_seeding_does_not_rewrite_a_card_you_have_been_graded_on(settings, repo):
    """Editing a statement in config.toml must not change the card the archive
    says you recalled — you would be shown recalling something you were never
    asked."""
    _seed_patterns(settings, repo, TODAY)
    name = settings.debate.patterns[0].name
    original = repo.card_named(name, Deck.argument)

    edited = settings.model_copy(
        update={
            "debate": settings.debate.model_copy(
                update={
                    "patterns": [
                        settings.debate.patterns[0].model_copy(
                            update={"statement": "Something else entirely, at length."}
                        )
                    ]
                }
            )
        }
    )
    _seed_patterns(edited, repo, TODAY)
    assert repo.card_named(name, Deck.argument).statement == original.statement


def test_seeding_leaves_a_retired_card_retired(settings, repo):
    _seed_patterns(settings, repo, TODAY)
    name = settings.debate.patterns[0].name
    repo.set_archived(repo.card_named(name, Deck.argument).id, True)
    _seed_patterns(settings, repo, TODAY)
    assert repo.card_named(name, Deck.argument).archived is True


def test_seeding_never_touches_the_math_deck(settings, repo):
    repo.add_card(card("Cauchy-Schwarz", deck=Deck.math))
    _seed_patterns(settings, repo, TODAY)
    assert repo.deck_size(Deck.math) == 1


def test_card_named_matches_case_insensitively(repo):
    repo.add_card(card("Motte and bailey"))
    assert repo.card_named("MOTTE AND BAILEY", Deck.argument) is not None
    assert repo.card_named("Motte and bailey", Deck.math) is None


def test_the_shipped_starter_deck_is_usable(settings):
    """A card too short to be a statement is a card that teaches nothing, and
    the deck is hand-written so nothing else checks it."""
    patterns = settings.debate.patterns
    assert len(patterns) >= 10
    assert all(len(p.statement) >= 60 for p in patterns)
    assert len({p.name for p in patterns}) == len(patterns)


# --- the panel --------------------------------------------------------------


def test_the_statement_is_withheld_until_the_recall_is_graded(repo):
    """[Invariant] The same rule the theorem panel follows: the answer must not
    be in the page while you are being asked for it — in `today` *or* in the
    deck list, which ships every card in full."""
    drawn = repo.add_card(card("Chesterton's fence"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)

    panel = state.patterns_panel(repo, TODAY)
    assert panel.today.statement == ""
    assert [e.card.statement for e in panel.deck if e.card.id == drawn.id] == [""]

    repo.record_recall(
        TODAY,
        explanation="x",
        application="y",
        grade=grade(),
        area=ARGUMENT_AREA,
        deck=Deck.argument,
    )
    assert state.patterns_panel(repo, TODAY).today.statement != ""


def test_the_panel_reports_the_pattern_bucket_not_the_pooled_rating(repo):
    drawn = repo.add_card(card("Second-best"))
    repo.record_draw(TODAY, drawn.id, Deck.argument)
    repo.record_recall(
        TODAY,
        explanation="x",
        application="y",
        grade=grade(),
        area=ARGUMENT_AREA,
        deck=Deck.argument,
    )
    panel = state.patterns_panel(repo, TODAY)
    assert panel.rating == repo.current_rating(Track.debate, ARGUMENT_AREA)
    assert panel.events == 1
    assert panel.delta == 3


def test_the_panel_suggests_ballot_patterns_not_already_in_the_deck(repo):
    repo.add_card(card("Motte and bailey"))
    repo.open_debate(
        d.DebateSession(
            date=TODAY,
            issue="immigration",
            subarea="a1",
            angle="values",
            motion="US policy on a1",
            format="socratic",
            side="pro",
            anchor=1500,
        )
    )
    ballot = d.Scorecard(
        items=[d.RubricItem(key="framing", score=1, quote="a long enough quotation")],
        patterns=["motte and bailey", "Base-rate neglect"],
    )
    repo.score_debate(TODAY, ballot, 0.5)

    # Already held, case-insensitively, so only the new one is offered.
    assert state.patterns_panel(repo, TODAY).suggested == ["Base-rate neglect"]


def test_no_ballot_means_nothing_suggested(repo):
    assert state.patterns_panel(repo, TODAY).suggested == []
