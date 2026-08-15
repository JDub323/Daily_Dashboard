"""The trick of the day: the marking, the callback, and where the rating lands.

Three things here are worth more attention than the rest.

The **marking** is a string comparison rather than a solver, so its tests are
about the gap between "the same answer" and "the same string" — and about the
gap staying closed only where it should. A test that made `n/2` equal
`\\frac{n}{2}` would be asking for a computer algebra system.

The **callback** is the whole reinforcement mechanism, and it is the one part
with an opinion in it.

And the **rating** must land in the `tricks` bucket and nowhere else. Eight
problems a day into the pooled math rating would quietly stop the daily
problem from moving it, which is the failure mode migration 9 spelled out for
theorem recalls and this repeats at four times the volume.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import batch, content, state, tricks
from dashboard.config import TrickSettings
from dashboard.db.repository import TRICK_AREA
from dashboard.models import domain as d
from dashboard.models.common import Track

TODAY = dt.date(2026, 8, 14)


def problem(band: str = "easy", answer: str = "42", **kw) -> d.TrickProblem:
    return d.TrickProblem(
        band=band,
        prompt=kw.pop("prompt", f"a {band} problem"),
        answer=answer,
        solution=kw.pop("solution", "apply the trick"),
        **kw,
    )


def a_set(easy: int = 2, medium: int = 1, *, callback: bool = False) -> list[d.TrickProblem]:
    out = [problem("easy", str(i), prompt=f"easy {i}") for i in range(easy)]
    out += [problem("medium", str(100 + i), prompt=f"medium {i}") for i in range(medium)]
    if callback:
        out.append(problem("callback", "7", prompt="the callback"))
    out.append(problem(tricks.CHALLENGE, "9", prompt="the hard one"))
    return out


def a_trick(name: str = "Roots of unity filter", added: dt.date = TODAY) -> d.Trick:
    return d.Trick(
        name=name,
        statement=r"$\frac{1}{n}\sum_{j} f(\omega^j)$",
        idea="use it when only every kth coefficient matters",
        topic="generating functions",
        added=added,
    )


# --- what counts as the same answer -----------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "42",
        " 42 ",
        "$42$",
        "$$42$$",
        "42.",
        "42.0",
        "4 2".replace(" ", ""),  # spelled out: whitespace is removed, not tolerated
    ],
)
def test_the_packaging_of_an_answer_is_not_part_of_it(typed):
    assert tricks.is_correct(typed, problem(answer="42"))


def test_a_decimal_and_its_other_spellings_are_one_answer():
    """`0.5`, `.5` and `0.50` are not three answers, and no `accept` list should
    have to say so."""
    half = problem(answer="0.5")
    assert tricks.is_correct(".5", half)
    assert tricks.is_correct("0.50", half)
    assert not tricks.is_correct("0.6", half)


def test_a_negative_written_with_a_unicode_minus_is_the_same_negative():
    """Copied out of a rendered formula rather than typed. Marking it wrong
    would be marking the renderer."""
    assert tricks.is_correct("−3", problem(answer="-3"))


def test_case_and_latex_spacing_do_not_decide_a_mark():
    assert tricks.is_correct(r"$N \cdot \left(n+1\right)$", problem(answer=r"$n\cdot(n+1)$"))


def test_an_alternative_spelling_has_to_be_offered():
    """The deliberate limit. `n/2` and `\\frac{n}{2}` are the same answer and
    different strings, and reconciling them here would make this a computer
    algebra system with no tests — so the generator lists them instead."""
    bare = problem(answer=r"$\frac{n}{2}$")
    assert not tricks.is_correct("n/2", bare)
    assert tricks.is_correct("n/2", problem(answer=r"$\frac{n}{2}$", accept=["n/2"]))


def test_a_blank_is_wrong_rather_than_an_error():
    assert not tricks.is_correct("", problem())
    assert not tricks.is_correct("   ", problem())


def test_things_float_would_accept_and_nobody_typed_are_not_numbers():
    """`float()` takes `inf`, `nan` and `1e999` — and the first and last of
    those both evaluate to infinity, so a numeric compare would call two
    different answers equal. The literal check is what stops that."""
    assert not tricks.is_correct("inf", problem(answer="1e999"))
    assert not tricks.is_correct("1e999", problem(answer="2e999"))


# --- marking a set ----------------------------------------------------------


def test_the_challenge_is_not_part_of_the_marked_set():
    problems = a_set(easy=2, medium=1)
    assert len(problems) == 4
    assert len(tricks.marked_problems(problems)) == 3
    assert tricks.challenge_of(problems).band == tricks.CHALLENGE


def test_a_short_answer_list_scores_the_rest_wrong():
    problems = a_set(easy=2, medium=1)
    assert tricks.mark(problems, ["0"]) == [True, False, False]


def test_extra_answers_are_ignored():
    problems = a_set(easy=1, medium=0)
    assert tricks.mark(problems, ["0", "junk", "junk"]) == [True]


# --- the shape of a set -----------------------------------------------------


def test_the_callback_replaces_a_medium_rather_than_lengthening_the_day():
    without = tricks.wanted_bands(4, 4, callback=False)
    with_ = tricks.wanted_bands(4, 4, callback=True)
    assert sum(without.values()) == sum(with_.values())
    assert with_ == {"easy": 4, "medium": 3, "callback": 1, "challenge": 1}


def test_a_misshapen_set_is_reported_band_by_band():
    wanted = tricks.wanted_bands(2, 1, callback=False)
    complaints = tricks.check_bands(a_set(easy=1, medium=1), wanted)
    assert len(complaints) == 1
    assert "2 easy" in complaints[0]


def test_a_set_of_the_right_shape_has_nothing_to_say():
    assert tricks.check_bands(a_set(2, 1), tricks.wanted_bands(2, 1, callback=False)) == []


# --- which old trick comes back ---------------------------------------------


def test_the_callback_prefers_the_trick_you_have_gone_longest_without():
    old = a_trick("old", added=TODAY - dt.timedelta(days=200))
    old.id = 1
    recent = a_trick("recent", added=TODAY - dt.timedelta(days=6))
    recent.id = 2
    picks = {
        tricks.pick_callback([old, recent], {}, TODAY, rng=__import__("random").Random(s)).name
        for s in range(40)
    }
    # Not deterministic — it is a weighted sample — but the 200-day-old one
    # should dominate a 6-day-old one by orders of magnitude.
    assert "old" in picks


def test_nothing_comes_back_while_everything_is_inside_its_cooldown():
    """A bonus, not an obligation: rather than reaching for yesterday's trick,
    the set is simply one problem shorter."""
    fresh = a_trick("yesterday", added=TODAY - dt.timedelta(days=1))
    fresh.id = 1
    assert tricks.pick_callback([fresh], {}, TODAY, cooldown=3) is None


def test_there_is_no_callback_on_the_first_day():
    assert tricks.pick_callback([], {}, TODAY) is None


def test_a_trick_called_back_yesterday_is_measured_from_then_not_from_when_it_was_added():
    old = a_trick("old", added=TODAY - dt.timedelta(days=200))
    old.id = 1
    assert tricks.pick_callback([old], {1: TODAY - dt.timedelta(days=1)}, TODAY, cooldown=3) is None


# --- anchors ----------------------------------------------------------------


def anchors(**kw) -> TrickSettings:
    return TrickSettings(anchors={"easy": 1200, "medium": 1450, "challenge": 1750}, **kw)


def test_a_callback_is_rated_as_the_medium_it_is():
    assert anchors().anchor_for("callback") == anchors().anchor_for("medium")


def test_the_set_is_rated_against_the_bands_it_actually_contained():
    cfg = anchors()
    easy_day = cfg.set_anchor(["easy", "easy", "easy", "easy"])
    mixed = cfg.set_anchor(["easy", "easy", "medium", "medium"])
    assert easy_day == 1200
    assert mixed == 1325
    assert cfg.set_anchor([]) == cfg.anchor_for("medium")


def test_an_anchor_table_missing_a_band_is_a_startup_error():
    with pytest.raises(ValueError, match="challenge"):
        TrickSettings(anchors={"easy": 1200, "medium": 1450})


def test_the_shipped_config_has_the_trick_enabled(settings):
    assert settings.tricks.enabled
    assert settings.tricks.easy + settings.tricks.medium == 8


# --- storage and rating -----------------------------------------------------


def opened(repo, *, callback: d.Trick | None = None, name: str = "Roots of unity filter"):
    stored = repo.add_trick(a_trick(name))
    return stored, repo.open_trick_round(
        TODAY, stored.id, a_set(2, 1), callback.id if callback else None
    )


def test_a_second_draw_hands_back_the_first(repo):
    """Idempotent per day, which matters more here than for the theorem draw:
    rerolling this one spends a generation."""
    stored, first = opened(repo)
    again = repo.open_trick_round(TODAY, stored.id, a_set(4, 4))
    assert [p.prompt for p in again.problems] == [p.prompt for p in first.problems]


def test_a_repeated_trick_name_is_rejected_by_the_index(repo):
    import sqlite3

    repo.add_trick(a_trick("Vieta's formulas"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_trick(a_trick("vieta's FORMULAS"))


def test_finishing_rates_the_whole_set_once(repo, settings):
    _, round_ = opened(repo)
    marks = tricks.mark(round_.problems, ["0", "1", "100"])
    closed, event = repo.finish_trick(TODAY, ["0", "1", "100"], marks, 1325)
    assert closed.correct == 3
    assert closed.status == "done"
    assert event.score == 1.0
    assert event.area == TRICK_AREA
    assert repo.event_count(Track.math, TRICK_AREA) == 1


def test_the_result_never_touches_the_pooled_math_rating(repo):
    """The point of the separate bucket. Eight problems a day into the pooled
    rating would drive its K to the floor and stop the daily problem moving it."""
    before = repo.current_rating(Track.math)
    opened(repo)
    repo.finish_trick(TODAY, ["0", "1", "100"], [True, True, True], 1325)
    assert repo.current_rating(Track.math) == before
    assert repo.event_count(Track.math) == 0
    assert repo.current_rating(Track.math, TRICK_AREA) > before


def test_the_trick_bucket_is_not_one_of_the_configured_areas(settings):
    """What keeps it out of the per-area strip and the retrospective chart,
    both of which iterate the configured areas."""
    assert TRICK_AREA not in settings.math.area_names


def test_a_second_submit_is_a_conflict_not_a_second_rating(repo):
    opened(repo)
    repo.finish_trick(TODAY, ["0"], [True, False, False], 1325)
    with pytest.raises(ValueError, match="already done"):
        repo.finish_trick(TODAY, ["0"], [True, False, False], 1325)
    assert repo.event_count(Track.math, TRICK_AREA) == 1


def test_the_challenge_rates_separately_and_only_once(repo):
    _, _ = opened(repo)
    grade = d.TrickGrade(correct=True, note="right, and by the intended route")
    closed, event = repo.record_challenge(TODAY, "because the roots cancel", grade, 1750)
    assert closed.challenge_ok is True
    assert event.score == 1.0
    assert event.opponent == 1750
    with pytest.raises(ValueError, match="already graded"):
        repo.record_challenge(TODAY, "again", grade, 1750)


def test_the_challenge_can_be_answered_without_the_set_being_marked(repo):
    """It is the one you go away and think about. Holding the other eight
    hostage to it would make it not optional."""
    _, _ = opened(repo)
    repo.record_challenge(TODAY, "an answer", d.TrickGrade(correct=False, note="no"), 1750)
    assert repo.trick_round(TODAY).status == "open"


def test_the_callback_draw_reads_the_rounds_that_used_it(repo):
    old = repo.add_trick(a_trick("old", added=TODAY - dt.timedelta(days=30)))
    opened(repo, callback=old, name="today's")
    assert repo.callback_dates() == {old.id: TODAY}


# --- the panel --------------------------------------------------------------


def test_the_answers_do_not_cross_the_wire_until_the_set_is_marked(settings, repo):
    """[Invariant] The marking is a string comparison the server performs, so
    the answers being in the page would be the answer key being in the page."""
    opened(repo)
    panel = state.tricks_panel(settings, repo, TODAY)
    assert panel.problems
    assert all(not p.answer and not p.solution and not p.accept for p in panel.problems)
    assert not panel.challenge.answer and not panel.challenge.solution
    # The statement is *not* withheld: it is what is being taught.
    assert panel.trick.statement


def test_the_answers_arrive_with_the_marking(settings, repo):
    opened(repo)
    repo.finish_trick(TODAY, ["0", "wrong", "100"], [True, False, True], 1325)
    panel = state.tricks_panel(settings, repo, TODAY)
    assert [p.answer for p in panel.problems] == ["0", "1", "100"]
    assert [p.correct for p in panel.problems] == [True, False, True]
    assert panel.problems[1].typed == "wrong"
    assert panel.correct == 2
    assert panel.delta is not None


def test_the_challenges_answer_waits_for_its_own_grading(settings, repo):
    """Marked set, ungraded challenge: the eight are revealed and the ninth is
    not, because it can still be attempted."""
    opened(repo)
    repo.finish_trick(TODAY, ["0", "1", "100"], [True, True, True], 1325)
    panel = state.tricks_panel(settings, repo, TODAY)
    assert not panel.challenge.answer
    repo.record_challenge(TODAY, "an answer", d.TrickGrade(correct=True, note=""), 1750)
    assert state.tricks_panel(settings, repo, TODAY).challenge.answer == "9"


def test_an_undrawn_day_still_reports_the_rating(settings, repo):
    panel = state.tricks_panel(settings, repo, TODAY)
    assert panel.trick is None
    assert panel.enabled
    assert panel.rating == repo.current_rating(Track.math)
    assert panel.events == 0


def test_the_archive_shows_the_answers_whatever_the_status(settings, repo):
    """A record of work already done. Withholding the answer the way the live
    panel does would mean the one thing you came for is the one thing missing."""
    opened(repo)
    archive = state.trick_archive(repo, TODAY)
    assert len(archive.entries) == 1
    assert all(p.answer for p in archive.entries[0].problems)


def test_todays_challenge_is_still_withheld_in_the_archive(repo):
    """It can still be attempted, and the archive is not a way round that."""
    opened(repo)
    assert not state.trick_archive(repo, TODAY).entries[0].challenge.answer


def test_a_past_challenge_you_never_answered_stops_being_a_secret(repo):
    """The rule that hid it can no longer be satisfied — nothing in the app
    offers a past day's challenge — so hiding it for ever would lose it."""
    opened(repo)
    later = state.trick_archive(repo, TODAY + dt.timedelta(days=1))
    assert later.entries[0].challenge.answer == "9"


