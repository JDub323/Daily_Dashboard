"""The investigation pool: the paste, the draw, and what a wasted day costs.

The rule worth most of this file is that a drawn topic is *not* spent. The
other daily cells in this app claim their subject on the way in -- the theorem
draw, the math problem -- and need a reconciliation sweep afterwards to undo
the claim when the day goes unanswered. This one claims the day and leaves the
topic alone, so "returns to the pool" is a thing the schema does rather than a
thing a job has to remember to do at midnight.
"""

from __future__ import annotations

import datetime as dt
import json
import random

import pytest

from dashboard import investigate, state

TODAY = dt.date(2026, 8, 28)
TOMORROW = dt.date(2026, 8, 29)


@pytest.fixture
def files(tmp_path):
    """Where the write-ups go. Never the real `investigations/` directory --
    these tests create and read files, and the real one holds work."""
    return tmp_path / "investigations"


def add(repo, *topics, on=TODAY):
    return repo.add_investigations(list(topics), on)


# --- the paste -------------------------------------------------------------


def test_strips_the_decoration_a_handout_arrives_with():
    parsed = investigate.parse_topics(
        "- Taiwan strait transit rights\n"
        "2. AUKUS submarine timeline\n"
        "b) The Quad after 2025\n"
        "• Sanctions on Rosatom\n"
    )
    assert [p.topic for p in parsed] == [
        "Taiwan strait transit rights",
        "AUKUS submarine timeline",
        "The Quad after 2025",
        "Sanctions on Rosatom",
    ]


def test_a_leading_year_is_not_an_enumerator():
    """The marker pattern has to let a topic keep a number it means.

    "2026 defence budget" opens exactly like "2. Defence budget" and only the
    punctuation separates them, which is why the enumerator branch requires it.
    """
    (parsed,) = investigate.parse_topics("2026 defence budget request")
    assert parsed.topic == "2026 defence budget request"


def test_blank_lines_vanish_rather_than_becoming_errors():
    parsed = investigate.parse_topics("\n\nArctic shipping lanes\n   \n\nGrain corridor\n")
    assert [p.topic for p in parsed] == ["Arctic shipping lanes", "Grain corridor"]


def test_a_bad_line_reports_itself_and_the_rest_survive():
    parsed = investigate.parse_topics("Sahel coup belt\n7\nRed Sea shipping")
    assert [p.topic for p in parsed] == ["Sahel coup belt", None, "Red Sea shipping"]
    assert parsed[1].error == "too short to be a topic"


def test_duplicates_inside_one_paste_are_caught_before_the_database():
    parsed = investigate.parse_topics("Taiwan strait\n- taiwan  strait\nHormuz")
    assert [p.topic for p in parsed] == ["Taiwan strait", None, "Hormuz"]
    assert parsed[1].error == "listed twice in this paste"


# --- the pool --------------------------------------------------------------


def test_the_same_list_pasted_twice_adds_nothing_the_second_time(repo):
    add(repo, "Taiwan strait transit rights", "AUKUS submarine timeline")
    again = add(repo, "Taiwan strait transit rights", "AUKUS submarine timeline")

    assert again == [None, None]
    assert len(repo.pool()) == 2


def test_the_pool_check_is_case_folded(repo):
    add(repo, "Taiwan strait transit rights")
    assert add(repo, "TAIWAN STRAIT TRANSIT RIGHTS") == [None]
    assert len(repo.pool()) == 1


def test_the_result_is_positional_so_a_line_can_be_reported(repo):
    add(repo, "Hormuz")
    written = add(repo, "Sahel", "Hormuz", "Arctic")

    assert [w is None for w in written] == [False, True, False]


def test_the_pool_keeps_the_order_the_handout_had(repo):
    """Insertion order, not `added_on`: one paste shares a date, so ordering by
    the day would reshuffle each list on every read."""
    add(repo, "first", "second", "third")
    assert [i.topic for i in repo.pool()] == ["first", "second", "third"]


def test_a_struck_off_topic_leaves_the_pool_and_frees_its_name(repo):
    (item,) = add(repo, "Taiwan strait transit rights")
    cleared = repo.clear_investigation(item.id, TODAY)

    assert cleared.cleared_on == TODAY
    assert not cleared.open
    assert repo.pool() == []
    # The unique index is scoped to open rows, so a topic struck off in August
    # can fairly be handed out again in November.
    assert add(repo, "Taiwan strait transit rights", on=TOMORROW) != [None]


