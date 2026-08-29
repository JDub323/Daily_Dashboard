"""The HTTP surface, over a TestClient against a temp database.

Nothing here reaches the network or the LLM: the two endpoints that would
(`/api/math/new`, `/api/word/today`) are stubbed at the `content` module.
"""

from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from dashboard import content, feeds
from dashboard.models import api
from dashboard.models import domain as d
from dashboard.models.common import HealthMetric, Track

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
    assert [s["metric"] for s in body["health"]["series"]] == [m.value for m in HealthMetric]
    assert all(s["points"] == [] for s in body["health"]["series"])
    assert body["health"]["readiness_today"] is None
    assert body["health"]["sessions_today"] == []
    # Four weeks of rest days rather than an empty list — the bar chart's gaps
    # are data, so the zeros are sent rather than inferred.
    assert len(body["health"]["loads"]) == 28
    # Nothing derived says anything on day one, which is the point of it.
    derived = body["health"]["derived"]
    assert derived["acwr"] is None
    assert derived["monotony"] is None
    assert derived["weight_ema"] is None
    assert derived["readiness_baseline"] is None
    assert derived["week_load"] == 0


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


def test_math_new_returns_the_panel_with_the_problem_on_it(client, monkeypatch):
    """It was an ndjson stream while there was a paid generation behind it.
    Both remaining sources are a file read, so it is an ordinary POST and the
    response is the panel — the same shape every other write route returns."""

    def fake(settings, repository, date):
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
    panel = client.post("/api/math/new", json={"date": DATE}).json()
    assert panel["problem"]["answer"] == "2"
    assert panel["status"] == "open"
    assert (
        client.get("/api/state", params={"date": DATE}).json()["math"]["problem"]["answer"] == "2"
    )


def test_no_batch_and_no_pool_is_a_plain_error_not_a_generation(client):
    """The whole point of removing the paid path: a morning the batch did not
    run reports what happened instead of quietly buying a problem. Nothing is
    stubbed here — `content.next_problem` runs for real against an empty
    batches directory and an empty pool, and must not reach `llm`."""
    response = client.post("/api/math/new", json={"date": DATE})
    assert response.status_code >= 400
    assert "batch" in response.json()["error"].lower()


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

    def fake(settings, repo_, date):
        calls.append(date)
        return d.WordOfDay(date=date, word="susurrus", pos="noun", definition="a whisper")

    monkeypatch.setattr(content, "word_of_day", fake)
    first = client.post("/api/word/today", json={"date": DATE}).json()
    second = client.post("/api/word/today", json={"date": DATE}).json()
    assert first["word"] == second["word"] == "susurrus"
    assert len(calls) == 1  # the second call read the row instead of generating


def test_the_need_all_of_them_rule_lives_in_the_contract(client):
    """Previously a short list was a 500 from a client-side `if` being
    skipped.

    Written against `domain.SENTENCES` rather than a literal, because the
    count has changed once (five to two) and this test existing to pin the
    *rule* is the reason it should not also pin the number.
    """
    short = ["a"] * (d.SENTENCES - 1)
    long = ["a"] * (d.SENTENCES + 1)
    for sentences in (short, long):
        response = client.post(
            "/api/word/check", json={"date": DATE, "word": "x", "sentences": sentences}
        )
        assert response.status_code == 422, sentences


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


# --- news dedupe -----------------------------------------------------------


CATS = ["uspolitics", "world", "finance", "other"]


def _cached(value):
    """Wrap a stub's return value the way the real cache would.

    The fetchers hand back a `Cached` now, not a bare list: the panel has to be
    able to say when the data was fetched and whether the source answered.
    """
    return feeds.Cached(value, time.time(), 60)


def _headlines(client, date):
    """GET /api/news, unwrapped — the response is an envelope now."""
    return client.get("/api/news", params={"date": date}).json()["items"]


def _serve(monkeypatch, *links):
    """Stub `Feeds.news`, not `http_get`.

    The fetch is memoised for `cache_seconds`, so patching the transport only
    reaches the first call in a test and every later one silently re-reads the
    first response -- which looks exactly like the filter failing.
    """
    items = [
        feeds.NewsItem(
            title=f"Story {i}", link=link, cat=CATS[i % len(CATS)], key=feeds.normalize_link(link)
        )
        for i, link in enumerate(links)
    ]
    monkeypatch.setattr(feeds.Feeds, "news", lambda self: _cached(items))


def test_a_headline_shown_today_is_gone_tomorrow(client, monkeypatch):
    """The dominant duplicate: an RSS feed carries a story for days, so the
    same link came back every morning until you stopped opening the panel."""
    # More stories than one day's quotas can take, so tomorrow has somewhere
    # to go. A feed offering only what you have already read is the separate
    # exhausted case below.
    _serve(monkeypatch, *[f"https://ex.com/{n}" for n in range(8)])
    first = _headlines(client, DATE)
    assert first

    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    second = _headlines(client, tomorrow)
    assert second
    assert not ({i["key"] for i in first} & {i["key"] for i in second})


def test_the_same_day_reload_is_stable(client, monkeypatch):
    """Refreshing must not swap your headlines.

    Showing a story records it, so a filter that looked at everything seen
    "in the last 30 days" would exclude what it had just displayed and serve
    the *next* five instead -- glance at the panel, reload for the markets,
    and the news is gone. Caught live; the pool has to exceed one day's
    quotas for a test to see it at all.
    """
    _serve(monkeypatch, *[f"https://ex.com/{n}" for n in range(8)])
    first = _headlines(client, DATE)
    second = _headlines(client, DATE)
    third = _headlines(client, DATE)
    assert [i["key"] for i in first] == [i["key"] for i in second] == [i["key"] for i in third]


def test_a_tracking_parameter_does_not_make_a_story_new(client, monkeypatch):
    _serve(monkeypatch, "https://ex.com/a")
    client.get("/api/news", params={"date": DATE})

    # Tomorrow the feed re-lists the same story with a campaign tag, plus one
    # genuinely new item. Only the new one should come back.
    _serve(monkeypatch, "https://ex.com/a?utm_source=rss", "https://ex.com/b")
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    again = _headlines(client, tomorrow)
    assert {i["key"] for i in again} == {"https://ex.com/b"}


def test_an_exhausted_feed_shows_repeats_rather_than_nothing(client, monkeypatch):
    """Stale feeds are the usual cause, and a blank panel reads as a fetch
    failure rather than as "you have read everything"."""
    _serve(monkeypatch, "https://ex.com/only")
    assert _headlines(client, DATE)
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    assert _headlines(client, tomorrow)


def test_news_requires_a_date(client):
    assert client.get("/api/news").status_code == 422


