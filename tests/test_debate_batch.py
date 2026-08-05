"""Phase 2: the overnight hand-off, the week's reading, and the trends.

The validators get the most attention, for the reason `test_batch.py` gives:
these files are written by a model at 3am with nobody watching, and every check
here is one that would otherwise be discovered at 8am as a wrong-looking panel.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import batch, debate, state
from dashboard.models import domain as d
from dashboard.models.batch import DebateMotion, ReadingList

SUNDAY = dt.date(2026, 8, 2)
MONDAY = dt.date(2026, 8, 3)
WEDNESDAY = dt.date(2026, 8, 5)


def week_of(settings, date: dt.date) -> tuple[str, str]:
    week = settings.debate.week_for(date)
    assert week is not None
    return week


def motion_json(settings, date: dt.date, **overrides) -> str:
    slug, subarea = week_of(settings, date)
    angle = settings.debate.angle_for(date)
    body = {
        "date": date.isoformat(),
        "issue": slug,
        "subarea": subarea,
        "angle": angle.key if angle else "empirical",
        "motion": "Congress should pass the 2026 Registered Provisional Immigrant Act.",
        "note": "The bill cleared committee on 2026-07-29.",
        "source_url": "https://www.congress.gov/bill/example",
    }
    return json.dumps({**body, **overrides})


def reading_json(settings, week: dt.date, **overrides) -> str:
    slug, subarea = week_of(settings, week)
    body = {
        "week_start": week.isoformat(),
        "issue": slug,
        "subarea": subarea,
        "articles": [
            {
                "title": "How the registry stalled for a decade",
                "url": "https://example.org/a",
                "publisher": "The Atlantic",
                "summary": "A long read.",
                "required": True,
            },
            {
                "title": "The enforcement case, restated",
                "url": "https://example.org/b",
                "required": True,
            },
        ],
        "books": [
            {"title": "Immigration Economics", "author": "Borjas", "lean": "right"},
            {
                "title": "The Undocumented Americans",
                "author": "Cornejo Villavicencio",
                "lean": "left",
            },
        ],
    }
    return json.dumps({**body, **overrides})


# --- the context payload ---------------------------------------------------


def test_the_context_names_tonights_motion_job(settings):
    payload = batch.debate_context(settings, None, WEDNESDAY)
    assert payload["enabled"] is True
    assert payload["issue"] == "immigration"
    assert payload["motion"]["angle"] == "values"
    # The brief is what makes Wednesday's motion different from Tuesday's on
    # the same sub-area, so the generator gets it rather than the label alone.
    assert "principle governs" in payload["motion"]["angle_brief"]


def test_a_reading_day_has_no_motion_to_generate(settings):
    payload = batch.debate_context(settings, None, SUNDAY)
    assert payload["reading_day"] is True
    assert payload["motion"] is None


def test_the_reading_list_is_asked_for_only_on_the_night_before(settings):
    """Generated on Saturday so it is on screen when the week opens, rather
    than a day after the day it is for. No other night asks for one."""
    for offset in range(6):  # Sunday through Friday
        assert (
            batch.debate_context(settings, None, SUNDAY + dt.timedelta(days=offset))["reading_list"]
            is None
        )

    saturday = SUNDAY + dt.timedelta(days=6)
    reading = batch.debate_context(settings, None, saturday)["reading_list"]
    assert reading is not None
    assert reading["week_start"] == (SUNDAY + dt.timedelta(days=7)).isoformat()
    # The week *after* the one being lived, since that is the one opening.
    assert reading["issue"] == "ai-and-tech"


def test_books_are_offered_only_the_first_time_a_subarea_comes_round(settings, repo):
    saturday = SUNDAY + dt.timedelta(days=6)
    assert batch.debate_context(settings, repo, saturday)["reading_list"]["books_wanted"] is True

    week = SUNDAY + dt.timedelta(days=7)
    slug, subarea = week_of(settings, week)
    repo.upsert_readings(
        [
            d.DebateReading(
                week_start=week,
                issue=slug,
                subarea=subarea,
                kind="book",
                title="Something",
                lean="left",
                required=False,
            )
        ]
    )
    assert batch.debate_context(settings, repo, saturday)["reading_list"]["books_wanted"] is False


def test_the_context_survives_having_no_database(settings):
    """Everything load-bearing is config, so a database outage costs the
    dossier thumbnail and nothing else."""
    payload = batch.debate_context(settings, None, WEDNESDAY)
    assert payload["motion"] is not None
    assert payload["dossier"] == {"facts": 0, "recent_claims": [], "past_motions": []}


# --- validating a motion ---------------------------------------------------


def test_a_good_motion_validates(settings):
    motion, errors = batch.validate_debate(settings, motion_json(settings, WEDNESDAY))
    assert errors == []
    assert isinstance(motion, DebateMotion)


def test_a_motion_for_the_wrong_week_is_rejected(settings):
    _, errors = batch.validate_debate(
        settings, motion_json(settings, WEDNESDAY, issue="health-care")
    )
    assert any("is a 'immigration' week" in e for e in errors)


def test_a_motion_for_the_wrong_angle_is_rejected(settings):
    _, errors = batch.validate_debate(settings, motion_json(settings, WEDNESDAY, angle="steelman"))
    assert any("'values' angle" in e for e in errors)


def test_a_motion_on_the_reading_day_is_rejected(settings):
    _, errors = batch.validate_debate(settings, motion_json(settings, SUNDAY))
    assert any("reading day" in e for e in errors)


def test_a_motion_that_is_not_a_sentence_is_rejected(settings):
    """A frame is what this exists to replace. "US policy on X" is not a
    resolution, and cannot be argued for or against."""
    _, errors = batch.validate_debate(
        settings, motion_json(settings, WEDNESDAY, motion="US policy on the registry")
    )
    assert any("not a sentence" in e for e in errors)


def test_an_unsourced_motion_is_rejected(settings):
    _, errors = batch.validate_debate(settings, motion_json(settings, WEDNESDAY, source_url=None))
    assert any("source_url" in e for e in errors)


# --- validating a reading list ---------------------------------------------


def test_a_good_reading_list_validates(settings):
    listing, errors = batch.validate_debate(settings, reading_json(settings, SUNDAY))
    assert errors == []
    assert isinstance(listing, ReadingList)


def test_a_reading_list_not_starting_on_the_reading_day_is_rejected(settings):
    _, errors = batch.validate_debate(
        settings, reading_json(settings, SUNDAY, week_start="2026-08-04")
    )
    assert any("not the configured reading day" in e for e in errors)


def test_a_reading_list_with_no_required_article_is_rejected(settings):
    raw = json.loads(reading_json(settings, SUNDAY))
    for article in raw["articles"]:
        article["required"] = False
    _, errors = batch.validate_debate(settings, json.dumps(raw))
    assert any("no required articles" in e for e in errors)


def test_books_all_from_one_side_are_rejected(settings):
    """One from each side was the whole request. One shelf twice is a reading
    list that confirms whatever you already thought."""
    raw = json.loads(reading_json(settings, SUNDAY))
    for book in raw["books"]:
        book["lean"] = "left"
    _, errors = batch.validate_debate(settings, json.dumps(raw))
    assert any("one from each side" in e for e in errors)


def test_a_repeated_article_is_rejected(settings):
    raw = json.loads(reading_json(settings, SUNDAY))
    raw["articles"][1]["url"] = raw["articles"][0]["url"]
    _, errors = batch.validate_debate(settings, json.dumps(raw))
    assert any("listed twice" in e for e in errors)


def test_the_validator_tells_the_two_shapes_apart(settings):
    """One command for both files, because the 3am task should not have to
    remember which validator each one wants."""
    motion, _ = batch.validate_debate(settings, motion_json(settings, WEDNESDAY))
    listing, _ = batch.validate_debate(settings, reading_json(settings, SUNDAY))
    assert isinstance(motion, DebateMotion)
    assert isinstance(listing, ReadingList)


# --- serving what the task left --------------------------------------------


def test_a_missing_batch_file_is_not_an_error(tmp_path):
    """A batch is an optimisation, and an optimisation that can take the panel
    down is not one."""
    assert debate.motion_from_batch(tmp_path, WEDNESDAY, "immigration", "x") is None
    assert debate.readings_from_batch(tmp_path, SUNDAY, "immigration", "x") is None


def test_unparseable_json_falls_through_rather_than_raising(tmp_path):
    debate.motion_path(tmp_path, WEDNESDAY).write_text("{ not json", encoding="utf-8")
    assert debate.motion_from_batch(tmp_path, WEDNESDAY, "immigration", "x") is None


def test_a_motion_about_the_wrong_subarea_is_ignored(settings, tmp_path):
    """The rotation is editable and the file was written eight hours ago. A
    motion about semiconductors under an immigration heading would look
    deliberate, which is worse than falling back to the frame."""
    slug, subarea = week_of(settings, WEDNESDAY)
    debate.motion_path(tmp_path, WEDNESDAY).write_text(
        motion_json(settings, WEDNESDAY), encoding="utf-8"
    )
    assert debate.motion_from_batch(tmp_path, WEDNESDAY, slug, subarea) is not None
    assert debate.motion_from_batch(tmp_path, WEDNESDAY, slug, "a different sub-area") is None


def test_week_start_follows_the_configured_reading_day(settings):
    cfg = settings.debate
    for offset in range(7):
        assert debate.week_start(cfg, SUNDAY + dt.timedelta(days=offset)) == SUNDAY


def test_to_readings_never_makes_a_book_required(settings):
    listing = ReadingList(**json.loads(reading_json(settings, SUNDAY)))
    rows = debate.to_readings(listing, SUNDAY)
    assert [r.required for r in rows if r.kind == "book"] == [False, False]
    assert all(r.required for r in rows if r.kind == "article")


# --- storing the week's reading --------------------------------------------


def readings_for(settings, week: dt.date) -> list[d.DebateReading]:
    listing = ReadingList(**json.loads(reading_json(settings, week)))
    return debate.to_readings(listing, week)


def test_regenerating_a_reading_list_does_not_untick_what_was_read(settings, repo):
    """The 3am task may run twice. Losing a tick would mean re-reading an
    article to satisfy a box."""
    rows = readings_for(settings, SUNDAY)
    repo.upsert_readings(rows)
    stored = repo.readings(SUNDAY)
    repo.mark_read(stored[0].id, SUNDAY)

    repo.upsert_readings(rows)
    again = repo.readings(SUNDAY)
    assert len(again) == len(stored)
    assert again[0].read_on == SUNDAY


def test_required_articles_are_listed_before_optional_ones_and_books(settings, repo):
    rows = readings_for(settings, SUNDAY)
    rows.append(
        d.DebateReading(
            week_start=SUNDAY,
            issue=rows[0].issue,
            subarea=rows[0].subarea,
            kind="article",
            title="An optional extra",
            url="https://example.org/c",
            required=False,
        )
    )
    repo.upsert_readings(rows)
    kinds = [(r.kind, r.required) for r in repo.readings(SUNDAY)]
    assert kinds == [
        ("article", True),
        ("article", True),
        ("article", False),
        ("book", False),
        ("book", False),
    ]


def test_unread_required_counts_only_articles_that_are_required(settings, repo):
    repo.upsert_readings(readings_for(settings, SUNDAY))
    assert repo.unread_required(SUNDAY) == 2
    for item in repo.readings(SUNDAY):
        if item.required:
            repo.mark_read(item.id, SUNDAY)
    assert repo.unread_required(SUNDAY) == 0


def test_a_tick_can_be_taken_back(settings, repo):
    repo.upsert_readings(readings_for(settings, SUNDAY))
    item = repo.readings(SUNDAY)[0]
    repo.mark_read(item.id, SUNDAY)
    assert repo.mark_read(item.id, None).read_on is None


def test_the_schema_refuses_a_required_book(settings, repo):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute(
            "INSERT INTO debate_reading (week_start, issue, subarea, kind, title, required) "
            "VALUES ('2026-08-02', 'immigration', 'x', 'book', 'Something', 1)"
        )


# --- the prompt, with a week's reading in it -------------------------------


def session_for(**overrides) -> d.DebateSession:
    base = dict(
        date=WEDNESDAY,
        issue="immigration",
        subarea="a1",
        angle="values",
        motion="US policy on a1",
        format="socratic",
        side="pro",
        anchor=1500,
    )
    return d.DebateSession(**{**base, **overrides})


def prompt_with(reading: list[d.DebateReading], **overrides) -> str:
    from dashboard.config import DebateAngle

    angle = DebateAngle(key="values", label="V", format="socratic", brief="the values crux")
    return debate.build_prompt(session_for(**overrides), angle, "Immigration", [], reading)


def test_the_comprehension_check_targets_the_weeks_reading_when_there_is_some(settings, repo):
    repo.upsert_readings(readings_for(settings, SUNDAY))
    stored = repo.readings(SUNDAY)
    repo.mark_read(stored[0].id, SUNDAY)
    text = prompt_with(repo.readings(SUNDAY))

    assert "the week's reading" in text
    assert "this is where it gets tested" in text
    # Only what was actually ticked. Quizzing on a piece you never claimed to
    # have read is a question with no honest answer.
    assert stored[0].title in text
    assert stored[1].title not in text


def test_with_no_weeks_reading_the_check_falls_back_to_the_headlines():
    text = prompt_with([])
    assert "the week's reading" not in text
    assert "three short questions" in text


def test_a_resolved_motion_is_confirmed_rather_than_researched():
    text = prompt_with(
        [],
        motion_source="batch",
        motion="Congress should pass X.",
        motion_note="cleared committee 2026-07-29",
    )
    assert "**Resolution:**" in text
    assert "drawn up overnight" in text
    assert "cleared committee" in text


def test_a_template_frame_still_asks_the_opponent_to_sharpen_it():
    text = prompt_with([], motion_source="template")
    assert "**Frame:**" in text
    assert "Search for the most live instance" in text


# --- the trends ------------------------------------------------------------


def scored_session(repo, date: dt.date, scores: dict[str, int], fouls: int = 0) -> None:
    repo.open_debate(session_for(date=date))
    card = d.Scorecard(
        items=[
            d.RubricItem(key=k, score=v, quote="a long enough quotation") for k, v in scores.items()
        ],
        fouls=[d.Foul(kind="motive") for _ in range(fouls)],
    )
    repo.score_debate(date, card, debate.score_card(card))


def test_a_line_the_judge_skipped_counts_as_zero(settings, repo):
    """Matching how `debate_score` totals it. Skipping it here instead would
    make the chart disagree with the rating it is explaining."""
    scored_session(repo, MONDAY, {"framing": 2})
    trends = state.debate_history(settings, repo)
    lines = {line.key: line for line in trends.lines}
    assert lines["framing"].scores == [2]
    assert lines["weighing"].scores == [0]


def test_lines_are_ordered_weakest_recent_first(settings, repo):
    scored_session(repo, MONDAY, {"framing": 2, "evidence": 0, "clash": 1})
    trends = state.debate_history(settings, repo)
    assert trends.lines[0].recent <= trends.lines[-1].recent
    assert trends.lines[-1].key == "framing"


def test_the_trend_stays_zero_until_there_is_history_on_both_sides(settings, repo):
    """A trend computed from two debates is noise wearing a plus sign."""
    for i in range(3):
        scored_session(repo, MONDAY + dt.timedelta(days=i), {"framing": 2})
    trends = state.debate_history(settings, repo)
    assert all(line.trend == 0.0 for line in trends.lines)


def test_fouls_and_voided_lines_are_counted(settings, repo):
    scored_session(repo, MONDAY, {"framing": 2}, fouls=2)
    repo.open_debate(session_for(date=MONDAY + dt.timedelta(days=1)))
    card = debate.apply_quote_rule(
        d.Scorecard(items=[d.RubricItem(key="framing", score=2, quote="short")])
    )
    repo.score_debate(MONDAY + dt.timedelta(days=1), card, debate.score_card(card))

    trends = state.debate_history(settings, repo)
    assert trends.debates == 2
    assert trends.fouls[0].kind == "motive"
    assert trends.fouls[0].count == 2
    assert trends.voided == 1


def test_conviction_records_whether_arguing_moved_you(settings, repo):
    repo.open_debate(session_for(date=MONDAY))
    repo.set_debate_position(MONDAY, "I think X", 60, after=False)
    repo.set_debate_position(MONDAY, "Now I think Y", 40, after=True)

    trends = state.debate_history(settings, repo)
    assert len(trends.conviction) == 1
    assert trends.conviction[0].moved is True
    assert trends.conviction[0].confidence == 60


def test_an_unchanged_position_is_not_counted_as_moved(settings, repo):
    repo.open_debate(session_for(date=MONDAY))
    repo.set_debate_position(MONDAY, "I think X", 60, after=False)
    repo.set_debate_position(MONDAY, "I think X", 70, after=True)
    assert state.debate_history(settings, repo).conviction[0].moved is False


def test_an_empty_history_is_empty_rather_than_an_error(settings, repo):
    trends = state.debate_history(settings, repo)
    assert trends.debates == 0
    assert [line.recent for line in trends.lines] == [0.0] * len(debate.RUBRIC)