def test_clearing_never_counts_as_having_investigated_anything(repo):
    (item,) = add(repo, "Taiwan strait transit rights")
    repo.clear_investigation(item.id, TODAY)
    assert repo.investigated_count() == 0


def test_clearing_something_that_is_gone_is_a_no_op(repo):
    (item,) = add(repo, "Hormuz")
    repo.clear_investigation(item.id, TODAY)
    again = repo.clear_investigation(item.id, TOMORROW)
    assert again.cleared_on == TODAY  # the first strike stands


def test_clearing_a_topic_that_never_existed_raises(repo):
    with pytest.raises(LookupError):
        repo.clear_investigation(404, TODAY)


# --- the draw --------------------------------------------------------------


def test_the_draw_is_fixed_once_made(repo):
    add(repo, *[f"topic {n}" for n in range(20)])

    first = repo.draw_investigation(TODAY, random.Random(1))
    # A different seed, which would pick differently on an undrawn day.
    second = repo.draw_investigation(TODAY, random.Random(99))

    assert first.investigation_id == second.investigation_id


def test_an_empty_pool_draws_nothing_rather_than_erroring(repo):
    assert repo.draw_investigation(TODAY) is None
    assert repo.investigation_day(TODAY) is None


def test_a_pool_of_struck_off_topics_is_an_empty_pool(repo):
    (item,) = add(repo, "Hormuz")
    repo.clear_investigation(item.id, TODAY)
    assert repo.draw_investigation(TODAY) is None


def test_the_drawn_topic_stays_in_the_pool_until_it_is_graded(repo):
    """The whole of "returns to the pool".

    Drawing claims the *day*, not the topic. Nothing is spent until a write-up
    is graded, so a day that draws and then goes unanswered needs no midnight
    sweep to put anything back -- there is nothing to put back.
    """
    (item,) = add(repo, "Taiwan strait transit rights")
    repo.draw_investigation(TODAY)

    assert [i.topic for i in repo.pool()] == ["Taiwan strait transit rights"]
    assert repo.investigation(item.id).open

    # A fresh day draws from the whole pool, which still holds it.
    tomorrow = repo.draw_investigation(TOMORROW)
    assert tomorrow.investigation_id == item.id


def test_an_investigated_topic_leaves_the_pool(repo):
    """Reaches for SQL because nothing sets `used_on` until the ballot lands.

    Worth testing now anyway: the open-pool predicate is shared by the draw,
    the panel count and the unique index, and it is the one thing here that
    would fail silently.
    """
    item, _ = add(repo, "Hormuz", "Sahel")
    repo.conn.execute("UPDATE investigation SET used_on = ? WHERE id = ?", ("2026-08-28", item.id))

    assert [i.topic for i in repo.pool()] == ["Sahel"]
    assert repo.investigated_count() == 1
    assert repo.draw_investigation(TODAY).investigation_id != item.id


# --- the panel -------------------------------------------------------------


def test_the_panel_reports_nothing_drawn_before_the_button(repo, files):
    add(repo, "Hormuz", "Sahel")
    panel = state.investigate_panel(repo, TODAY, files)

    assert panel.today is None
    assert panel.drawn_on is None
    assert panel.pool_size == 2


def test_the_panel_never_draws(repo, files):
    """Rendering the tab must not spend a topic -- the rule the theorem draw
    holds too."""
    add(repo, "Hormuz")
    state.investigate_panel(repo, TODAY, files)
    assert repo.investigation_day(TODAY) is None


def test_the_panel_carries_the_drawn_topic(repo, files):
    add(repo, "Taiwan strait transit rights")
    repo.draw_investigation(TODAY)

    panel = state.investigate_panel(repo, TODAY, files)
    assert panel.today.topic == "Taiwan strait transit rights"
    assert panel.drawn_on == TODAY
    # Still in the pool, and the panel says both things at once without
    # contradicting itself.
    assert panel.pool_size == 1


def test_a_topic_struck_off_after_being_drawn_is_still_the_days_topic(repo, files):
    """Drawn in the morning, struck off in the evening. The day did draw it,
    and rewriting that after the fact would make the panel disagree with its
    own history."""
    (item,) = add(repo, "Hormuz")
    repo.draw_investigation(TODAY)
    repo.clear_investigation(item.id, TODAY)

    panel = state.investigate_panel(repo, TODAY, files)
    assert panel.today.topic == "Hormuz"
    assert panel.pool_size == 0


