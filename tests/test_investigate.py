"""The investigation pool: the paste, the draw, and what a wasted day costs.

The rule worth most of this file is that a drawn topic is *not* spent. Every
other daily cell in this app claims its subject on the way in -- the theorem
draw, the debate session, the math problem -- and each needed a reconciliation
sweep afterwards to undo the claim when the day went unanswered. This one
claims the day and leaves the topic alone, so "returns to the pool" is a thing
the schema does rather than a thing a job has to remember to do at midnight.
"""

from __future__ import annotations

import datetime as dt
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
    and the debate session both hold."""
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