def test_links_outside_the_window_are_pruned(client, repo, monkeypatch, settings):
    """A link that stops being offered eventually leaves the table.

    The second fetch serves a *different* story on purpose: showing the same
    one again refreshes `last_seen`, which is correct -- you saw it again --
    and would keep it alive forever, which is not what pruning is for.
    """
    _serve(monkeypatch, "https://ex.com/old")
    client.get("/api/news", params={"date": DATE})
    assert "https://ex.com/old" in repo.seen_links(TODAY - dt.timedelta(days=365))

    _serve(monkeypatch, "https://ex.com/new")
    beyond = (TODAY + dt.timedelta(days=settings.news.repeat_window_days + 1)).isoformat()
    client.get("/api/news", params={"date": beyond})

    remaining = repo.seen_links(TODAY - dt.timedelta(days=365))
    assert "https://ex.com/old" not in remaining
    assert "https://ex.com/new" in remaining


# --- papers ----------------------------------------------------------------


def test_the_days_paper_is_surfaced_once(client, monkeypatch):
    calls = []

    def fake(self, date):
        calls.append(date)
        return _cached(
            [
                feeds.ArxivEntry(
                    title="A Paper", link="http://arxiv.org/abs/1", summary="s", published=TODAY
                )
            ]
        )

    monkeypatch.setattr(feeds.Feeds, "papers", fake)
    first = client.get("/api/papers/today", params={"date": DATE}).json()
    second = client.get("/api/papers/today", params={"date": DATE}).json()
    assert first["surfaced"]["title"] == "A Paper"
    assert second["surfaced"]["id"] == first["surfaced"]["id"]
    assert len(calls) == 1


def _entries(*titles):
    return _cached(
        [
            feeds.ArxivEntry(
                title=t, link=f"http://arxiv.org/abs/{i}", summary=f"{t} summary", published=TODAY
            )
            for i, t in enumerate(titles)
        ]
    )


def test_a_paper_already_surfaced_is_not_surfaced_again(client, monkeypatch):
    """The observed bug: on a day arXiv had nothing new, the top entry was
    yesterday's, and it was surfaced twice and marked read twice."""
    monkeypatch.setattr(feeds.Feeds, "papers", lambda self, date: _entries("Old", "Newer"))
    first = client.get("/api/papers/today", params={"date": DATE}).json()

    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    second = client.get("/api/papers/today", params={"date": tomorrow}).json()
    assert second["surfaced"]["link"] != first["surfaced"]["link"]


def test_all_candidates_seen_surfaces_nothing_rather_than_repeating(client, monkeypatch):
    """Re-showing a paper you have already triaged is the exact complaint, so
    the honest answer on an exhausted feed is an empty slot, not a repeat."""
    monkeypatch.setattr(feeds.Feeds, "papers", lambda self, date: _entries("Only"))
    client.get("/api/papers/today", params={"date": DATE})
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    panel = client.get("/api/papers/today", params={"date": tomorrow}).json()
    assert panel["surfaced"] is None
    assert panel["queue"] == []


def test_interests_choose_the_candidate(client, monkeypatch, settings):
    """`interests.md` outranks arXiv's own order without an LLM call."""
    settings.path("interests").write_text(
        "# Interested in\n- widget alignment\n\n# Not interested in\n- federated learning\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        feeds.Feeds,
        "papers",
        lambda self, date: _entries("Federated learning at scale", "Widget alignment methods"),
    )
    panel = client.get("/api/papers/today", params={"date": DATE}).json()
    assert panel["surfaced"]["title"] == "Widget alignment methods"


def test_pasting_a_link_already_in_the_list_is_a_409(client, monkeypatch):
    monkeypatch.setattr(feeds.Feeds, "papers", lambda self, date: _entries("A Paper"))
    client.get("/api/papers/today", params={"date": DATE})
    response = client.post("/api/paper/queue", json={"date": DATE, "url": "http://arxiv.org/abs/0"})
    assert response.status_code == 409
    assert "already in the list" in response.json()["error"]


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


def _card(repo, name: str, added: dt.date = TODAY, topic: str = "combinatorics") -> d.TheoremCard:
    return repo.add_card(
        d.TheoremCard(name=name, statement=f"the statement of {name}", topic=topic, added=added)
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


def _grader(monkeypatch, explained: bool, applied: bool, note: str = "ok"):
    monkeypatch.setattr(
        content,
        "grade_theorem",
        lambda *a, **k: d.TheoremGrade(explained=explained, applied=applied, note=note),
    )


ANSWER = {
    "explanation": "the dot product is at most the product of the norms",
    "application": "the polynomial that agreed with a rational function on [0,1]",
}


def test_a_written_recall_is_graded_and_reported_back(client, repo, monkeypatch):
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})
    _grader(monkeypatch, explained=True, applied=False, note="right, but the problem is vague")

    assert client.get("/api/state", params={"date": DATE}).json()["theorems"]["recalled"] is None
    panel = client.post("/api/theorem/recall", json={"date": DATE, **ANSWER}).json()

    # `recalled` tracks the theorem half, so the deck's tally goes on counting
    # what it always counted.
    assert panel["recalled"] is True
    assert panel["written"]["explained"] is True
    assert panel["written"]["applied"] is False
    assert panel["written"]["note"] == "right, but the problem is vague"
    assert panel["deck"][0]["seen"] == 1
    assert panel["deck"][0]["recalled"] == 1


@pytest.mark.parametrize(
    ("explained", "applied", "delta"),
    [(True, True, 3), (True, False, 0), (False, True, 0), (False, False, -3)],
)
def test_a_grade_moves_the_rating_by_exactly_three(
    client, repo, monkeypatch, explained, applied, delta
):
    """Half each, so both halves is +3, one is a draw, neither is -3. The
    number falls out of K=6 against an opponent equal to your own rating."""
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})
    _grader(monkeypatch, explained=explained, applied=applied)

    before = repo.current_rating(Track.math, "combinatorics")
    panel = client.post("/api/theorem/recall", json={"date": DATE, **ANSWER}).json()

    assert panel["delta"] == delta
    assert repo.current_rating(Track.math, "combinatorics") == before + delta


def test_a_recall_does_not_touch_the_pooled_rating_or_its_k(client, repo, monkeypatch):
    """One row, against the area. A pooled row would walk the headline rating
    +3 a day with nothing to pull it back, and count as evidence about problem
    solving, which it is not."""
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})
    _grader(monkeypatch, explained=True, applied=True)

    pooled = repo.current_rating(Track.math)
    n = repo.event_count(Track.math)
    n_area = repo.event_count(Track.math, "combinatorics")

    client.post("/api/theorem/recall", json={"date": DATE, **ANSWER})

    assert repo.current_rating(Track.math) == pooled
    assert repo.event_count(Track.math) == n
    # Not evidence about problem solving in its own area either: left in, one
    # row a day would drive that area's K to the floor within two months.
    assert repo.event_count(Track.math, "combinatorics") == n_area