# --- the endpoints ---------------------------------------------------------


def test_the_box_adds_what_is_new_and_says_so(client):
    body = {"date": TODAY.isoformat(), "text": "- Hormuz\n- Sahel\n- Hormuz\n"}
    got = client.post("/api/investigate/add", json=body).json()

    assert got["added"] == 2
    assert got["lines"][2]["error"] == "listed twice in this paste"
    assert got["panel"]["pool_size"] == 2


def test_a_paste_of_nothing_but_duplicates_is_not_a_success(client):
    body = {"date": TODAY.isoformat(), "text": "Hormuz"}
    client.post("/api/investigate/add", json=body)
    again = client.post("/api/investigate/add", json=body).json()

    assert again["added"] == 0
    assert again["lines"][0]["error"] == "already in the pool"


def test_drawing_twice_returns_the_same_topic(client):
    paste = "Hormuz\nSahel\nArctic lanes\nThe Quad\nAUKUS"
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": paste})

    first = client.post("/api/investigate/draw", json={"date": TODAY.isoformat()}).json()
    second = client.post("/api/investigate/draw", json={"date": TODAY.isoformat()}).json()

    assert first["today"]["id"] == second["today"]["id"]


def test_drawing_from_an_empty_pool_is_a_panel_not_an_error(client):
    got = client.post("/api/investigate/draw", json={"date": TODAY.isoformat()})

    assert got.status_code == 200
    assert got.json()["today"] is None
    assert got.json()["pool_size"] == 0


def test_the_boot_payload_carries_the_panel(client):
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": "Hormuz"})
    got = client.get("/api/state", params={"date": TODAY.isoformat()}).json()

    assert got["investigate"]["pool_size"] == 1
    assert got["investigate"]["today"] is None


# --- the file --------------------------------------------------------------


def test_the_slug_is_a_filename_and_never_a_path():
    """The one property that matters. A topic is typed by a human and becomes
    a filename, so it has to be incapable of naming a directory."""
    for topic in ["../../etc/passwd", "a/b/c", "..", ".hidden", r"C:\windows"]:
        made = investigate.slug(topic)
        assert "/" not in made and "\\" not in made
        assert not made.startswith(".")
        assert made not in ("", ".", "..")


def test_the_slug_folds_accents_rather_than_dropping_them():
    assert investigate.slug("Côte d'Ivoire sanctions") == "cote-d-ivoire-sanctions"


def test_a_long_topic_is_cut_at_a_word():
    made = investigate.slug("the " * 60)
    assert len(made) <= investigate.MAX_SLUG
    assert not made.endswith("-")


def test_a_topic_of_pure_punctuation_still_gets_a_name():
    assert investigate.slug("!!! ???") == "untitled"


def test_the_scaffold_is_not_a_write_up():
    text = investigate.scaffold("Hormuz", TODAY)
    assert investigate.body(text) == ""


def test_the_body_is_what_you_typed_under_the_scaffold():
    text = investigate.scaffold("Hormuz", TODAY) + "Iran has never actually closed it.\n"
    assert investigate.body(text) == "Iran has never actually closed it."


def test_a_second_topic_with_the_same_slug_gets_its_own_file(files):
    files.mkdir(parents=True)
    (files / "hormuz.md").write_text("first")

    got = investigate.file_for(files, "Hormuz!", existing=None)
    assert got.name == "hormuz-2.md"


def test_the_recorded_path_beats_the_slug(files):
    """A topic that has been written up keeps its file even if the slug
    would now produce a different name -- otherwise a rename orphans work."""
    got = investigate.file_for(files, "Hormuz", existing="something-else.md")
    assert got == files / "something-else.md"


def test_an_existing_file_is_never_overwritten(files):
    path = files / "hormuz.md"
    investigate.ensure_file(path, "Hormuz", TODAY)
    path.write_text("# Hormuz\n\nHours of typing.\n")

    investigate.ensure_file(path, "Hormuz", TOMORROW)
    assert "Hours of typing." in path.read_text()


def test_a_missing_file_reads_as_empty_rather_than_raising(files):
    assert investigate.read_file(files / "nothing.md") == ""