def test_the_callback_row_is_labelled_with_the_trick_it_reinforces(settings, repo):
    old = repo.add_trick(a_trick("Telescoping", added=TODAY - dt.timedelta(days=30)))
    today_trick = repo.add_trick(a_trick("today's"))
    repo.open_trick_round(TODAY, today_trick.id, a_set(1, 1, callback=True), old.id)
    panel = state.tricks_panel(settings, repo, TODAY)
    assert panel.callback_of == "Telescoping"
    labelled = [p for p in panel.problems if p.callback_of]
    assert len(labelled) == 1
    assert labelled[0].band == "callback"


# --- the overnight batch ----------------------------------------------------


def a_batch(settings, date: dt.date = TODAY, **overrides) -> dict:
    base = {
        "date": date.isoformat(),
        "topic": settings.math.topic_for(date),
        "name": "Conjugate multiplication",
        "statement": r"$(a-b)(a+b) = a^2 - b^2$",
        "idea": "kill a surd in a denominator",
        "kind": "trick",
        "callback": None,
        "problems": [
            {"band": "easy", "prompt": f"easy {i}", "answer": str(i), "solution": "s"}
            for i in range(settings.tricks.easy)
        ]
        + [
            {"band": "medium", "prompt": f"medium {i}", "answer": str(100 + i), "solution": "s"}
            for i in range(settings.tricks.medium)
        ]
        + [{"band": "challenge", "prompt": "the hard one", "answer": "9", "solution": "s"}],
    }
    return {**base, **overrides}