def test_the_statement_is_not_sent_until_the_recall_is_graded(client, repo, monkeypatch):
    """The answer must not be in the page while the question is being asked.
    The old panel shipped it and trusted the client to hide it."""
    _card(repo, "a")
    drawn = client.get("/api/theorem/today", params={"date": DATE}).json()
    assert drawn["today"]["statement"] == ""

    _grader(monkeypatch, explained=True, applied=True)
    graded = client.post("/api/theorem/recall", json={"date": DATE, **ANSWER}).json()
    assert graded["today"]["statement"] == "the statement of a"


def test_a_second_submission_is_a_conflict_not_a_second_rating(client, repo, monkeypatch):
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})
    _grader(monkeypatch, explained=True, applied=True)

    client.post("/api/theorem/recall", json={"date": DATE, **ANSWER})
    rated = repo.current_rating(Track.math, "combinatorics")

    again = client.post("/api/theorem/recall", json={"date": DATE, **ANSWER})
    assert again.status_code == 409
    assert repo.current_rating(Track.math, "combinatorics") == rated


def test_an_empty_answer_is_rejected_rather_than_graded_as_a_miss(client, repo):
    _card(repo, "a")
    client.get("/api/theorem/today", params={"date": DATE})
    response = client.post(
        "/api/theorem/recall", json={"date": DATE, "explanation": "", "application": ""}
    )
    assert response.status_code == 422


def test_checking_recall_before_anything_is_drawn_is_a_404(client):
    assert client.post("/api/theorem/recall", json={"date": DATE, **ANSWER}).status_code == 404


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


def test_health_upserts_and_returns_the_whole_panel(client):
    client.post("/api/health", json={"date": DATE, "metric": "weight", "value": 172.4})
    body = client.post(
        "/api/health", json={"date": DATE, "metric": "weight", "value": 171.9}
    ).json()
    weight = next(s for s in body["series"] if s["metric"] == "weight")
    assert weight["points"] == [[DATE, 171.9]]


def test_readiness_round_trips_and_scores_out_of_25(client):
    body = client.post(
        "/api/readiness",
        json={
            "date": DATE,
            "sleep_quality": 4,
            "fatigue": 3,
            "soreness": 2,
            "stress": 4,
            "motivation": 5,
        },
    ).json()
    assert body["readiness_today"]["score"] == 18


@pytest.mark.parametrize("field", ["sleep_quality", "fatigue", "soreness", "stress", "motivation"])
def test_a_readiness_answer_outside_one_to_five_is_rejected(client, field):
    payload = {
        "date": DATE,
        "sleep_quality": 3,
        "fatigue": 3,
        "soreness": 3,
        "stress": 3,
        "motivation": 3,
    } | {field: 6}
    assert client.post("/api/readiness", json=payload).status_code == 422


def test_two_sessions_in_a_day_both_stand_and_their_load_sums(client):
    client.post(
        "/api/session", json={"date": DATE, "kind": "roadwork", "rpe": 4, "minutes": 30}
    )
    body = client.post(
        "/api/session", json={"date": DATE, "kind": "sparring", "rpe": 9, "minutes": 45}
    ).json()
    assert [s["load"] for s in body["sessions_today"]] == [120, 405]
    assert body["derived"]["week_load"] == 525


def test_deleting_a_session_removes_it_from_the_load(client):
    added = client.post(
        "/api/session", json={"date": DATE, "kind": "boxing", "rpe": 6, "minutes": 60}
    ).json()
    session_id = added["sessions_today"][0]["id"]
    body = client.post(
        "/api/session/delete", json={"date": DATE, "session_id": session_id}
    ).json()
    assert body["sessions_today"] == []
    assert body["derived"]["week_load"] == 0


def test_deleting_a_session_that_is_gone_is_a_404(client):
    response = client.post("/api/session/delete", json={"date": DATE, "session_id": 999})
    assert response.status_code == 404


def test_a_non_positive_weight_is_rejected(client):
    assert (
        client.post("/api/health", json={"date": DATE, "metric": "weight", "value": 0}).status_code
        == 422
    )


def test_completion_computes_pct_rather_than_storing_it(client):
    body = client.post("/api/completion", json={"date": DATE, "done": 3, "total": 8}).json()
    assert body["pct"] == pytest.approx(0.375)


def test_completion_carries_the_points_from_the_optional_half(client):
    body = client.post(
        "/api/completion", json={"date": DATE, "done": 4, "total": 4, "bonus": 7}
    ).json()
    assert body["bonus"] == 7
    # Points sit beside the ring rather than inside it: 4/4 is still 4/4.
    assert body["pct"] == pytest.approx(1.0)


def test_completion_updates_the_points_alone(client):
    """The ordinary evening: the required four are already in, and every extra
    after that moves nothing but the bonus."""
    client.post("/api/completion", json={"date": DATE, "done": 4, "total": 4, "bonus": 3})
    body = client.post(
        "/api/completion", json={"date": DATE, "done": 4, "total": 4, "bonus": 5}
    ).json()
    assert body["bonus"] == 5


def test_completion_without_points_reads_as_none(client):
    body = client.post("/api/completion", json={"date": DATE, "done": 3, "total": 4}).json()
    assert body["bonus"] == 0


def test_completion_rejects_negative_points(client):
    response = client.post(
        "/api/completion", json={"date": DATE, "done": 4, "total": 4, "bonus": -1}
    )
    assert response.status_code == 422


def test_completion_rejects_more_done_than_total(client):
    """A 422 naming the fields, not a 500 from the table's CHECK."""
    response = client.post("/api/completion", json={"date": DATE, "done": 9, "total": 8})
    assert response.status_code == 422
    assert "cannot exceed total" in response.text


def test_the_daylog_round_trips(client):
    client.post("/api/daylog", json={"date": DATE, "text": "shipped the rewrite"})
    assert client.get("/api/state", params={"date": DATE}).json()["daylog"] == "shipped the rewrite"


def _no_gcalcli(monkeypatch, writable=("Special Events", "Tests", "Homework")):
    """Stub the two gcalcli reads the quick-add path makes, and capture the write."""
    written: dict = {}

    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [api.CalendarInfo(name=n, access="owner") for n in writable],
    )
    monkeypatch.setattr(feeds.Feeds, "calendar_history", lambda self, today: [])

    def _add(self, **kwargs):
        written.update(kwargs)
        return feeds.WrittenEvent(event_id="ev1", url="https://example/e", output="created")

    monkeypatch.setattr(feeds.Feeds, "add_calendar_event", _add)
    return written