# --- the file, through the API ---------------------------------------------


def drawn(client, topic="Hormuz shipping insurance rates"):
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": topic})
    return client.post("/api/investigate/draw", json={"date": TODAY.isoformat()}).json()


def test_opening_creates_the_file_and_records_it(client, settings):
    drawn(client)
    panel = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()

    path = settings.path("investigations") / panel["today"]["path"]
    assert path.exists()
    assert panel["written"] is False


def test_opening_twice_keeps_the_same_file(client):
    drawn(client)
    first = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()
    second = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()

    assert first["today"]["path"] == second["today"]["path"]


def test_reading_back_an_empty_file_is_refused(client):
    drawn(client)
    client.post("/api/investigate/open", json={"date": TODAY.isoformat()})
    got = client.post("/api/investigate/read", json={"date": TODAY.isoformat()})

    assert got.status_code == 409  # the app's mapping for a refused ValueError
    assert "empty" in got.json()["error"]


def test_reading_back_takes_the_file_as_the_submission(client, settings):
    drawn(client)
    panel = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()
    path = settings.path("investigations") / panel["today"]["path"]
    path.write_text(path.read_text() + "Lloyd's rates tripled after the first drone strike.\n")

    got = client.post("/api/investigate/read", json={"date": TODAY.isoformat()}).json()
    assert got["written"] is True
    assert "Lloyd's rates tripled" in got["day"]["write_up"]


def test_the_panel_reads_the_file_rather_than_trusting_the_snapshot(client, settings):
    """The file is openable from a terminal this app knows nothing about, so
    a snapshot taken an hour ago is not evidence about what is on disk now."""
    drawn(client)
    panel = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()
    path = settings.path("investigations") / panel["today"]["path"]
    path.write_text(path.read_text() + "Something.\n")
    client.post("/api/investigate/read", json={"date": TODAY.isoformat()})

    # Emptied behind the app's back.
    path.write_text("# Hormuz shipping insurance rates\n\n")
    after = client.get("/api/investigate", params={"date": TODAY.isoformat()}).json()

    assert after["written"] is False
    assert after["day"]["write_up"] is not None  # the submission still stands


def test_the_same_topic_drawn_again_opens_the_work_already_done(client, settings):
    """The reason the path is on the topic and not on the day."""
    drawn(client)
    panel = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()
    path = settings.path("investigations") / panel["today"]["path"]
    path.write_text(path.read_text() + "Half a paragraph.\n")

    # Never read back, so the topic is still open and tomorrow draws it again.
    client.post("/api/investigate/draw", json={"date": TOMORROW.isoformat()})
    again = client.post("/api/investigate/open", json={"date": TOMORROW.isoformat()}).json()

    assert again["today"]["path"] == panel["today"]["path"]
    assert "Half a paragraph." in again["file_body"]


def test_opening_with_nothing_drawn_is_a_clean_error(client):
    got = client.post("/api/investigate/open", json={"date": TODAY.isoformat()})
    assert got.status_code == 404


# --- the ballot ------------------------------------------------------------


def ballot_json(scores=None, corrections=(), **extra):
    """A grader's reply, shaped the way the prompt asks for it."""
    scores = scores or dict.fromkeys([c.key for c in investigate.RUBRIC], 2)
    payload = {
        "items": [{"key": k, "score": v, "note": f"note on {k}"} for k, v in scores.items()],
        "corrections": list(corrections),
        "feedback": "Sourcing was the weak half. Name the report, not the outlet.",
        "verdict": "solid, under-sourced",
        **extra,
    }
    return "Here is my assessment.\n\n```json\n" + json.dumps(payload) + "\n```"


def test_the_ballot_is_found_inside_a_chat_reply():
    ballot = investigate.parse_ballot(ballot_json())
    assert len(ballot.items) == len(investigate.RUBRIC)
    assert investigate.score_ballot(ballot) == 1.0


def test_the_second_fenced_block_wins():
    """A grader that shows the shape first and then fills it in leaves two
    blocks, and the real one is the last."""
    text = (
        "The format is:\n```json\n"
        + json.dumps({"items": [], "feedback": "x"})
        + "\n```\nAnd here it is:\n"
        + ballot_json({"sourcing": 1})
    )
    ballot = investigate.parse_ballot(text)
    assert [i.key for i in ballot.items] == ["sourcing"]


