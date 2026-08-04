"""The debate: the rotation, the ballot parser, the scoring, and the dossier.

The parser gets the most attention here for the same reason `test_parsers.js`
does: it reads a document produced by something outside this codebase, and
every shape it fails on is a debate that already happened and cannot be graded.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import debate, scoring
from dashboard.config import DebateAngle, DebateIssue, DebateSettings, load_settings
from dashboard.models import domain as d
from dashboard.models.api import NewsItem
from dashboard.models.common import DebateSide, FactStance

SUNDAY = dt.date(2026, 8, 2)


def settings_for(**overrides) -> DebateSettings:
    base = {
        "issues": [
            DebateIssue(slug="alpha", name="Alpha", subareas=["a1", "a2"], keywords=["alpha"]),
            DebateIssue(slug="beta", name="Beta", subareas=["b1"], keywords=["beta"]),
        ],
        "angles": [
            DebateAngle(key="one", label="One", format="cross", brief="the first angle"),
            DebateAngle(key="two", label="Two", format="cross", brief="the second angle"),
        ],
        "anchors": {"cross": 1500},
        "rotation_start": SUNDAY,
        "reading_day": "sunday",
        "contra_days": ["wednesday"],
    }
    return DebateSettings(**{**base, **overrides})


# --- the traversal ---------------------------------------------------------


def test_interleave_round_robins_and_sequential_does_not():
    cfg = settings_for()
    assert cfg.rotation == [("alpha", "a1"), ("beta", "b1"), ("alpha", "a2")]
    assert settings_for(order="sequential").rotation == [
        ("alpha", "a1"),
        ("alpha", "a2"),
        ("beta", "b1"),
    ]


def test_the_week_turns_over_on_sunday_and_wraps():
    cfg = settings_for()
    # Saturday is still week 0; the Sunday after it is week 1.
    assert cfg.week_for(SUNDAY) == ("alpha", "a1")
    assert cfg.week_for(SUNDAY + dt.timedelta(days=6)) == ("alpha", "a1")
    assert cfg.week_for(SUNDAY + dt.timedelta(days=7)) == ("beta", "b1")
    # Three entries, so week 3 is week 0 again.
    assert cfg.week_for(SUNDAY + dt.timedelta(days=21)) == ("alpha", "a1")


def test_angles_advance_through_the_week_and_skip_the_reading_day():
    cfg = settings_for()
    assert cfg.angle_for(SUNDAY) is None  # reading day
    keys = [cfg.angle_for(SUNDAY + dt.timedelta(days=n)).key for n in range(1, 7)]  # Mon..Sat
    assert keys == ["one", "two", "one", "two", "one", "two"]


def test_moving_the_reading_day_moves_the_ladder_with_it():
    """The angle is the nth debate day of the week, not the nth weekday. A
    reading day mid-week must not leave a hole where its angle would be."""
    cfg = settings_for(reading_day="wednesday", contra_days=[])
    monday = SUNDAY + dt.timedelta(days=1)
    assert [cfg.angle_for(monday + dt.timedelta(days=n)) for n in range(2, 3)] == [None]
    # Mon=one, Tue=two, Wed=reading, Thu=one — Thursday takes what Wednesday
    # would have, rather than skipping to `two`.
    assert cfg.angle_for(monday).key == "one"
    assert cfg.angle_for(monday + dt.timedelta(days=1)).key == "two"
    assert cfg.angle_for(monday + dt.timedelta(days=3)).key == "one"


def test_the_side_is_yours_except_on_contra_days_and_steelman():
    cfg = settings_for()
    angle = cfg.angles[0]
    monday, wednesday = SUNDAY + dt.timedelta(days=1), SUNDAY + dt.timedelta(days=3)
    assert debate.side_for(cfg, monday, angle) is DebateSide.pro
    assert debate.side_for(cfg, wednesday, angle) is DebateSide.con
    # The steelman angle is *defined* as arguing what you do not hold, so it
    # overrides the weekday rather than being subject to it.
    steel = DebateAngle(key="steelman", label="S", format="cross", brief="the other side")
    assert debate.side_for(cfg, monday, steel) is DebateSide.con


def test_the_contra_bonus_lands_on_the_anchor_not_the_score():
    cfg = settings_for(contra_bonus=75)
    angle = cfg.angles[0]
    assert cfg.anchor_for(angle, contra=False) == 1500
    assert cfg.anchor_for(angle, contra=True) == 1575


def test_a_huge_contra_bonus_cannot_write_an_out_of_range_anchor():
    cfg = settings_for(contra_bonus=400, anchors={"cross": 3900})
    assert cfg.anchor_for(cfg.angles[0], contra=True) == 4000


# --- config validation -----------------------------------------------------


def test_rotation_start_must_be_a_sunday():
    with pytest.raises(ValueError, match="must be a Sunday"):
        settings_for(rotation_start=dt.date(2026, 8, 3))


def test_an_angle_naming_an_unknown_format_is_a_startup_error():
    with pytest.raises(ValueError, match="no entry in"):
        settings_for(anchors={"parliamentary": 1500})


def test_a_duplicate_issue_slug_is_a_startup_error():
    """Two issues sharing a slug would merge their ratings and overwrite each
    other's dossier — both silently."""
    with pytest.raises(ValueError, match="reuses slug"):
        settings_for(
            issues=[
                DebateIssue(slug="alpha", name="A", subareas=["x"]),
                DebateIssue(slug="alpha", name="B", subareas=["y"]),
            ]
        )