def test_calendar_add_takes_no_date(client, monkeypatch):
    """The body carries the browser's date as `today`, never as `date`.

    `date` stays rejected: it was the name on every other dated body, and the
    two meant different things here -- this one is the day the *text* is read
    relative to, not the day the event lands on.
    """
    _no_gcalcli(monkeypatch)
    body = client.post("/api/calendar/add", json={"text": "lunch friday"}).json()
    assert body["written"][0]["title"] == "Lunch"
    assert (
        client.post("/api/calendar/add", json={"text": "x", "date": DATE}).status_code == 422
    )  # extra="forbid"


def test_a_line_with_no_day_in_it_is_refused(client, monkeypatch):
    """The one rule the todo half adds, and it applies to every line.

    `calparse` still resolves a dateless line onto today -- that is what makes
    it usable for anything else -- but a due date nobody typed is a claim about
    when work is owed, and inventing one is worse than refusing the line.
    Nothing is written, and the text comes back so it can be edited.
    """
    written = _no_gcalcli(monkeypatch)
    body = client.post(
        "/api/calendar/add", json={"text": "fix my printing setup", "today": DATE}
    ).json()
    assert written == {}
    assert body["written"] == []
    assert body["failed"][0]["raw"] == "fix my printing setup"
    assert "needs a day" in body["failed"][0]["error"]


def test_someday_writes_an_item_and_no_event(client, monkeypatch):
    """The one thing the box writes that Google never hears about.

    An undated item is the dashboard's own: no day, so no event, so no
    calendar and no handle to delete it by. The schema enforces those four
    together; this checks the route agrees.
    """
    written = _no_gcalcli(monkeypatch)
    body = client.post(
        "/api/calendar/add", json={"text": "fix the coffee table someday", "today": DATE}
    ).json()

    assert written == {}  # gcalcli was never asked to add anything
    assert body["events"] == 0
    assert [line["title"] for line in body["written"]] == ["Fix the coffee table"]
    assert body["written"][0]["undated"] is True
    assert body["written"][0]["calendar"] is None

    item = client.get("/api/todo", params={"date": DATE}).json()["items"][0]
    assert item["due_date"] is None
    assert item["calendar"] is None
    assert item["event_id"] is None
    # Nothing of it was ever in Google, so there is nothing outstanding to say
    # is still standing -- the panel's "on calendar" warning must stay silent.
    assert item["event_deleted"] is True


def test_ticking_an_undated_item_asks_google_for_nothing(client, monkeypatch):
    _no_gcalcli(monkeypatch)
    called: list = []
    monkeypatch.setattr(
        feeds.Feeds, "delete_calendar_event", lambda self, **kw: called.append(kw)
    )
    client.post(
        "/api/calendar/add", json={"text": "fix the coffee table someday", "today": DATE}
    )
    item = client.get("/api/todo", params={"date": DATE}).json()["items"][0]

    body = client.post(
        "/api/todo/done", json={"todo_id": item["id"], "done": True, "today": DATE}
    ).json()
    assert called == []
    # And it is still in the panel afterwards, in the done box: an undated row
    # has no `due_date` for the fortnight window to bound, so it is windowed on
    # `done_at` instead. Without that it would vanish the moment it was ticked.
    assert [i["done"] for i in body["items"]] == [True]


def test_the_good_lines_commit_and_the_bad_ones_come_back(client, monkeypatch):
    """A forty-line paste must not be lost to one typo."""
    _no_gcalcli(monkeypatch)
    body = client.post(
        "/api/calendar/add",
        json={
            "text": "fpm Thornhill Chapter 3 due thursday\n"
                    "fix the printer\n"
                    "read ch4 friday",
            "today": DATE,
        },
    ).json()
    # The expansion is read from the real config.toml, so this string tracks
    # `[todo.subjects].fpm` and has to be updated when the class is renamed.
    assert [line["title"] for line in body["written"]] == [
        "US National Security Policy Making Thornhill Chapter 3",
        "Read ch4",
    ]
    # Was "someday: fix the printer", which is now a *writable* line -- see
    # `todo._someday`. The bad line here has to be one with no day and no way
    # to read one, which is what a bare title is.
    assert [line["raw"] for line in body["failed"]] == ["fix the printer"]


def test_an_alias_expands_as_a_word_not_as_a_substring(client, monkeypatch):
    """`os` -> Operating Systems fires in "new os homework", never in "banjos".

    The distinction is the whole of the alias design: a substitution behaves
    like a variable, so it is safe to give a subject a two-letter name. A
    substring match would make every such name a hazard in ordinary English.
    """
    _no_gcalcli(monkeypatch)
    lines = client.post(
        "/api/calendar/preview",
        json={"text": "new os homework friday\nbuy banjos saturday", "today": DATE},
    ).json()["lines"]
    assert lines[0]["title"] == "Operating Systems homework"
    assert [h["key"] for h in lines[0]["aliases"]] == ["os"]
    assert lines[1]["title"] == "Buy banjos"
    assert lines[1]["aliases"] == []


def test_a_repeating_line_becomes_one_event_per_week(client, monkeypatch):
    """No recurrence flag exists in `gcalcli add`, so occurrences are written
    out -- which is also what makes deleting a single week possible later."""
    _no_gcalcli(monkeypatch)
    body = client.post(
        "/api/calendar/add",
        json={"text": "os reading every tuesday for 3 weeks", "today": DATE},
    ).json()
    assert body["events"] == 3
    assert body["written"][0]["recurrence"] == "every week × 3"
    assert len(body["written"][0]["dates"]) == 3

    items = client.get("/api/todo", params={"date": DATE}).json()["items"]
    assert [i["due_date"] for i in items] == ["2026-07-28", "2026-08-04", "2026-08-11"]
    # One series, so the panel can group them and a delete can name just one.
    assert len({i["series"] for i in items}) == 1


def test_work_becomes_a_todo_and_an_event_does_not(client, monkeypatch):
    """The target calendar is the whole todo/event distinction -- no mode, no
    sigil. `[todo].todo_calendars` names which ones mean "this can be done"."""
    _no_gcalcli(monkeypatch)
    client.post("/api/calendar/add", json={"text": "read ch4 friday", "today": DATE})
    client.post(
        "/api/calendar/add",
        json={"text": "brunch sunday", "today": DATE, "calendar": "Special Events"},
    )
    items = client.get("/api/todo", params={"date": DATE}).json()["items"]
    assert [i["title"] for i in items] == ["Read ch4"]
    assert items[0]["calendar"] == "Homework"  # the default, not the model