def test_a_good_batch_validates(settings):
    parsed, errors = batch.validate_trick(settings, json.dumps(a_batch(settings)), TODAY)
    assert errors == []
    assert parsed.name == "Conjugate multiplication"


def test_a_set_generated_for_the_wrong_week_is_rejected(settings):
    """Not visibly wrong at 8am, which is exactly why it is checked at 3am."""
    _, errors = batch.validate_trick(
        settings, json.dumps(a_batch(settings, topic="knot theory")), TODAY
    )
    assert any("knot theory" in e for e in errors)


def test_a_callback_problem_with_no_named_trick_is_rejected(settings):
    """Without the name the import cannot link the problem to the card it is
    reinforcing, and the row renders as a non-sequitur."""
    payload = a_batch(settings)
    payload["problems"][-2] = {
        "band": "callback",
        "prompt": "reinforce something",
        "answer": "3",
        "solution": "s",
    }
    _, errors = batch.validate_trick(settings, json.dumps(payload), TODAY)
    assert any("does not name" in e for e in errors)


def test_a_repeated_problem_is_rejected(settings):
    payload = a_batch(settings)
    payload["problems"][1] = dict(payload["problems"][0])
    _, errors = batch.validate_trick(settings, json.dumps(payload), TODAY)
    assert any("twice" in e for e in errors)