def test_the_reading_day_cannot_also_be_a_contra_day():
    with pytest.raises(ValueError, match="also a contra day"):
        settings_for(contra_days=["sunday"])


def test_the_real_config_defines_a_full_six_month_rotation():
    """The shipped config is the one that actually runs; a rotation that does
    not cover the angles evenly would be a curriculum with a hole in it."""
    cfg = load_settings().debate
    assert cfg.enabled
    assert len(cfg.rotation) == sum(len(i.subareas) for i in cfg.issues)
    assert len(cfg.angles) == 6  # one per debate day of the week
    assert len({i.slug for i in cfg.issues}) == len(cfg.issues)


# --- scoring ---------------------------------------------------------------


def test_the_ballot_is_scored_out_of_the_whole_rubric():
    """A judge that answers six of eight lines is scored out of eight. Scoring
    out of what was answered would pay it for omitting your worst lines."""
    assert scoring.debate_score([2, 2], items=8) == 0.25
    assert scoring.debate_score([2] * 8, items=8) == 1.0
    assert scoring.debate_score([1] * 8, items=8) == 0.5


def test_fouls_subtract_and_the_score_floors_at_zero():
    assert scoring.debate_score([2, 2, 2, 2, 0, 0, 0, 0], fouls=1, items=8) == 0.4375
    assert scoring.debate_score([1, 0, 0, 0, 0, 0, 0, 0], fouls=9, items=8) == 0.0


def test_a_band_above_two_is_clamped_rather_than_rejected():
    """A judge that wrote 3 meant full marks. Failing the whole paste over it
    would throw away a debate that already happened."""
    assert scoring.debate_score([3] * 8, items=8) == 1.0


# --- parsing the ballot ----------------------------------------------------

BALLOT = """
{"items": [
  {"key": "framing", "score": 2, "quote": "the dispute is whether status turns on time"},
  {"key": "evidence", "score": 1, "quote": "roughly eleven million people, per DHS"}
], "fouls": [], "verdict": "name your premises"}
"""


def test_a_fenced_ballot_with_commentary_around_it_parses():
    text = f"Good round!\n\n```json\n{BALLOT}\n```\n\nSee you tomorrow."
    card = debate.parse_scorecard(text)
    assert [i.key for i in card.items] == ["framing", "evidence"]
    assert card.verdict == "name your premises"


def test_bare_json_with_no_fence_parses():
    assert len(debate.parse_scorecard(BALLOT).items) == 2


def test_the_last_fenced_block_wins():
    """A model that shows the format first and the real ballot second must not
    be graded on its illustration."""
    first = '```json\n{"items": [{"key": "framing", "score": 0, "quote": ""}]}\n```'
    second = f"```json\n{BALLOT}\n```"
    card = debate.parse_scorecard(f"here is the shape:\n{first}\nand the ballot:\n{second}")
    assert len(card.items) == 2


def test_items_are_returned_in_rubric_order_not_ballot_order():
    text = (
        '{"items": [{"key": "weighing", "score": 1, "quote": "x"}, '
        '{"key": "framing", "score": 1, "quote": "y"}]}'
    )
    assert [i.key for i in debate.parse_scorecard(text).items] == ["framing", "weighing"]