def test_ticking_an_item_takes_its_event_off_the_calendar(client, monkeypatch):
    _no_gcalcli(monkeypatch)
    deleted: dict = {}
    monkeypatch.setattr(
        feeds.Feeds,
        "delete_calendar_event",
        lambda self, **kw: deleted.update(kw) or True,
    )
    client.post("/api/calendar/add", json={"text": "read ch4 friday", "today": DATE})
    item = client.get("/api/todo", params={"date": DATE}).json()["items"][0]

    body = client.post(
        "/api/todo/done", json={"todo_id": item["id"], "done": True, "today": DATE}
    ).json()
    assert deleted["title"] == "Read ch4"
    assert deleted["event_id"] == "ev1"
    assert body["items"][0]["done"] is True
    assert body["items"][0]["event_deleted"] is True


def test_an_event_already_gone_still_counts_as_removed(client, monkeypatch):
    """Deleted and never-there are the same answer to the flag's question.

    Reporting "on calendar" for an event the search could not find would have
    the panel insisting something is standing that demonstrably is not — and
    the retry it invites can never succeed.
    """
    _no_gcalcli(monkeypatch)
    monkeypatch.setattr(feeds.Feeds, "delete_calendar_event", lambda self, **kw: False)
    client.post("/api/calendar/add", json={"text": "read ch4 friday", "today": DATE})
    item = client.get("/api/todo", params={"date": DATE}).json()["items"][0]

    body = client.post(
        "/api/todo/done", json={"todo_id": item["id"], "done": True, "today": DATE}
    ).json()
    assert body["items"][0]["event_deleted"] is True


def test_a_tick_survives_gcalcli_being_unreachable(client, monkeypatch):
    """The checkbox is local and the delete is a network call. An unreachable
    Google is not a reason to refuse to mark homework done."""
    _no_gcalcli(monkeypatch)

    def _boom(self, **kw):
        raise feeds.FeedError("gcalcli timed out after 20s")

    monkeypatch.setattr(feeds.Feeds, "delete_calendar_event", _boom)
    client.post("/api/calendar/add", json={"text": "read ch4 friday", "today": DATE})
    item = client.get("/api/todo", params={"date": DATE}).json()["items"][0]

    body = client.post(
        "/api/todo/done", json={"todo_id": item["id"], "done": True, "today": DATE}
    ).json()
    assert body["items"][0]["done"] is True
    # ...and the panel can still see that the event is standing, so it can say so.
    assert body["items"][0]["event_deleted"] is False


def test_calendar_add_names_the_calendar_it_writes_to(client, monkeypatch):
    """The bug this whole path exists to fix.

    `gcalcli quick` was called with no `--calendar`, which on an account with
    more than one writable calendar drops gcalcli to an interactive menu and
    hangs until the timeout. Every write must name its target.
    """
    written = _no_gcalcli(monkeypatch)
    client.post(
        "/api/calendar/add",
        json={"text": "analysis test wednesday from 9:25-10:30", "today": DATE},
    )
    assert written["calendar"] == "Tests"  # the rule, ahead of the Homework default
    assert written["title"] == "Analysis test"
    assert written["when"].endswith("09:25")
    assert written["duration"] == 65
    assert written["all_day"] is False


def test_calendar_preview_writes_nothing(client, monkeypatch):
    written = _no_gcalcli(monkeypatch)
    body = client.post(
        "/api/calendar/preview",
        json={"text": "move to ND 8am-10am wednesday", "today": DATE, "calendar": "model"},
    ).json()
    line = body["lines"][0]
    assert line["title"] == "Move to ND"
    assert (line["start"], line["end"]) == ("08:00", "10:00")
    # Under `learned` every writable calendar is offered, best first, so Tab
    # can reach all of them. `auto` — the picker's default — ranks nothing,
    # because it never asks the model.
    assert {g["calendar"] for g in line["ranked"]} == {"Special Events", "Tests", "Homework"}
    assert written == {}


def test_the_calendar_list_says_which_ones_are_writable(client, monkeypatch):
    """`writable` has to survive serialisation, not just exist in Python.

    It was a bare `@property`, which `model_dump` ignores — so the grid's Tab
    cycle filtered on `undefined`, found nothing writable, and silently refused
    to change the target calendar.
    """
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [
            api.CalendarInfo(name="Tests", access="owner"),
            api.CalendarInfo(name="Holidays in United States", access="reader"),
        ],
    )
    monkeypatch.setattr(feeds.Feeds, "calendar_history", lambda self, today: [])
    monkeypatch.setattr(
        feeds.Feeds,
        "calendar",
        lambda self, start, days=7: _cached(
            feeds.CalendarResponse(start=start, calendars=self.calendars())
        ),
    )
    body = client.get("/api/calendar", params={"date": DATE}).json()
    assert {c["name"]: c["writable"] for c in body["calendars"]} == {
        "Tests": True,
        "Holidays in United States": False,
    }


def test_a_calendar_created_after_boot_becomes_usable(client, monkeypatch):
    """The classifier is fitted once at boot, so the account it was fitted
    against has to be re-checked rather than assumed."""
    live = ["Special Events", "Tests"]
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [api.CalendarInfo(name=n, access="owner") for n in live],
    )
    monkeypatch.setattr(feeds.Feeds, "calendar_history", lambda self, today: [])
    written: dict = {}
    monkeypatch.setattr(
        feeds.Feeds,
        "add_calendar_event",
        lambda self, **kw: written.update(kw)
        or feeds.WrittenEvent(event_id="ev1", url=None, output="created"),
    )

    offered = lambda: {  # noqa: E731
        g["calendar"]
        for g in client.post(
            "/api/calendar/preview",
            json={"text": "read chapter 4 friday", "today": DATE, "calendar": "model"},
        ).json()["lines"][0]["ranked"]
    }
    assert offered() == {"Special Events", "Tests"}

    live.append("Thesis")
    assert offered() == {"Special Events", "Tests", "Thesis"}
    # ...and it can be written to, not merely listed.
    client.post(
        "/api/calendar/add",
        json={"text": "read chapter 4 friday", "today": DATE, "calendar": "Thesis"},
    )
    assert written["calendar"] == "Thesis"


def test_a_deleted_calendar_stops_being_offered(client, monkeypatch):
    live = ["Special Events", "Tests"]
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [api.CalendarInfo(name=n, access="owner") for n in live],
    )
    monkeypatch.setattr(feeds.Feeds, "calendar_history", lambda self, today: [])
    monkeypatch.setattr(
        feeds.Feeds,
        "add_calendar_event",
        lambda self, **kw: feeds.WrittenEvent(event_id="ev1", url=None, output="created"),
    )

    resolve = lambda: client.post(  # noqa: E731
        "/api/calendar/preview", json={"text": "final exam friday", "today": DATE}
    ).json()["lines"][0]
    assert resolve()["calendar"] == "Tests"  # the rule fires

    live.remove("Tests")
    body = resolve()
    # The rule names a calendar that is gone, so it is skipped -- and so is the
    # configured Homework default, which is not on this account either. Both
    # would produce an add gcalcli cannot satisfy.
    assert body["calendar"] == "Special Events"

    # And an override to it is refused now, where it would have been accepted.
    assert (
        client.post(
            "/api/calendar/add",
            json={"text": "final exam friday", "today": DATE, "calendar": "Tests"},
        ).status_code
        == 409
    )