def test_latex_in_a_batch_survives_the_json(settings):
    r"""`\frac` is a legal JSON escape, so a strict parse turns it into a
    formfeed and the maths vanishes silently. `extract_json` repairs first."""
    payload = a_batch(settings)
    payload["statement"] = "PLACEHOLDER"
    raw = json.dumps(payload).replace("PLACEHOLDER", r"$\frac{1}{2}$".replace("\\", "\\"))
    raw = raw.replace(r"$\frac", r"$\frac")
    parsed, _ = batch.validate_trick(settings, raw, TODAY)
    assert r"\frac" in parsed.statement


def test_the_context_names_the_bands_and_the_callback(settings, repo):
    old = repo.add_trick(a_trick("Telescoping", added=TODAY - dt.timedelta(days=90)))
    ctx = batch.trick_context(settings, repo, TODAY)
    assert ctx["enabled"] is True
    assert ctx["topic"] == settings.math.topic_for(TODAY)
    assert ctx["bands"]["callback"] == 1
    assert ctx["callback"]["name"] == old.name
    # The instructions come back from here rather than living in the scheduled
    # task's prompt, which is the whole reason the CLI exists.
    assert "callback" in ctx["instructions"].casefold()
    assert ctx["rules"] and ctx["schema"]


def test_the_context_degrades_without_a_database(settings):
    ctx = batch.trick_context(settings, None, TODAY)
    assert ctx["callback"] is None
    assert ctx["bands"]["callback"] == 0