def test_a_repeated_line_keeps_the_lower_score():
    """The only tie-break that cannot be exploited by listing a line again with
    a better number."""
    text = (
        '{"items": [{"key": "framing", "score": 2, "quote": "a long enough quotation here"}, '
        '{"key": "framing", "score": 0, "quote": ""}]}'
    )
    card = debate.parse_scorecard(text)
    assert len(card.items) == 1
    assert card.items[0].score == 0


def test_an_unknown_rubric_line_is_dropped_not_fatal():
    text = (
        '{"items": [{"key": "charisma", "score": 2, "quote": "a long enough quotation"}, '
        '{"key": "framing", "score": 1, "quote": "a long enough quotation"}]}'
    )
    card = debate.parse_scorecard(text)
    assert [i.key for i in card.items] == ["framing"]


def test_an_unknown_foul_is_dropped():
    text = '{"items": [{"key": "framing", "score": 0}], "fouls": [{"kind": "rudeness"}]}'
    assert debate.parse_scorecard(text).fouls == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "no ballot here at all, just chatter",
        "```json\nnot json\n```",
        '{"verdict": "well argued"}',  # no items key
        '{"items": []}',  # nothing scored
        '{"items": [{"key": "charisma", "score": 2}]}',  # nothing *known* scored
    ],
)
def test_unusable_pastes_raise_rather_than_scoring_zero(text):
    """A paste that went wrong must not be recorded as a bad debate. Scoring an
    unreadable ballot 0.0 would cost a rating for a clipboard mistake."""
    with pytest.raises(debate.DebateError):
        debate.parse_scorecard(text)


# --- the quote rule --------------------------------------------------------


def test_a_point_claimed_without_a_quotation_is_voided():
    card = d.Scorecard(
        items=[
            d.RubricItem(key="framing", score=2, quote="a quotation comfortably long enough"),
            d.RubricItem(key="evidence", score=2, quote="short"),
            d.RubricItem(key="clash", score=2, quote=""),
        ]
    )
    applied = debate.apply_quote_rule(card)
    assert [i.score for i in applied.items] == [2, 0, 0]
    assert applied.voided == ["evidence", "clash"]


def test_a_zero_needs_no_quotation():
    """Only a *claim* needs evidence. Requiring one to fail a line would make
    the judge invent quotations for things that did not happen."""
    card = d.Scorecard(items=[d.RubricItem(key="framing", score=0, quote="")])
    applied = debate.apply_quote_rule(card)
    assert applied.voided == []


def test_padding_the_ballot_cannot_raise_the_score():
    """The prompt tells the judge this; this is what makes it true."""
    padded = d.Scorecard(items=[d.RubricItem(key=k, score=2, quote="") for k in debate.RUBRIC_KEYS])
    assert debate.score_card(debate.apply_quote_rule(padded)) == 0.0


# --- readings --------------------------------------------------------------


def item(title: str) -> NewsItem:
    return NewsItem(title=title, link=f"https://example.org/{abs(hash(title))}", cat="uspolitics")


def test_readings_are_ranked_by_how_many_distinct_keywords_they_hit():
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["asylum", "border"])
    picks = debate.pick_readings(
        [item("Fed holds rates"), item("Asylum backlog"), item("New asylum rule at the border")],
        issue,
    )
    assert [r.title for r in picks] == ["New asylum rule at the border", "Asylum backlog"]


def test_a_short_acronym_does_not_match_inside_another_word():
    """The first run of this offered "gas prices", "Spokane police" and "cherry
    ice cream" as immigration reading, because `ICE` is a substring of all
    three. Word boundaries, not substrings — this is why `pick_readings` does
    not reuse `interests.rank`."""
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["ICE", "AI"])
    picks = debate.pick_readings(
        [
            item("Oil prices fall as the Strait reopens"),
            item("Spokane police arrest man over wildfire"),
            item("Airlines cut aid to stranded passengers"),
            item("ICE raids expand to three more states"),
        ],
        issue,
    )
    assert [r.title for r in picks] == ["ICE raids expand to three more states"]


def test_an_all_caps_keyword_matches_case_sensitively():
    """Word boundaries alone still let `ICE` match "cherry ice cream", where
    `ice` really is a word — just not the agency. Case is the only thing that
    separates them."""
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["ICE"])
    picks = debate.pick_readings(
        [item("Buttigieg on cherry ice cream voters"), item("ICE detentions hit a record")], issue
    )
    assert [r.title for r in picks] == ["ICE detentions hit a record"]


def test_a_lowercase_keyword_stays_case_insensitive():
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["immigration"])
    picks = debate.pick_readings([item("Immigration bill clears committee")], issue)
    assert len(picks) == 1


