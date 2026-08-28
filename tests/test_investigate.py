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


def test_the_panel_reports_nothing_drawn_before_the_button(repo):
    add(repo, "Hormuz", "Sahel")
    panel = state.investigate_panel(repo, TODAY)

    assert panel.today is None
    assert panel.drawn_on is None
    assert panel.pool_size == 2


def test_the_panel_never_draws(repo):
    """Rendering the tab must not spend a topic -- the rule the theorem draw
    and the debate session both hold."""
    add(repo, "Hormuz")
    state.investigate_panel(repo, TODAY)
    assert repo.investigation_day(TODAY) is None


def test_the_panel_carries_the_drawn_topic(repo):
    add(repo, "Taiwan strait transit rights")
    repo.draw_investigation(TODAY)

    panel = state.investigate_panel(repo, TODAY)
    assert panel.today.topic == "Taiwan strait transit rights"
    assert panel.drawn_on == TODAY
    # Still in the pool, and the panel says both things at once without
    # contradicting itself.
    assert panel.pool_size == 1


def test_a_topic_struck_off_after_being_drawn_is_still_the_days_topic(repo):
    """Drawn in the morning, struck off in the evening. The day did draw it,
    and rewriting that after the fact would make the panel disagree with its
    own history."""
    (item,) = add(repo, "Hormuz")
    repo.draw_investigation(TODAY)
    repo.clear_investigation(item.id, TODAY)

    panel = state.investigate_panel(repo, TODAY)
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