def test_the_context_says_so_when_the_feature_is_off(settings):
    off = settings.model_copy(update={"tricks": TrickSettings()})
    assert batch.trick_context(off, None, TODAY) == {
        "date": TODAY.isoformat(),
        "enabled": False,
    }


# --- serving from the batch -------------------------------------------------


def test_a_batched_set_is_served_without_an_llm_call(settings, repo):
    """No API key exists in the tests, so anything that reached `llm` would
    raise rather than pass."""
    settings.path("batches").mkdir(parents=True, exist_ok=True)
    content.trick_batch_path(settings, TODAY).write_text(
        json.dumps(a_batch(settings)), encoding="utf-8"
    )
    round_ = content.next_trick(settings, repo, TODAY)
    assert repo.trick(round_.trick_id).name == "Conjugate multiplication"
    assert len(round_.problems) == settings.tricks.easy + settings.tricks.medium + 1


def test_the_batchs_own_callback_wins_over_a_fresh_pick(settings, repo):
    """Its problem was written against that trick; re-picking here would label
    it with a different one."""
    old = repo.add_trick(a_trick("Telescoping", added=TODAY - dt.timedelta(days=90)))
    repo.add_trick(a_trick("Vieta's formulas", added=TODAY - dt.timedelta(days=400)))
    payload = a_batch(settings, callback="Telescoping")
    payload["problems"][-2]["band"] = "callback"
    settings.path("batches").mkdir(parents=True, exist_ok=True)
    content.trick_batch_path(settings, TODAY).write_text(json.dumps(payload), encoding="utf-8")
    round_ = content.next_trick(settings, repo, TODAY)
    assert round_.callback_id == old.id