def test_bare_braces_with_no_fence_still_parse():
    payload = {"items": [{"key": "depth", "score": 2}], "feedback": "fine"}
    ballot = investigate.parse_ballot("Sure. " + json.dumps(payload))
    assert ballot.items[0].key == "depth"


def test_the_lines_come_back_in_rubric_order():
    """Whatever order the grader wrote them in, the panel's five rows are
    always in the same places."""
    shuffled = {"calibration": 1, "sourcing": 2, "reasoning": 0, "accuracy": 2, "depth": 1}
    ballot = investigate.parse_ballot(ballot_json(shuffled))
    assert [i.key for i in ballot.items] == [c.key for c in investigate.RUBRIC]


def test_an_invented_sixth_line_costs_that_line_and_not_the_paste():
    ballot = investigate.parse_ballot(ballot_json({"sourcing": 2, "eloquence": 2}))
    assert [i.key for i in ballot.items] == ["sourcing"]


def test_a_line_listed_twice_keeps_the_lower_score():
    """The one tie-break that cannot be exploited by listing a line again."""
    payload = {
        "items": [{"key": "depth", "score": 2}, {"key": "depth", "score": 0}],
        "feedback": "x",
    }
    ballot = investigate.parse_ballot("```json\n" + json.dumps(payload) + "\n```")
    assert ballot.items[0].score == 0


def test_a_score_out_of_range_is_clamped_not_rejected():
    ballot = investigate.parse_ballot(ballot_json({"depth": 7}))
    assert ballot.items[0].score == 2


def test_an_empty_paste_says_so():
    with pytest.raises(investigate.InvestigateError, match="nothing pasted"):
        investigate.parse_ballot("   ")


def test_a_paste_with_no_ballot_in_it_says_where_to_look():
    with pytest.raises(investigate.InvestigateError, match="json block"):
        investigate.parse_ballot("Nice work. I thought the second paragraph was strongest.")


def test_a_ballot_of_only_unknown_lines_is_refused():
    with pytest.raises(investigate.InvestigateError, match="no usable rubric lines"):
        investigate.parse_ballot(ballot_json({"vibes": 2}))


# --- the accuracy rule -----------------------------------------------------


def test_full_marks_for_accuracy_are_capped_once_something_was_corrected():
    """Enforced server-side, not trusted to the prompt: a rule that lives only
    in a prompt holds until the grader would rather be encouraging."""
    correction = {"claim": "NATO spends 2%", "correction": "it is a target", "source": "NATO"}
    ballot = investigate.parse_ballot(ballot_json(corrections=[correction]))

    accuracy = next(i for i in ballot.items if i.key == "accuracy")
    assert accuracy.score == 1
    assert ballot.capped == ["accuracy"]
    assert all(i.score == 2 for i in ballot.items if i.key != "accuracy")


def test_a_ballot_with_no_corrections_is_not_capped():
    assert investigate.parse_ballot(ballot_json()).capped == []


def test_an_accuracy_score_already_below_two_is_left_alone():
    correction = {"claim": "a", "correction": "b", "source": "c"}
    ballot = investigate.parse_ballot(ballot_json({"accuracy": 0}, corrections=[correction]))
    assert ballot.items[0].score == 0
    assert ballot.capped == []  # nothing was pulled down


def test_a_correction_with_neither_claim_nor_fix_is_dropped():
    text = ballot_json(corrections=[{"source": "somewhere"}])
    assert investigate.parse_ballot(text).corrections == []


# --- filing ----------------------------------------------------------------


def test_the_folder_list_is_the_seed_plus_what_is_on_disk(files):
    (files / "immigration").mkdir(parents=True)
    (files / "a folder I made").mkdir()

    got = investigate.known_folders(files, ["immigration", "health policy"])
    assert got == ["immigration", "health policy", "a folder I made"]


def test_folders_differing_only_in_case_are_one_entry(files):
    files.mkdir(parents=True)
    (files / "Immigration").mkdir()
    assert investigate.known_folders(files, ["immigration"]) == ["immigration"]


def test_a_folder_splits_once_it_is_bulky(files):
    folder = files / investigate.slug("national security and defense")
    folder.mkdir(parents=True)
    for n in range(4):
        (folder / f"topic-{n}.md").write_text("x")

    assert not investigate.needs_splitting(files, "national security and defense", 5)
    (folder / "topic-4.md").write_text("x")
    assert investigate.needs_splitting(files, "national security and defense", 5)