def test_an_override_is_recorded_and_used(client, monkeypatch):
    written = _no_gcalcli(monkeypatch)
    client.post(
        "/api/calendar/add",
        json={"text": "brunch sunday", "today": DATE, "calendar": "Tests"},
    )
    assert written["calendar"] == "Tests"

    from dashboard.db.repository import Repository

    conn = client.app.state.db.conn
    assert Repository(conn).calendar_corrections() == [("Brunch", "Tests")]


def test_an_override_to_an_unknown_calendar_is_refused(client, monkeypatch):
    written = _no_gcalcli(monkeypatch)
    # gcalcli matches --calendar as a regex, so an unrecognised name does not
    # fail cleanly downstream -- it is rejected here instead.
    response = client.post(
        "/api/calendar/add",
        json={"text": "brunch sunday", "today": DATE, "calendar": "Holidays in United States"},
    )
    assert response.status_code == 409  # the app's ValueError mapping
    assert written == {}


# --- feed freshness --------------------------------------------------------


def test_markets_reports_when_it_was_fetched(client, monkeypatch):
    monkeypatch.setattr(
        feeds.Feeds, "markets", lambda self: _cached([feeds.MarketRow(ticker="AAPL", price=1.0)])
    )
    body = client.get("/api/markets").json()
    assert body["rows"][0]["ticker"] == "AAPL"
    # The TTL crosses HTTP so the browser schedules its next poll from the
    # server's own cache window rather than keeping a second copy of it.
    assert body["feed"]["ttl_seconds"] == 60
    assert body["feed"]["stale"] is False
    # tz-aware: `new Date(...)` reads a naive timestamp as *local*, which on
    # this machine means WSL's clock against the browser's.
    assert dt.datetime.fromisoformat(body["feed"]["fetched_at"]).tzinfo is not None


def test_a_source_outage_is_flagged_rather_than_served_as_live(client, monkeypatch):
    """The complaint this fixes: a Yahoo outage rendered like live quotes.

    The cache serving its last good copy is right — an empty panel would be
    worse. What was missing is that the panel had no way to say so.
    """
    rows = [feeds.MarketRow(ticker="AAPL", price=1.0)]

    def once(self):
        # The real `cached`, driven through a source that works and then does
        # not — stubbing `markets` itself would skip the code under test.
        return self.cached("markets", 60, next(sources))

    sources = iter([lambda: rows, _boom])
    monkeypatch.setattr(feeds.Feeds, "markets", once)

    fresh = client.get("/api/markets").json()
    assert fresh["feed"]["stale"] is False

    client.app.state.feeds._cache["markets"] = feeds.Cached(rows, 0.0, 60)
    stale = client.get("/api/markets").json()
    assert stale["feed"]["stale"] is True
    assert stale["rows"][0]["ticker"] == "AAPL"  # still served, just labelled
    assert stale["feed"]["fetched_at"].startswith("1970-")  # the original fetch


def _boom():
    raise OSError("connection reset")


def test_news_and_calendar_carry_freshness_too(client, monkeypatch):
    _serve(monkeypatch, "https://ex.com/a")
    assert client.get("/api/news", params={"date": DATE}).json()["feed"]["ttl_seconds"] == 60

    monkeypatch.setattr(
        feeds.Feeds, "calendar", lambda self, start, days=7: _cached(feeds.CalendarResponse())
    )
    body = client.get("/api/calendar", params={"date": DATE}).json()
    # Attached by the route: the fetcher builds this response inside the cache
    # and cannot know when it will count as fetched.
    assert body["feed"]["ttl_seconds"] == 60


# --- error mapping ---------------------------------------------------------


def test_a_feed_outage_is_a_503_with_a_readable_message(client, monkeypatch):
    def boom(self):
        raise feeds.FeedError("all news feeds failed")

    monkeypatch.setattr(feeds.Feeds, "news", boom)
    response = client.get("/api/news", params={"date": DATE})
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


def test_a_stalled_api_connection_is_a_502_naming_the_network(client, monkeypatch):
    """The reported word-grading failure. A transport error is still an
    `LlmError`, so it resolves to that handler by MRO rather than falling
    through to the catch-all and becoming an anonymous 500 -- the panel has to
    say the network was at fault, because that is the part you can act on."""
    from dashboard.llm import LlmTransportError

    def boom(*a, **k):
        raise LlmTransportError(
            "could not reach the Anthropic API: The read operation timed out", spent=False
        )

    monkeypatch.setattr(content, "grade_sentences", boom)
    response = client.post(
        "/api/word/check", json={"date": DATE, "word": "petrichor", "sentences": ["a", "b"]}
    )
    assert response.status_code == 502
    assert "could not reach the Anthropic API" in response.json()["error"]


def test_an_unexpected_error_does_not_leak_internals(client, monkeypatch):
    def boom(*a, **k):
        raise TypeError("something about a NoneType")

    monkeypatch.setattr(content, "topic_info", boom)
    response = client.get("/api/topic", params={"date": DATE})
    assert response.status_code == 500
    assert response.json() == {"error": "internal error — see the server log"}


# --- spend -----------------------------------------------------------------


def test_spend_reports_three_windows_on_an_empty_log(client):
    """The strip has to render on a fresh install, before anything is spent."""
    body = client.get("/api/spend", params={"date": DATE}).json()
    assert [w["label"] for w in body["windows"]] == ["today", "7d", "30d"]
    assert body["problems_served"] == 0
    assert body["tracking_since"] is None


def test_spend_counts_problems_served_from_the_database(client, settings, repo, problem):
    """Served comes from `math_attempt` and paid from the metrics log, so a
    problem with no log line reads as one the batch or the pool covered."""
    repo.open_math_attempt(TODAY, problem.id)
    settings.path("metrics").write_text(
        json.dumps({"date": DATE, "kind": "word", "est_cost_usd": 0.01}) + "\n",
        encoding="utf-8",
    )
    body = client.get("/api/spend", params={"date": DATE}).json()
    assert body["problems_served"] == 1
    assert body["problems_paid"] == 0
    assert body["windows"][0]["usd"] == 0.01


def test_spend_survives_a_corrupt_log(client, settings):
    """One bad line from July must not take the whole panel down -- it is
    counted and reported instead."""
    settings.path("metrics").write_text("{not json\n", encoding="utf-8")
    body = client.get("/api/spend", params={"date": DATE}).json()
    assert body["unreadable_lines"] == 1
    assert body["windows"][2]["usd"] == 0.0