def test_an_unusable_batch_does_not_take_the_panel_down(settings, repo, monkeypatch):
    """A batch is an optimisation, and an optimisation that can take the panel
    down is not one — it falls through to a generation."""
    settings.path("batches").mkdir(parents=True, exist_ok=True)
    content.trick_batch_path(settings, TODAY).write_text("{not json", encoding="utf-8")

    called = {}

    def fake(settings_, date, wanted, seen, callback):
        called["wanted"] = wanted
        return a_trick("Generated"), a_set(2, 1)

    monkeypatch.setattr(content, "generate_trick", fake)
    round_ = content.next_trick(settings, repo, TODAY)
    assert repo.trick(round_.trick_id).name == "Generated"
    assert called["wanted"]["challenge"] == 1


def test_a_generated_repeat_is_a_loud_error_rather_than_a_silent_one(
    settings, repo, monkeypatch
):
    repo.add_trick(a_trick("Generated"))
    monkeypatch.setattr(
        content, "generate_trick", lambda *a, **k: (a_trick("Generated"), a_set(2, 1))
    )
    with pytest.raises(content.ContentError, match="already had"):
        content.next_trick(settings, repo, TODAY)


# --- the free path: a set written in the Claude app -------------------------


def test_the_prompt_is_self_contained(settings, repo):
    """What is copied is the whole of what the model will see, so anything left
    implicit is something that only works when you remember to say it."""
    repo.add_trick(a_trick("Telescoping", added=TODAY - dt.timedelta(days=90)))
    prompt = content.trick_prompt(settings, repo, TODAY)
    assert settings.math.topic_for(TODAY) in prompt
    assert "Telescoping" in prompt  # the callback it must write a problem on
    assert '"problems"' in prompt  # the output schema
    assert "band" in prompt and "accept" in prompt
    assert TODAY.isoformat() in prompt


def test_the_prompt_names_the_same_callback_twice_running(settings, repo):
    """Asked once to build the prompt and again when the paste arrives. A fresh
    sample each time would name a different trick on every reload."""
    for i in range(6):
        repo.add_trick(a_trick(f"trick {i}", added=TODAY - dt.timedelta(days=30 + i * 20)))
    first = content.today_callback(settings, repo, TODAY)
    assert first is not None
    assert all(content.today_callback(settings, repo, TODAY).id == first.id for _ in range(5))