def test_files_already_in_sub_folders_do_not_count_toward_the_split(files):
    """A folder that has already been split is not still overflowing."""
    folder = files / "immigration"
    (folder / "asylum").mkdir(parents=True)
    (folder / "one.md").write_text("x")
    for n in range(10):
        (folder / "asylum" / f"deep-{n}.md").write_text("x")

    assert not investigate.needs_splitting(files, "immigration", 5)


def test_filing_keeps_the_filename_it_already_had(files):
    """It was chosen when the topic was opened and the `path` column has been
    pointing at it ever since; renaming here would be a rename for nothing."""
    current = files / "hormuz-shipping-insurance-rates.md"
    got = investigate.filed_path(files, "Foreign Policy and Diplomacy", None, current)
    assert got == files / "foreign-policy-and-diplomacy" / "hormuz-shipping-insurance-rates.md"


def test_filing_into_a_sub_folder_nests_both_slugs(files):
    got = investigate.filed_path(files, "national security", "the middle east", files / "note.md")
    assert got == files / "national-security" / "the-middle-east" / "note.md"


def test_moving_never_clobbers_an_existing_write_up(files):
    target_dir = files / "immigration"
    target_dir.mkdir(parents=True)
    (target_dir / "note.md").write_text("an afternoon that was already there")
    source = files / "note.md"
    source.write_text("mine")

    moved = investigate.move_write_up(source, target_dir / "note.md")

    assert moved.name == "note-2.md"
    assert moved.read_text() == "mine"
    assert (target_dir / "note.md").read_text() == "an afternoon that was already there"


def test_moving_a_file_onto_itself_does_nothing(files):
    files.mkdir(parents=True)
    path = files / "note.md"
    path.write_text("x")
    assert investigate.move_write_up(path, path) == path
    assert path.read_text() == "x"


# --- grading, end to end ---------------------------------------------------
#
# The filing call is the only paid thing in the feature, so it is stubbed here
# rather than skipped: what matters is the order the endpoint does things in,
# and a real call would only make that slower to observe.


@pytest.fixture
def filed_as(monkeypatch):
    """Stub the folder classifier, and record what it was asked."""
    asked = []

    def stub(settings, date, topic, folders):
        asked.append((topic, list(folders)))
        # First call files the write-up; a second is the sub-folder question.
        return ("national security and defense", True) if len(asked) == 1 else ("ukraine", False)

    monkeypatch.setattr("dashboard.content.file_investigation", stub)
    return asked


def written(client, settings, topic="Hormuz shipping insurance rates", text="Lloyd's tripled.\n"):
    """Draw a topic, open its file, write in it, and hand it in."""
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": topic})
    client.post("/api/investigate/draw", json={"date": TODAY.isoformat()})
    panel = client.post("/api/investigate/open", json={"date": TODAY.isoformat()}).json()
    path = settings.path("investigations") / panel["today"]["path"]
    path.write_text(path.read_text() + text)
    return client.post("/api/investigate/read", json={"date": TODAY.isoformat()}).json()


def test_the_prompt_appears_only_once_something_is_read_back(client, settings):
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": "Hormuz rates"})
    drawn = client.post("/api/investigate/draw", json={"date": TODAY.isoformat()}).json()
    assert drawn["prompt"] is None

    panel = written(client, settings, topic="Hormuz rates")
    assert panel["prompt"] is not None


def test_the_prompt_quotes_the_snapshot_and_the_rubric(client, settings):
    panel = written(client, settings)

    assert "Lloyd's tripled." in panel["prompt"]
    assert "Hormuz shipping insurance rates" in panel["prompt"]
    for line in investigate.RUBRIC:
        assert line.key in panel["prompt"]


def test_the_prompt_does_not_follow_the_file_after_it_is_read_back(client, settings):
    """What you hand the grader has to be what the grade is recorded against,
    and the file goes on being editable in a terminal the app cannot see."""
    panel = written(client, settings)
    path = settings.path("investigations") / panel["today"]["path"]
    path.write_text("# Hormuz shipping insurance rates\n\nCompletely different.\n")

    after = client.get("/api/investigate", params={"date": TODAY.isoformat()}).json()
    assert "Lloyd's tripled." in after["prompt"]
    assert "Completely different." not in after["prompt"]