# --- history ---------------------------------------------------------------


def test_history_is_empty_but_well_formed_with_no_data(client):
    body = client.get("/api/history").json()
    assert body == {"areas": [], "daylogs": []}


def test_history_returns_day_logs_newest_first(client, repo):
    repo.set_daylog(TODAY - dt.timedelta(days=2), "older")
    repo.set_daylog(TODAY, "newer")
    body = client.get("/api/history").json()
    assert [e["text"] for e in body["daylogs"]] == ["newer", "older"]


def test_history_plots_only_areas_with_measured_results(client, repo, settings):
    """An area showing the pooled rating as a placeholder has no trajectory of
    its own; drawing one would show a trend that never happened."""
    stored = repo.add_problem(
        d.MathProblem(
            source="AIME 2012 Problem 3",
            tier="aime-late",
            anchor=1500,
            statement="s",
            answer="a",
            hints=["1", "2", "3"],
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 9, 0),
            topic="generating functions",
            area="combinatorics",
        )
    )
    repo.open_math_attempt(TODAY, stored.id)
    repo.finish_math(TODAY, "solved")

    body = client.get("/api/history").json()
    assert [a["area"] for a in body["areas"]] == ["combinatorics"]
    (point,) = body["areas"][0]["points"]
    assert point[0] == DATE
    assert body["areas"][0]["events"] == 1


def test_history_takes_no_date_parameter(client):
    """The one GET with nothing relative to today in it. Asking the browser
    for a day it would not use would be cargo-culting the invariant."""
    assert client.get("/api/history", params={"date": DATE}).status_code == 200


def test_a_reordered_calendar_list_does_not_refit(client, monkeypatch):
    """What the model is fitted against is *which* calendars exist, not the
    order gcalcli printed them in.

    gcalcli sorts by access role and nothing promises a stable order within
    one, so comparing sequences would refit — an eight-second history fetch —
    over a reshuffle that changes nothing.
    """
    live = ["Special Events", "Tests"]
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [api.CalendarInfo(name=n, access="owner") for n in live],
    )
    histories = []

    def history(self, today):
        histories.append(today)
        # Two classes at least, or sklearn declines to fit and `fitted` stays
        # False -- which would make every preview retry and hide the very
        # thing this test is checking.
        return [
            api.CalendarEvent(date=TODAY, title="Final Exam", calendar="Tests"),
            api.CalendarEvent(date=TODAY, title="Probability Midterm", calendar="Tests"),
            api.CalendarEvent(date=TODAY, title="Blue and Gold Game", calendar="Special Events"),
            api.CalendarEvent(date=TODAY, title="Bengal Bouts Final", calendar="Special Events"),
        ]

    monkeypatch.setattr(feeds.Feeds, "calendar_history", history)

    preview = lambda: client.post(  # noqa: E731
        "/api/calendar/preview", json={"text": "lunch friday", "today": DATE}
    )
    assert preview().status_code == 200
    fitted_once = len(histories)

    live.reverse()
    assert preview().status_code == 200
    assert len(histories) == fitted_once, "a reshuffle must not trigger a refit"

    # ...but a genuine change still does.
    live.append("Classes")
    assert preview().status_code == 200
    assert len(histories) > fitted_once


def test_retrain_rereads_the_history_the_daily_cache_would_have_hidden(client, monkeypatch):
    """The button exists for exactly one thing the automatic triggers miss.

    `auto` already refits at boot, when a calendar appears or disappears, and
    after every correction — but the event history behind it is cached for a
    day, so on a long-running server nothing filed since this morning reaches
    the model. Retrain drops that cache first.
    """
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [
            api.CalendarInfo(name=n, access="owner") for n in ("Special Events", "Tests")
        ],
    )
    events = [
        api.CalendarEvent(date=TODAY, title="Final Exam", calendar="Tests"),
        api.CalendarEvent(date=TODAY, title="Blue and Gold Game", calendar="Special Events"),
    ]
    reads = []

    def history(self, today):
        reads.append(today)
        return list(events)

    monkeypatch.setattr(feeds.Feeds, "calendar_history", history)

    client.post("/api/calendar/preview", json={"text": "lunch friday", "today": DATE})
    assert len(reads) == 1

    # Something filed since. Without a retrain the model never sees it: the
    # calendar set is unchanged, so nothing triggers a refit.
    events.append(api.CalendarEvent(date=TODAY, title="Probability Midterm", calendar="Tests"))
    client.post("/api/calendar/preview", json={"text": "lunch friday", "today": DATE})
    assert len(reads) == 1, "an ordinary preview must not refetch the history"

    body = client.post("/api/calendar/retrain", json={}).json()
    assert len(reads) == 2
    assert body["fitted"] is True
    assert body["samples"] == 3
    assert sorted(body["calendars"]) == ["Special Events", "Tests"]


def test_retrain_reports_why_when_it_cannot_fit(client, monkeypatch):
    monkeypatch.setattr(
        feeds.Feeds,
        "calendars",
        lambda self: [api.CalendarInfo(name="Tests", access="owner")],
    )
    monkeypatch.setattr(feeds.Feeds, "calendar_history", lambda self, today: [])
    body = client.post("/api/calendar/retrain", json={}).json()
    assert body["fitted"] is False
    assert body["samples"] == 0
    assert "not enough" in body["unavailable"]


# --- the two quick logs ----------------------------------------------------


def test_a_visit_comes_back_in_the_rebuilt_panel(client):
    panel = client.post(
        "/api/bathroom",
        json={"date": DATE, "hour": 8, "kind": "stool", "solidity": "soft", "volume": "medium"},
    ).json()
    assert [(e["at"], e["solidity"]) for e in panel["bathroom_today"]] == [("08:00", "soft")]


def test_a_stool_without_a_solidity_is_a_422_naming_the_field(client):
    """Refused at the model rather than at the table.

    Both refuse it, and the difference is the audience: this way the browser
    gets a sentence with a field name in it, where an IntegrityError reaching
    SQLite would be a 500 with a CHECK constraint in it.
    """
    response = client.post(
        "/api/bathroom",
        json={"date": DATE, "hour": 8, "kind": "stool", "volume": "medium"},
    )
    assert response.status_code == 422
    assert "solidity" in response.text


def test_a_urination_with_a_solidity_is_refused_too(client):
    response = client.post(
        "/api/bathroom",
        json={
            "date": DATE,
            "hour": 8,
            "kind": "urine",
            "color": "light",
            "solidity": "soft",
            "volume": "small",
        },
    )
    assert response.status_code == 422


