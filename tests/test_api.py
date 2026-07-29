"""The HTTP surface, over a TestClient against a temp database.

Nothing here reaches the network or the LLM: the two endpoints that would
(`/api/math/new`, `/api/word/today`) are stubbed at the `content` module.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard import content, feeds
from dashboard.models import domain as d

from .conftest import TODAY

DATE = TODAY.isoformat()


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """The client supplies the date, so the tests can pick one and the server
    must honour it — including when it is not the server's own today."""
    return DATE


# --- boot ------------------------------------------------------------------


def test_config_reports_the_topic_and_tickers(client, settings):
    body = client.get("/api/config", params={"date": DATE}).json()
    assert body["topic"] == settings.math.topic_for(TODAY)
    assert body["tickers"] == settings.markets.tickers


def test_config_requires_a_date_because_the_topic_rotates(client):
    response = client.get("/api/config")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "date"]


def test_the_topic_follows_the_browsers_week(client, settings):
    """Two requests a week apart get different topics from the same server."""
    next_week = (TODAY + dt.timedelta(days=7)).isoformat()
    this = client.get("/api/config", params={"date": DATE}).json()["topic"]
    later = client.get("/api/config", params={"date": next_week}).json()["topic"]
    assert this != later
    assert this == settings.math.rotation[0]


def test_state_requires_a_date(client):
    """The browser is the authority on "today"; the server must not guess."""
    response = client.get("/api/state")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "date"]


def test_state_on_an_empty_database_is_a_full_payload(client):
    body = client.get("/api/state", params={"date": DATE}).json()
    assert body["date"] == DATE
    assert body["math"]["summary"]["rating"] == 1500
    assert body["chess"]["summary"]["rating"] == 1400
    assert body["math"]["problem"] is None
    assert body["games"]["wordle_streak"] == 0
    assert body["health"] == [
        {"metric": "weight", "points": []},
        {"metric": "sleep", "points": []},
    ]


def test_the_server_honours_a_date_that_is_not_its_own_today(client):
    other = (TODAY - dt.timedelta(days=400)).isoformat()
    assert client.get("/api/state", params={"date": other}).json()["date"] == other


# --- deleted endpoints -----------------------------------------------------


@pytest.mark.parametrize("path", ["/api/state", "/api/state/merge"])
def test_the_whole_state_write_endpoints_are_gone(client, path):
    """`POST /api/state` full-replaced the world from whatever a tab held.
    `/api/state/merge` went with it — per-column UPDATE leaves it nothing."""
    response = client.post(path, json={})
    assert response.status_code == 404
    assert "no such endpoint" in response.json()["error"]