def test_grading_scores_files_and_spends_the_topic(client, settings, filed_as):
    written(client, settings)
    got = client.post(
        "/api/investigate/ballot",
        json={"date": TODAY.isoformat(), "text": ballot_json({"sourcing": 2, "accuracy": 2,
                                                              "depth": 1, "reasoning": 1,
                                                              "calibration": 0})},
    ).json()

    assert got["day"]["ballot"] is not None
    assert got["today"]["folder"] == "national security and defense"
    # 6/10 against a 1500 anchor from a 1400 seed: a win, so the rating rises.
    assert got["delta"] > 0
    # Spent at last: this is the only place `used_on` is ever set.
    assert got["pool_size"] == 0
    assert got["investigated"] == 1


def test_grading_moves_the_file_into_its_folder(client, settings, filed_as):
    before = written(client, settings)
    old = settings.path("investigations") / before["today"]["path"]
    assert old.exists()

    got = client.post(
        "/api/investigate/ballot",
        json={"date": TODAY.isoformat(), "text": ballot_json()},
    ).json()

    new = settings.path("investigations") / got["today"]["path"]
    assert got["today"]["path"].startswith("national-security-and-defense/")
    assert new.exists() and not old.exists()
    assert "Lloyd's tripled." in new.read_text()


def test_the_classifier_is_offered_the_seeded_folders(client, settings, filed_as):
    written(client, settings)
    client.post(
        "/api/investigate/ballot", json={"date": TODAY.isoformat(), "text": ballot_json()}
    )

    topic, offered = filed_as[0]
    assert topic == "Hormuz shipping insurance rates"
    assert "immigration" in offered  # straight off config.toml


def test_a_bulky_folder_gets_a_sub_folder_and_a_second_call(client, settings, filed_as):
    root = settings.path("investigations")
    bulky = root / investigate.slug("national security and defense")
    bulky.mkdir(parents=True)
    for n in range(settings.investigate.split_at):
        (bulky / f"old-{n}.md").write_text("x")

    written(client, settings)
    got = client.post(
        "/api/investigate/ballot", json={"date": TODAY.isoformat(), "text": ballot_json()}
    ).json()

    assert len(filed_as) == 2  # the folder, then the sub-folder
    assert got["today"]["path"].startswith("national-security-and-defense/ukraine/")
    # Nothing that was already there moved.
    assert len(list(bulky.glob("old-*.md"))) == settings.investigate.split_at


def test_grading_twice_is_refused_rather_than_rated_twice(client, settings, filed_as):
    written(client, settings)
    body = {"date": TODAY.isoformat(), "text": ballot_json()}
    client.post("/api/investigate/ballot", json=body)
    again = client.post("/api/investigate/ballot", json=body)

    assert again.status_code == 409
    assert "already graded" in again.json()["error"]


def test_grading_with_nothing_read_back_is_refused(client, settings, filed_as):
    client.post("/api/investigate/add", json={"date": TODAY.isoformat(), "text": "Hormuz rates"})
    client.post("/api/investigate/draw", json={"date": TODAY.isoformat()})

    got = client.post(
        "/api/investigate/ballot", json={"date": TODAY.isoformat(), "text": ballot_json()}
    )
    assert got.status_code == 409


def test_an_unparseable_paste_costs_nothing(client, settings, filed_as):
    """Parsing happens before the classification and before the transaction, so
    a bad paste must leave the day exactly where it was."""
    written(client, settings)
    got = client.post(
        "/api/investigate/ballot",
        json={"date": TODAY.isoformat(), "text": "Great work, honestly."},
    )

    assert got.status_code == 400  # a paste that went wrong, not a server fault
    assert filed_as == []  # nothing was paid for
    after = client.get("/api/investigate", params={"date": TODAY.isoformat()}).json()
    assert after["day"]["ballot"] is None
    assert after["pool_size"] == 1  # still spendable


def test_the_folder_becomes_a_rating_bucket(client, settings, filed_as):
    written(client, settings)
    got = client.post(
        "/api/investigate/ballot", json={"date": TODAY.isoformat(), "text": ballot_json()}
    ).json()

    assert [f["area"] for f in got["folders"]] == ["national security and defense"]
    assert got["folders"][0]["events"] == 1
