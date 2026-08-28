"""The spelling bee: the pool, the draw, the marking, and five ratings a day.

The draw gets most of the attention. It is the only part with an opinion in it
— a bee that merely avoided repeats would ask the words you already spell and
move on from the ones you do not, which is backwards.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import spelling, state
from dashboard.config import SpellingSettings
from dashboard.models import domain as d
from dashboard.models.common import Track

TODAY = dt.date(2026, 8, 5)


def cfg(**overrides) -> SpellingSettings:
    base = {
        "words_per_day": 2,
        "ladder": ["easy", "hard"],
        "anchors": {"easy": 1100, "hard": 1600},
        "relearn_days": 7,
        "cooldown_days": 30,
    }
    return SpellingSettings(**{**base, **overrides})


def pool(*entries: tuple[str, str]) -> list[spelling.PoolWord]:
    return [spelling.PoolWord(word=w, tier=t, hint=f"the word {w}") for w, t in entries]


def seen(**words: tuple[dt.date, int]) -> dict[str, spelling.Seen]:
    return {w: spelling.Seen(last_seen=d_, missed=m) for w, (d_, m) in words.items()}


# --- the pool ---------------------------------------------------------------


def test_the_shipped_pool_is_usable(settings):
    """It is hand-written, so nothing else checks it."""
    words = spelling.load_pool(settings.path("spelling"))
    assert len(words) >= 100
    assert all(w.hint for w in words), "every word needs a hint — homophones"
    assert all(w.tier in settings.spelling.anchors for w in words)
    lowered = [w.word.casefold() for w in words]
    assert len(set(lowered)) == len(lowered), "a repeated word would share one history row"
    # Every rung of the ladder must have words, or the day comes up short.
    for tier in settings.spelling.ladder:
        assert any(w.tier == tier for w in words), tier


def test_a_missing_pool_is_empty_rather_than_fatal(tmp_path):
    assert spelling.load_pool(tmp_path / "nope.json") == []


def test_unreadable_json_is_empty_rather_than_fatal(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{ not json", encoding="utf-8")
    assert spelling.load_pool(path) == []


def test_a_duplicated_word_keeps_the_first(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "liaison", "tier": "hard", "hint": "first"},
                    {"word": "LIAISON", "tier": "easy", "hint": "second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    words = spelling.load_pool(path)
    assert [(w.word, w.hint) for w in words] == [("liaison", "first")]


# --- the draw ---------------------------------------------------------------


def test_the_draw_follows_the_ladder_and_ramps(settings):
    words = spelling.draw(
        spelling.load_pool(settings.path("spelling")), {}, settings.spelling, TODAY
    )
    assert [w.tier for w in words] == settings.spelling.ladder
    # Easiest first is the point of the ladder, so the anchors must not fall.
    anchors = [w.anchor for w in words]
    assert anchors == sorted(anchors)


def test_a_word_is_never_drawn_twice_in_one_day(settings):
    """The two `medium` rungs must not both take the same word."""
    words = spelling.draw(
        spelling.load_pool(settings.path("spelling")), {}, settings.spelling, TODAY
    )
    assert len({w.word for w in words}) == len(words)


def test_never_seen_words_go_first(settings):
    drawn = spelling.draw(
        pool(("alpha", "easy"), ("beta", "easy")), {}, cfg(ladder=["easy"], words_per_day=1), TODAY
    )
    assert [w.word for w in drawn] == ["alpha"]


def test_a_missed_word_comes_back_once_it_is_due(settings):
    """The whole point: stop misspelling the words you misspell."""
    words = pool(("alpha", "easy"), ("beta", "easy"))
    history = seen(
        alpha=(TODAY - dt.timedelta(days=10), 1),
        beta=(TODAY - dt.timedelta(days=10), 0),
    )
    drawn = spelling.draw(words, history, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert [w.word for w in drawn] == ["alpha"]


def test_a_missed_word_waits_out_its_relearn_days(settings):
    """Asked again tomorrow it would be a memory test, not a spelling one."""
    words = pool(("alpha", "easy"), ("beta", "easy"))
    history = seen(alpha=(TODAY - dt.timedelta(days=2), 1))
    drawn = spelling.draw(words, history, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert [w.word for w in drawn] == ["beta"]  # never seen beats a too-recent miss


def test_the_most_missed_word_wins(settings):
    words = pool(("alpha", "easy"), ("beta", "easy"))
    history = seen(
        alpha=(TODAY - dt.timedelta(days=10), 1),
        beta=(TODAY - dt.timedelta(days=10), 3),
    )
    drawn = spelling.draw(words, history, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert [w.word for w in drawn] == ["beta"]


def test_a_correct_word_waits_out_its_cooldown(settings):
    words = pool(("alpha", "easy"), ("beta", "easy"))
    history = seen(
        alpha=(TODAY - dt.timedelta(days=40), 0),
        beta=(TODAY - dt.timedelta(days=5), 0),
    )
    drawn = spelling.draw(words, history, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert [w.word for w in drawn] == ["alpha"]


def test_an_exhausted_tier_repeats_rather_than_coming_up_short(settings):
    """ "Repeats rare, but possible." Asking nothing would be worse than asking
    something twice, and this only fires when every word in a tier is inside
    its cooldown."""
    words = pool(("alpha", "easy"), ("beta", "easy"))
    history = seen(
        alpha=(TODAY - dt.timedelta(days=3), 0),
        beta=(TODAY - dt.timedelta(days=1), 0),
    )
    drawn = spelling.draw(words, history, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert [w.word for w in drawn] == ["alpha"]  # the least recent of the two


# --- one repeat a round -----------------------------------------------------


def test_only_one_rung_asks_a_word_you_have_had(settings):
    """Uncapped, every rung with a word due for relearning took it, and a
    morning could be five words you had already seen."""
    words = pool(
        ("alpha", "easy"), ("alef", "easy"),
        ("beta", "hard"), ("bet", "hard"),
    )  # fmt: skip
    old = TODAY - dt.timedelta(days=10)
    history = seen(alpha=(old, 1), beta=(old, 1))

    drawn = spelling.draw(words, history, cfg(), TODAY)
    assert [w.word for w in drawn] == ["alpha", "bet"]


def test_the_allowance_goes_to_the_word_you_have_missed_most(settings):
    """Not to whichever tier the ladder lists first: the one repeat you get
    should be the one worth having."""
    words = pool(
        ("alpha", "easy"), ("alef", "easy"),
        ("beta", "hard"), ("bet", "hard"),
    )  # fmt: skip
    old = TODAY - dt.timedelta(days=10)
    history = seen(alpha=(old, 1), beta=(old, 3))

    drawn = spelling.draw(words, history, cfg(), TODAY)
    assert [w.word for w in drawn] == ["alef", "beta"]


def test_two_rungs_of_a_tier_do_not_both_spend_it(settings):
    """The real ladder has two `medium` rungs. They make the same claim, and
    only the first can win it."""
    words = pool(("alpha", "easy"), ("alef", "easy"), ("aleph", "easy"))
    history = seen(alpha=(TODAY - dt.timedelta(days=10), 2))

    drawn = spelling.draw(words, history, cfg(ladder=["easy", "easy"], words_per_day=2), TODAY)
    assert [w.word for w in drawn] == ["alpha", "alef"]


def test_the_cap_yields_when_there_is_nothing_new_to_ask(settings):
    """Best effort, as the config says. A rung over the allowance with no
    unseen word repeats anyway — and repeats a miss, since if the day is going
    to repeat it should repeat something you got wrong."""
    words = pool(("alpha", "easy"), ("beta", "hard"), ("bet", "hard"))
    old = TODAY - dt.timedelta(days=10)
    history = seen(alpha=(old, 3), beta=(old, 1), bet=(old, 0))

    drawn = spelling.draw(words, history, cfg(), TODAY)
    assert [w.word for w in drawn] == ["alpha", "beta"]


def test_the_allowance_can_be_turned_off_entirely(settings):
    """`max_repeats = 0`: never ask a word you have had while a new one
    exists, whatever its miss count."""
    words = pool(("alpha", "easy"), ("alef", "easy"))
    history = seen(alpha=(TODAY - dt.timedelta(days=10), 3))

    drawn = spelling.draw(
        words, history, cfg(ladder=["easy"], words_per_day=1, max_repeats=0), TODAY
    )
    assert [w.word for w in drawn] == ["alef"]


def test_the_real_config_allows_one_repeat(settings):
    assert settings.spelling.max_repeats == 1


def test_a_tier_with_no_words_drops_its_rung(settings):
    """Widening to another tier would quietly change what the rung is worth."""
    drawn = spelling.draw(pool(("alpha", "easy")), {}, cfg(), TODAY)
    assert [w.tier for w in drawn] == ["easy"]


def test_the_draw_is_deterministic(settings):
    words = spelling.load_pool(settings.path("spelling"))
    first = spelling.draw(words, {}, settings.spelling, TODAY)
    second = spelling.draw(words, {}, settings.spelling, TODAY)
    assert [w.word for w in first] == [w.word for w in second]


def test_the_anchor_is_resolved_from_the_tier(settings):
    drawn = spelling.draw(pool(("alpha", "easy")), {}, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert drawn[0].anchor == 1100


# --- marking ----------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,word,expected",
    [
        ("liaison", "liaison", True),
        ("  Liaison  ", "liaison", True),
        ("LIAISON", "liaison", True),
        ("liason", "liaison", False),
        ("", "liaison", False),
        ("   ", "liaison", False),
        # Internal punctuation is part of the spelling, not noise.
        ("hors d'oeuvre", "hors d'oeuvre", True),
        ("hors doeuvre", "hors d'oeuvre", False),
        ("billet doux", "billet-doux", False),
    ],
)
def test_marking(answer, word, expected):
    assert spelling.is_correct(answer, word) is expected


def test_a_short_answer_list_marks_the_rest_wrong(settings):
    words = spelling.draw(pool(("alpha", "easy"), ("beta", "hard")), {}, cfg(), TODAY)
    assert spelling.mark(words, ["alpha"]) == [True, False]


def test_extra_answers_are_ignored(settings):
    words = spelling.draw(pool(("alpha", "easy")), {}, cfg(ladder=["easy"], words_per_day=1), TODAY)
    assert spelling.mark(words, ["alpha", "beta", "gamma"]) == [True]


# --- config validation ------------------------------------------------------


def test_a_ladder_naming_an_unknown_tier_is_a_startup_error():
    with pytest.raises(ValueError, match="no entry in"):
        cfg(ladder=["easy", "impossible"], words_per_day=2)


def test_the_ladder_length_must_match_words_per_day():
    """Two ways to say how many words a day, and they have to agree."""
    with pytest.raises(ValueError, match="words_per_day"):
        cfg(words_per_day=5)


def test_the_real_config_asks_for_five_words(settings):
    assert settings.spelling.words_per_day == 5
    assert len(settings.spelling.ladder) == 5


# --- rating -----------------------------------------------------------------


def words_for(repo, *specs: tuple[str, str, int]) -> list[d.SpellingWord]:
    return [d.SpellingWord(word=w, tier=t, hint="a hint", anchor=a) for w, t, a in specs]


def test_one_rating_event_per_word(repo):
    words = words_for(repo, ("alpha", "easy", 1100), ("beta", "hard", 1600))
    repo.open_spelling_round(TODAY, words)
    _, events = repo.finish_spelling(TODAY, ["alpha", "wrong"], [True, False])

    assert [e.ref for e in events] == ["alpha", "beta"]
    assert [e.opponent for e in events] == [1100, 1600]
    assert [e.score for e in events] == [1.0, 0.0]
    assert all(e.track is Track.spelling for e in events)


def test_each_word_chains_off_the_previous_one(repo):
    words = words_for(repo, ("alpha", "easy", 1100), ("beta", "easy", 1100))
    repo.open_spelling_round(TODAY, words)
    _, events = repo.finish_spelling(TODAY, ["alpha", "beta"], [True, True])
    assert events[1].rating_before == events[0].rating_after


def test_a_hard_word_is_worth_more_than_an_easy_one(repo):
    """The reason the ladder ramps: one event per word against its own anchor,
    not one event against a mean."""
    easy = words_for(repo, ("alpha", "easy", 1100))
    repo.open_spelling_round(TODAY, easy)
    _, easy_events = repo.finish_spelling(TODAY, ["alpha"], [True])
    easy_gain = easy_events[0].rating_after - easy_events[0].rating_before

    hard = words_for(repo, ("beta", "impossible", 1750))
    repo.open_spelling_round(TODAY + dt.timedelta(days=1), hard)
    _, hard_events = repo.finish_spelling(TODAY + dt.timedelta(days=1), ["beta"], [True])
    hard_gain = hard_events[0].rating_after - hard_events[0].rating_before

    assert hard_gain > easy_gain


def test_the_round_records_what_was_asked_and_missed(repo):
    words = words_for(repo, ("alpha", "easy", 1100), ("beta", "hard", 1600))
    repo.open_spelling_round(TODAY, words)
    repo.finish_spelling(TODAY, ["alpha", "wrong"], [True, False])

    history = repo.spelling_seen()
    assert history["alpha"][1] == 0
    assert history["beta"][1] == 1


def test_missing_the_same_word_twice_accumulates(repo):
    """Nothing displays this any more — the "keeps catching you" line is gone.
    It still has to be counted: it is what decides which word gets the day's
    one relearn slot."""
    for offset in range(2):
        day = TODAY + dt.timedelta(days=offset)
        repo.open_spelling_round(day, words_for(repo, ("beta", "hard", 1600)))
        repo.finish_spelling(day, ["wrong"], [False])
    assert repo.spelling_seen()["beta"][1] == 2


def test_reopening_a_round_returns_the_same_words(repo):
    """A reload must not hand you a fresh set after you have heard the first."""
    first = repo.open_spelling_round(TODAY, words_for(repo, ("alpha", "easy", 1100)))
    second = repo.open_spelling_round(TODAY, words_for(repo, ("beta", "hard", 1600)))
    assert [w.word for w in second.words] == [w.word for w in first.words]


def test_a_double_submit_is_refused(repo):
    repo.open_spelling_round(TODAY, words_for(repo, ("alpha", "easy", 1100)))
    repo.finish_spelling(TODAY, ["alpha"], [True])
    with pytest.raises(ValueError, match="already done"):
        repo.finish_spelling(TODAY, ["alpha"], [True])


def test_submitting_with_no_round_raises(repo):
    with pytest.raises(LookupError, match="no spelling round"):
        repo.finish_spelling(TODAY, ["alpha"], [True])


# --- the panel --------------------------------------------------------------


def test_the_panel_recomputes_marks_from_the_stored_answers(settings, repo):
    repo.open_spelling_round(
        TODAY, words_for(repo, ("alpha", "easy", 1100), ("beta", "hard", 1600))
    )
    repo.finish_spelling(TODAY, ["ALPHA", "wrong"], [True, False])

    panel = state.spelling_panel(settings, repo, TODAY)
    assert panel.marks == [True, False]
    assert panel.correct == 1
    assert panel.status == "done"
    assert panel.delta is not None


def test_an_open_round_reports_no_marks(settings, repo):
    repo.open_spelling_round(TODAY, words_for(repo, ("alpha", "easy", 1100)))
    panel = state.spelling_panel(settings, repo, TODAY)
    assert panel.marks == []
    assert panel.correct is None
    assert panel.status == "open"


def test_no_round_is_an_empty_panel_not_an_error(settings, repo):
    panel = state.spelling_panel(settings, repo, TODAY)
    assert panel.words == []
    assert panel.enabled is True
    assert panel.summary.rating == 1300  # SPELLING_SEED