def test_a_pasted_set_costs_nothing_and_lands(settings, repo):
    """No API key exists in the tests, so anything reaching `llm` would raise."""
    round_ = content.trick_from_paste(settings, repo, TODAY, json.dumps(a_batch(settings)))
    assert repo.trick(round_.trick_id).name == "Conjugate multiplication"
    assert len(round_.problems) == settings.tricks.easy + settings.tricks.medium + 1


def test_a_pasted_set_survives_the_chat_wrapping_it_in_prose(settings, repo):
    """`extract_json` takes the object out of whatever it is sitting in, which
    is what a copy out of a chat window looks like."""
    wrapped = (
        f"Sure — here's today's set:\n\n```json\n{json.dumps(a_batch(settings))}\n```\n"
        f"Hope it helps!"
    )
    round_ = content.trick_from_paste(settings, repo, TODAY, wrapped)
    assert repo.trick(round_.trick_id).name == "Conjugate multiplication"


def test_a_paste_for_another_week_is_refused(settings, repo):
    with pytest.raises(tricks.TrickError, match="week"):
        content.trick_from_paste(
            settings, repo, TODAY, json.dumps(a_batch(settings, topic="knot theory"))
        )


def test_a_paste_that_is_not_json_says_so(settings, repo):
    with pytest.raises(tricks.TrickError, match=r"not JSON|not a trick set"):
        content.trick_from_paste(settings, repo, TODAY, "I'm afraid I can't do that")


def test_a_paste_cannot_overwrite_a_set_you_have_already_started(settings, repo):
    opened(repo)
    with pytest.raises(tricks.TrickError, match="already stored"):
        content.trick_from_paste(settings, repo, TODAY, json.dumps(a_batch(settings)))


def test_a_misshapen_paste_is_accepted_where_the_cli_would_reject_it(settings, repo):
    """The two callers disagree about strictness on purpose: at 3am regenerating
    is free, and at breakfast you have already done the work of getting it."""
    payload = a_batch(settings)
    payload["problems"] = payload["problems"][2:]  # two easy problems short
    _, errors, warnings = content.parse_trick_set(settings, json.dumps(payload), TODAY)
    assert errors == []
    assert any("easy" in w for w in warnings)
    _, cli_errors = batch.validate_trick(settings, json.dumps(payload), TODAY)
    assert cli_errors
    assert content.trick_from_paste(settings, repo, TODAY, json.dumps(payload))


def test_a_paste_resolves_its_callback_by_name(settings, repo):
    old = repo.add_trick(a_trick("Telescoping", added=TODAY - dt.timedelta(days=90)))
    payload = a_batch(settings, callback="Telescoping")
    payload["problems"][-2]["band"] = "callback"
    round_ = content.trick_from_paste(settings, repo, TODAY, json.dumps(payload))
    assert round_.callback_id == old.id


def test_the_panel_carries_the_prompt_until_there_is_a_set(settings, repo):
    """Shipped with the panel rather than fetched on click, so the copy button
    is instant and what you copy is what you were looking at."""
    before = state.tricks_panel(settings, repo, TODAY)
    assert before.prompt and before.batched is False
    opened(repo)
    assert state.tricks_panel(settings, repo, TODAY).prompt is None


def test_the_panel_says_when_tonights_file_is_already_there(settings, repo):
    settings.path("batches").mkdir(parents=True, exist_ok=True)
    content.trick_batch_path(settings, TODAY).write_text(
        json.dumps(a_batch(settings)), encoding="utf-8"
    )
    assert state.tricks_panel(settings, repo, TODAY).batched is True


def test_a_set_with_nothing_to_mark_is_refused(settings):
    with pytest.raises(content.ContentError, match="no gradeable"):
        content._to_trick(
            {
                "name": "n",
                "statement": "$x$",
                "problems": [{"band": "challenge", "prompt": "only", "answer": "1"}],
            },
            settings,
            TODAY,
        )