def test_an_unknown_api_path_answers_json_not_html(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"].startswith("no such endpoint")


def test_the_vendored_board_is_not_served(client):
    assert client.get("/chess.js").status_code == 404


def test_the_front_end_is_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/app/main.js").status_code == 200
    assert client.get("/vendor/katex/katex.min.css").status_code == 200


@pytest.mark.parametrize("path", ["/", "/app/main.js", "/style.css", "/data/piano.json"])
def test_app_files_must_be_revalidated(client, path):
    """Starlette sends ETag and Last-Modified but no Cache-Control, so a
    browser applies heuristic freshness and serves an edited module from
    cache without asking. That looks exactly like the edit not working."""
    assert client.get(path).headers["cache-control"] == "no-cache"


def test_vendored_assets_are_cached_hard(client):
    """Version-pinned and never edited in place — including 20 font files."""
    cache_control = client.get("/vendor/katex/katex.min.css").headers["cache-control"]
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control


# --- math ------------------------------------------------------------------


def test_hint_and_finish_move_the_rating(client, repo, problem):
    repo.open_math_attempt(TODAY, problem.id)

    panel = client.post("/api/math/hint", json={"date": DATE}).json()
    assert panel["hints_revealed"] == 1
    assert panel["hints_visible"] == [problem.hints[0]]

    panel = client.post("/api/math/finish", json={"date": DATE, "outcome": "solved"}).json()
    assert panel["status"] == "solved"
    assert panel["score"] == pytest.approx(0.7)
    assert panel["summary"]["rating"] != 1500


def test_unrevealed_hints_are_never_sent_to_the_browser(client, repo, problem):
    """The old client received all three and merely declined to render them."""
    repo.open_math_attempt(TODAY, problem.id)
    panel = client.get("/api/state", params={"date": DATE}).json()["math"]
    assert panel["hints_visible"] == []
    assert panel["problem"]["hints"] == problem.hints  # still available to the panel


def test_a_second_finish_is_a_conflict_not_a_second_rating(client, repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    client.post("/api/math/finish", json={"date": DATE, "outcome": "solved"})
    response = client.post("/api/math/finish", json={"date": DATE, "outcome": "solved"})
    assert response.status_code == 409


def test_a_hint_with_no_attempt_is_a_404(client):
    response = client.post("/api/math/hint", json={"date": DATE})
    assert response.status_code == 404


def test_an_unknown_outcome_is_rejected_by_the_model(client, repo, problem):
    repo.open_math_attempt(TODAY, problem.id)
    assert (
        client.post("/api/math/finish", json={"date": DATE, "outcome": "kinda"}).status_code == 422
    )


def test_math_new_streams_status_lines_then_the_problem(client, monkeypatch, repo):
    def fake(settings, repository, date, on_status=None):
        on_status("thinking about a problem…")
        on_status("verifying against AoPS… (search 1)")
        stored = repository.add_problem(
            d.MathProblem(
                source=f"Stub {date}",
                tier="aime-late",
                anchor=1500,
                statement="$1+1$?",
                answer="2",
                hints=["a", "b", "c"],
                model="stub",
                generated_at=dt.datetime(2026, 7, 26, 9, 0),
            )
        )
        repository.open_math_attempt(date, stored.id)
        return stored

    monkeypatch.setattr(content, "next_problem", fake)
    lines = [
        line for line in client.post("/api/math/new", json={"date": DATE}).text.splitlines() if line
    ]
    assert len(lines) == 3
    assert '"status"' in lines[0]
    assert '"problem"' in lines[-1]
    assert (
        client.get("/api/state", params={"date": DATE}).json()["math"]["problem"]["answer"] == "2"
    )


def test_a_failed_generation_ends_the_stream_with_an_error(client, monkeypatch):
    def boom(*a, **k):
        raise content.ContentError("the model returned a problem you have already seen")

    monkeypatch.setattr(content, "next_problem", boom)
    lines = [
        line for line in client.post("/api/math/new", json={"date": DATE}).text.splitlines() if line
    ]
    assert '"error"' in lines[-1]
    assert "already seen" in lines[-1]


# --- chess -----------------------------------------------------------------


def test_chess_new_stores_the_puzzle_and_opens_an_attempt(client, monkeypatch):
    monkeypatch.setattr(
        feeds.Feeds,
        "chess_puzzle",
        lambda self, rating: d.ChessPuzzle(
            id="zzz9", rating=1490, themes=["pin"], url="https://lichess.org/training/zzz9"
        ),
    )
    panel = client.post("/api/chess/new", json={"date": DATE}).json()
    assert panel["puzzle_id"] == "zzz9"
    assert panel["puzzle_rating"] == 1490
    assert panel["status"] == "open"


@pytest.mark.parametrize(("outcome", "score"), [("clean", 1.0), ("messy", 0.4), ("failed", 0.0)])
def test_the_three_self_report_buttons(client, repo, puzzle, outcome, score):
    repo.open_chess_attempt(TODAY, puzzle.id)
    panel = client.post("/api/chess/finish", json={"date": DATE, "outcome": outcome}).json()
    assert panel["score"] == pytest.approx(score)
    assert panel["status"] == "done"


# --- word ------------------------------------------------------------------


def test_word_today_is_paid_for_once(client, monkeypatch, repo):
    calls = []

    def fake(settings, date):
        calls.append(date)
        return d.WordOfDay(date=date, word="susurrus", pos="noun", definition="a whisper")

    monkeypatch.setattr(content, "word_of_day", fake)
    first = client.post("/api/word/today", json={"date": DATE}).json()
    second = client.post("/api/word/today", json={"date": DATE}).json()
    assert first["word"] == second["word"] == "susurrus"
    assert len(calls) == 1  # the second call read the row instead of generating


def test_the_need_all_five_rule_lives_in_the_contract(client):
    """Previously a short list was a 500 from a client-side `if` being
    skipped."""
    response = client.post(
        "/api/word/check", json={"date": DATE, "word": "x", "sentences": ["a", "b"]}
    )
    assert response.status_code == 422


def test_sentences_autosave_without_grading(client, repo):
    repo.upsert_word(d.WordOfDay(date=TODAY, word="w", pos="n", definition="d"))
    body = client.post(
        "/api/word/sentences", json={"date": DATE, "sentences": ["a", "b", "c"]}
    ).json()
    assert body["sentences"] == ["a", "b", "c"]
    assert body["grade"] is None


# --- games -----------------------------------------------------------------


def test_logging_wordle_returns_the_refreshed_panel(client):
    panel = client.post(
        "/api/games/wordle",
        json={"date": DATE, "puzzle_no": 1861, "guesses": 4, "won": True, "grid": ["⬛🟨⬛⬛⬛"]},
    ).json()
    assert panel["wordle_today"]["puzzle_no"] == 1861
    assert panel["wordle_streak"] == 1
    assert panel["wordle_average"] == 4.0


def test_a_grid_row_limit_is_enforced(client):
    response = client.post(
        "/api/games/wordle",
        json={"date": DATE, "guesses": 4, "won": True, "grid": ["x"] * 7},
    )
    assert response.status_code == 422


def test_pokedoku_correct_is_bounded(client):
    assert (
        client.post(
            "/api/games/pokedoku", json={"date": DATE, "correct": 12, "grid": []}
        ).status_code
        == 422
    )


# --- papers ----------------------------------------------------------------


def test_the_days_paper_is_surfaced_once(client, monkeypatch):
    calls = []

    def fake(self, date):
        calls.append(date)
        return feeds.ArxivEntry(
            title="A Paper", link="http://arxiv.org/abs/1", summary="s", published=TODAY
        )

    monkeypatch.setattr(feeds.Feeds, "paper", fake)
    first = client.get("/api/papers/today", params={"date": DATE}).json()
    second = client.get("/api/papers/today", params={"date": DATE}).json()
    assert first["surfaced"]["title"] == "A Paper"
    assert second["surfaced"]["id"] == first["surfaced"]["id"]
    assert len(calls) == 1


def test_triage_moves_the_paper_into_the_queue(client, repo):
    repo.add_paper(
        d.Paper(
            title="P",
            link="https://arxiv.org/abs/1",
            status="surfaced",
            surfaced_on=TODAY,
            added=TODAY,
        )
    )
    panel = client.post("/api/paper/triage", json={"date": DATE, "mark": "queued"}).json()
    assert panel["surfaced"]["status"] == "queued"
    assert [p["title"] for p in panel["queue"]] == ["P"]


def test_a_javascript_url_cannot_reach_the_queue(client):
    """Was a free-form string interpolated into an href; `javascript:` in the
    paste box executed. It now fails validation at the request model."""
    response = client.post("/api/paper/queue", json={"date": DATE, "url": "javascript:alert(1)"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "url_scheme"


def test_queueing_a_link_derives_a_title(client):
    panel = client.post(
        "/api/paper/queue", json={"date": DATE, "url": "https://arxiv.org/abs/2401.00001"}
    ).json()
    assert panel["queue"][0]["title"] == "arxiv.org/abs/2401.00001"


def test_promoting_reorders_the_queue(client):
    for i in range(2):
        client.post("/api/paper/queue", json={"date": DATE, "url": f"https://a.example/{i}"})
    queue = client.get("/api/state", params={"date": DATE}).json()["papers"]["queue"]
    panel = client.post(
        "/api/paper/queue/action",
        json={"date": DATE, "paper_id": queue[1]["id"], "action": "up"},
    ).json()
    assert [p["id"] for p in panel["queue"]] == [queue[1]["id"], queue[0]["id"]]


def test_an_unknown_queue_action_is_rejected(client):
    assert (
        client.post(
            "/api/paper/queue/action", json={"date": DATE, "paper_id": 1, "action": "delete"}
        ).status_code
        == 422
    )


# --- theorems --------------------------------------------------------------


def _card(repo, name: str, added: dt.date = TODAY) -> d.TheoremCard:
    return repo.add_card(
        d.TheoremCard(name=name, statement=f"the statement of {name}", topic="gf", added=added)
    )


def test_an_empty_deck_draws_nothing_rather_than_failing(client):
    panel = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert panel["today"] is None
    assert panel["deck_size"] == 0


def test_the_day_draws_one_theorem_and_keeps_it_across_reloads(client, repo):
    for n in "abcdef":
        _card(repo, n)
    first = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert first["today"] is not None
    for _ in range(5):
        again = client.get("/api/theorem/today", params={"date": DATE}).json()
        assert again["today"]["id"] == first["today"]["id"]


def test_a_never_seen_card_goes_before_any_card_with_history(client, repo):
    """You added it because you just met it; it should not wait on a die."""
    old = _card(repo, "seen once")
    repo.record_draw(TODAY - dt.timedelta(days=40), old.id)
    fresh = _card(repo, "vandermonde")

    panel = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert panel["today"]["id"] == fresh.id


def test_yesterdays_theorem_cannot_be_todays(client, repo):
    a, b = _card(repo, "a"), _card(repo, "b")
    repo.record_draw(TODAY - dt.timedelta(days=1), a.id)
    repo.record_draw(TODAY - dt.timedelta(days=30), b.id)

    panel = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert panel["today"]["id"] == b.id


def test_a_lone_card_on_cooldown_is_drawn_anyway(client, repo):
    """The all-zero fallback, end to end: a deck of one has no other answer."""
    only = _card(repo, "only")
    repo.record_draw(TODAY - dt.timedelta(days=1), only.id)

    panel = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert panel["today"]["id"] == only.id


def test_an_archived_card_is_never_drawn(client, repo):
    retired = _card(repo, "retired")
    keep = _card(repo, "keep")
    client.post("/api/theorem/archive", json={"date": DATE, "card_id": retired.id})

    panel = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert panel["today"]["id"] == keep.id
    assert panel["deck_size"] == 1
    assert len(panel["deck"]) == 2  # archived cards stay visible in the deck list


def test_the_self_check_records_and_is_reported_back(client, repo):
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})

    assert client.get("/api/state", params={"date": DATE}).json()["theorems"]["recalled"] is None
    panel = client.post("/api/theorem/recall", json={"date": DATE, "recalled": False}).json()
    assert panel["recalled"] is False
    assert panel["deck"][0]["seen"] == 1
    assert panel["deck"][0]["recalled"] == 0


def test_checking_recall_before_anything_is_drawn_is_a_404(client):
    assert (
        client.post("/api/theorem/recall", json={"date": DATE, "recalled": True}).status_code == 404
    )


def test_archiving_a_missing_card_is_a_404(client):
    assert (
        client.post("/api/theorem/archive", json={"date": DATE, "card_id": 999}).status_code == 404
    )


def test_the_boot_payload_never_draws(client, repo):
    """A day you did not open is a day no theorem appeared — so reading the
    state must not age the deck."""
    _card(repo, "a")
    assert client.get("/api/state", params={"date": DATE}).json()["theorems"]["today"] is None
    assert repo.draw(TODAY) is None


def test_adding_by_name_stores_what_the_model_resolved(client, repo, monkeypatch):
    monkeypatch.setattr(
        content,
        "theorem_from_prompt",
        lambda cfg, date, prompt: d.TheoremCard(
            name="Vandermonde's Identity",
            statement=r"$\binom{m+n}{k}=\sum_j\binom{m}{j}\binom{n}{k-j}$",
            topic="combinatorics",
            added=date,
        ),
    )
    panel = client.post("/api/theorem/add", json={"date": DATE, "prompt": "vandermonde's"}).json()
    assert panel["deck_size"] == 1
    assert panel["deck"][0]["card"]["name"] == "Vandermonde's Identity"
    assert panel["deck"][0]["days_since"] is None


def test_an_empty_add_prompt_is_rejected(client):
    assert client.post("/api/theorem/add", json={"date": DATE, "prompt": " "}).status_code == 422


# --- health / log / completion ---------------------------------------------


def test_health_upserts_and_returns_both_series(client):
    client.post("/api/health", json={"date": DATE, "metric": "weight", "value": 172.4})
    series = client.post(
        "/api/health", json={"date": DATE, "metric": "weight", "value": 171.9}
    ).json()
    weight = next(s for s in series if s["metric"] == "weight")
    assert weight["points"] == [[DATE, 171.9]]


def test_a_non_positive_weight_is_rejected(client):
    assert (
        client.post("/api/health", json={"date": DATE, "metric": "weight", "value": 0}).status_code
        == 422
    )


def test_completion_computes_pct_rather_than_storing_it(client):
    body = client.post("/api/completion", json={"date": DATE, "done": 3, "total": 8}).json()
    assert body["pct"] == pytest.approx(0.375)


def test_completion_rejects_more_done_than_total(client):
    """A 422 naming the fields, not a 500 from the table's CHECK."""
    response = client.post("/api/completion", json={"date": DATE, "done": 9, "total": 8})
    assert response.status_code == 422
    assert "cannot exceed total" in response.text


def test_the_daylog_round_trips(client):
    client.post("/api/daylog", json={"date": DATE, "text": "shipped the rewrite"})
    assert client.get("/api/state", params={"date": DATE}).json()["daylog"] == "shipped the rewrite"


def test_calendar_add_takes_no_date(client, monkeypatch):
    """The one body with no `date`: gcalcli parses the day out of the text."""
    monkeypatch.setattr(feeds.Feeds, "add_calendar_event", lambda self, text: f"added {text}")
    assert client.post("/api/calendar/add", json={"text": "lunch friday"}).json() == {
        "out": "added lunch friday"
    }
    assert (
        client.post("/api/calendar/add", json={"text": "x", "date": DATE}).status_code == 422
    )  # extra="forbid"


# --- error mapping ---------------------------------------------------------


def test_a_feed_outage_is_a_503_with_a_readable_message(client, monkeypatch):
    def boom(self):
        raise feeds.FeedError("all news feeds failed")

    monkeypatch.setattr(feeds.Feeds, "news", boom)
    response = client.get("/api/news")
    assert response.status_code == 503
    assert response.json()["error"] == "all news feeds failed"


def test_an_llm_failure_is_a_502(client, monkeypatch):
    from dashboard.llm import LlmError

    def boom(*a, **k):
        raise LlmError("ANTHROPIC_API_KEY missing from .env")

    monkeypatch.setattr(content, "word_of_day", boom)
    response = client.post("/api/word/today", json={"date": DATE})
    assert response.status_code == 502
    assert "ANTHROPIC_API_KEY" in response.json()["error"]


def test_an_unexpected_error_does_not_leak_internals(client, monkeypatch):
    def boom(*a, **k):
        raise TypeError("something about a NoneType")

    monkeypatch.setattr(content, "topic_info", boom)
    response = client.get("/api/topic", params={"date": DATE})
    assert response.status_code == 500
    assert response.json() == {"error": "internal error — see the server log"}