def test_a_multi_word_keyword_matches_as_a_phrase_and_its_plural():
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["green card"])
    picks = debate.pick_readings(
        [item("Green cards and the backlog"), item("A green field and a card game")], issue
    )
    assert [r.title for r in picks] == ["Green cards and the backlog"]


def test_the_shipped_keywords_do_not_match_an_unrelated_headline():
    """A regression guard on the config itself, not only the matcher: the bug
    that produced it was a keyword choice, and the keyword list is the part
    that will keep being edited."""
    cfg = load_settings().debate
    noise = [
        item("Oil prices fall on hopes Strait of Hormuz could reopen"),
        item("Spokane police arrest man connected to wildfire"),
        item("Buttigieg shares his theory for winning over cherry ice cream"),
    ]
    for issue in cfg.issues:
        assert debate.pick_readings(noise, issue) == [], issue.slug


def test_an_issue_with_nothing_in_the_feeds_gets_nothing():
    """Padding with the best of a bad list looks like an answer, which is worse
    than an empty panel that says so."""
    issue = DebateIssue(slug="iss", name="I", subareas=["s"], keywords=["semiconductor"])
    assert debate.pick_readings([item("Fed holds rates")], issue) == []


def test_an_issue_with_no_keywords_gets_nothing_rather_than_everything():
    issue = DebateIssue(slug="iss", name="I", subareas=["s"])
    assert debate.pick_readings([item("Anything at all")], issue) == []


# --- the prompt ------------------------------------------------------------


def session_for(**overrides) -> d.DebateSession:
    base = dict(
        date=SUNDAY,
        issue="alpha",
        subarea="a1",
        angle="one",
        motion="US policy on a1 (Alpha)",
        format="cross",
        side=DebateSide.pro,
        anchor=1500,
    )
    return d.DebateSession(**{**base, **overrides})


def built(**overrides) -> str:
    angle = DebateAngle(key="one", label="One", format="cross", brief="the first angle")
    facts = overrides.pop("facts", [])
    return debate.build_prompt(session_for(**overrides), angle, "Alpha", facts)


def test_the_prompt_carries_the_rubric_the_server_grades_against():
    """Two copies of eight band descriptors would drift inside a month."""
    text = built()
    for criterion in debate.RUBRIC:
        assert criterion.key in text
        for band in criterion.bands:
            assert band in text


def test_the_prompt_asks_for_no_memory_and_a_comprehension_check():
    text = built()
    assert "memory" in text
    assert "three short questions" in text


def test_the_prompt_states_the_quote_rule_so_padding_looks_pointless():
    assert "score it 0" in built()
    assert str(debate.MIN_QUOTE) in built()


def test_a_contra_session_says_the_side_was_assigned():
    assert "did not choose it" in built(side=DebateSide.con, contra=True)
    assert "did not choose it" not in built()


def test_collected_facts_go_into_the_prompt_with_their_sources():
    facts = [
        d.DebateFact(
            issue="alpha",
            date=SUNDAY,
            claim="Eleven million people",
            source="https://dhs.example",
            stance=FactStance.against,
        )
    ]
    text = built(facts=facts)
    assert "Eleven million people" in text
    assert "https://dhs.example" in text
    assert "[against]" in text


# --- the dossier -----------------------------------------------------------


def test_the_dossier_buckets_facts_by_stance_and_logs_the_position():
    issue = DebateIssue(slug="alpha", name="Alpha", subareas=["a1"])
    facts = [
        d.DebateFact(issue="alpha", date=SUNDAY, claim="For it", source="src1", stance="for"),
        d.DebateFact(
            issue="alpha", date=SUNDAY, claim="Against it", source="src2", stance="against"
        ),
    ]
    sessions = [session_for(position="I think X", confidence=60, position_after="Less sure")]
    text = debate.render_dossier(issue, facts, sessions)

    assert text.index("For it") < text.index("Against it")
    assert "## Evidence for" in text and "## Evidence against" in text
    assert "I think X" in text and "Less sure" in text
    # The header has to say this out loud: it is written by the server on every
    # fact, so anything typed into it is lost.
    assert "edits here are lost" in text


def test_an_empty_dossier_says_so_rather_than_rendering_blank_headings():
    issue = DebateIssue(slug="alpha", name="Alpha", subareas=["a1"])
    text = debate.render_dossier(issue, [], [])
    assert text.count("*Nothing yet.*") == 3
    assert "*None yet.*" in text