def test_an_hour_outside_the_day_is_a_422(client):
    response = client.post(
        "/api/bathroom",
        json={"date": DATE, "hour": 24, "kind": "urine", "color": "light", "volume": "small"},
    )
    assert response.status_code == 422


def test_deleting_a_visit_removes_it(client):
    panel = client.post(
        "/api/bathroom",
        json={"date": DATE, "hour": 8, "kind": "urine", "color": "clear", "volume": "large"},
    ).json()
    entry_id = panel["bathroom_today"][0]["id"]
    after = client.post(
        "/api/bathroom/delete", json={"date": DATE, "entry_id": entry_id}
    ).json()
    assert after["bathroom_today"] == []


def test_deleting_a_visit_that_is_gone_is_a_404(client):
    assert (
        client.post("/api/bathroom/delete", json={"date": DATE, "entry_id": 999}).status_code
        == 404
    )


def test_meals_stack_up_through_the_day(client):
    client.post("/api/meal", json={"date": DATE, "hour": 8, "size": "small", "quality": 5})
    panel = client.post(
        "/api/meal", json={"date": DATE, "hour": 13, "size": "large", "quality": 2}
    ).json()
    assert [(m["at"], m["size"]) for m in panel["meals_today"]] == [
        ("08:00", "small"),
        ("13:00", "large"),
    ]


def test_a_meal_quality_outside_the_scale_is_a_422(client):
    response = client.post(
        "/api/meal", json={"date": DATE, "hour": 12, "size": "large", "quality": 9}
    )
    assert response.status_code == 422


def test_a_drink_comes_back_in_the_rebuilt_panel(client):
    panel = client.post(
        "/api/drink", json={"date": DATE, "hour": 9, "kind": "water", "amount": 1.4}
    ).json()
    assert [(e["at"], e["kind"], e["amount"]) for e in panel["drinks_today"]] == [
        ("09:00", "water", 1.4)
    ]


def test_a_drink_of_nothing_is_a_422(client):
    response = client.post(
        "/api/drink", json={"date": DATE, "hour": 9, "kind": "water", "amount": 0}
    )
    assert response.status_code == 422
    assert "amount" in response.text


def test_a_misplaced_decimal_point_is_a_422_rather_than_a_500(client):
    """The bound is on the table too. Refused here so the browser gets a
    sentence naming the field, exactly as a stool without a solidity is."""
    response = client.post(
        "/api/drink", json={"date": DATE, "hour": 9, "kind": "water", "amount": 150}
    )
    assert response.status_code == 422


def test_a_drink_nobody_has_defined_is_a_422(client):
    response = client.post(
        "/api/drink", json={"date": DATE, "hour": 9, "kind": "milk", "amount": 1}
    )
    assert response.status_code == 422


def test_deleting_a_drink_removes_it(client):
    panel = client.post(
        "/api/drink", json={"date": DATE, "hour": 9, "kind": "coffee", "amount": 1}
    ).json()
    entry_id = panel["drinks_today"][0]["id"]
    after = client.post(
        "/api/drink/delete", json={"date": DATE, "entry_id": entry_id}
    ).json()
    assert after["drinks_today"] == []


def test_deleting_a_drink_that_is_gone_is_a_404(client):
    assert (
        client.post("/api/drink/delete", json={"date": DATE, "entry_id": 999}).status_code == 404
    )


def test_the_quick_logs_are_quiet_until_they_have_history(client):
    """One meal and one visit say nothing at all.

    The rule the whole health tab runs on, and the one most at risk here: a day
    with two rows in it will happily produce an average, and the average would
    be wrong in the direction that reads as a problem.
    """
    client.post("/api/meal", json={"date": DATE, "hour": 12, "size": "large", "quality": 1})
    client.post("/api/drink", json={"date": DATE, "hour": 21, "kind": "alcohol", "amount": 6})
    panel = client.post(
        "/api/bathroom",
        json={"date": DATE, "hour": 8, "kind": "urine", "color": "darker", "volume": "small"},
    ).json()
    derived = panel["derived"]
    assert derived["intake_est"] is None
    assert derived["diet_quality"] is None
    assert derived["liquid_days"] is None
    assert derived["days_since_stool"] is None
    assert derived["dark_urine_days"] == 0
    # Six drinks on the one day the log has ever been kept is not a week, and
    # a zero here would be worse than the silence: it would read as a dry week.
    assert derived["fluid_ml"] is None
    assert derived["caffeine_drinks"] is None
    assert derived["alcohol_week"] is None


def test_sleep_hours_are_typed_like_any_other_scalar(client):
    """The check-in's second box. It posts to `/api/health` rather than to an
    endpoint of its own, because hours slept is a daily scalar exactly like
    weight -- and once the watch is syncing, the same row arrives without
    anybody typing anything."""
    panel = client.post(
        "/api/health", json={"date": DATE, "metric": "sleep_hours", "value": 7.5}
    ).json()
    series = {s["metric"]: s["points"] for s in panel["series"]}
    assert series["sleep_hours"] == [[DATE, 7.5]]
    assert panel["synced_today"] == []


def test_a_typed_reading_is_not_reported_as_synced(client, repo):
    """`synced_today` is provenance, not presence. A number you typed must
    never come back labelled as the watch's, or the morning panel would tell
    you to stop filling in a box nothing is filling in for you."""
    from dashboard.models.common import EntrySource

    repo.log_health(HealthMetric.hrv, TODAY, 61.0, EntrySource.garmin)
    repo.log_health(HealthMetric.weight, TODAY, 171.0, EntrySource.manual)
    panel = client.get("/api/state", params={"date": DATE}).json()["health"]
    assert panel["synced_today"] == ["hrv"]


def test_the_sync_endpoint_says_so_when_the_feature_is_off(client):
    """409 with a sentence rather than a silent no-op: the button is only ever
    pressed by somebody who expected numbers."""
    response = client.post("/api/garmin/sync", json={"date": DATE})
    assert response.status_code == 409
    assert "config.toml" in response.json()["error"]


def test_the_health_panel_can_be_asked_for(client):
    """The phone log's one read.

    Every write already returns this panel, so the dashboard has never needed
    to ask -- it gets it in the boot payload and keeps it fresh from its own
    responses. `/m` opens straight onto one of the five logs, and pulling the
    whole of `/api/state` to draw a list of today's meals would be a page of
    maths problems, feeds and ratings for four rows.
    """
    client.post("/api/meal", json={"date": DATE, "hour": 13, "size": "large", "quality": 4})
    panel = client.get("/api/health", params={"date": DATE}).json()
    assert [m["hour"] for m in panel["meals_today"]] == [13]


def test_the_health_panel_read_takes_the_browsers_date(client):
    """[Invariant 1]: the server never decides what day it is, and this is the
    one GET on the health tab that could have been tempted to."""
    assert client.get("/api/health").status_code == 422
