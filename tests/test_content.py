"""Every paid generation writes a line to metrics.jsonl.

The math problem always did. The word, the grader, the theorem card and the
topic links did not: they called `llm.json_call` straight through and were
invisible to the log while billing to the same API key. These tests are the
guard against that regressing, which it will, because adding a new prompt is
easy and remembering the log is not.

No test here makes a network call — `llm.call` is stubbed and the assertions
are about what got written, not what the model said.
"""

from __future__ import annotations

import json

import pytest

from dashboard import content, llm

from .conftest import TODAY


@pytest.fixture
def paid(monkeypatch):
    """Stub `llm.call` with a fixed reply and a fixed bill.

    Fills the caller's `metrics` rather than only returning one — that is
    exactly the contract the cost log depends on, so a stub that skipped it
    would let the real regression through.
    """
    payloads: dict[str, object] = {}

    def fake_call(settings, system, user, *, metrics=None, **kw):
        m = metrics if metrics is not None else llm.Metrics()
        m.input_tokens += 1_000
        m.output_tokens += 500
        m.web_searches += 1
        return json.dumps(payloads["reply"]), m

    monkeypatch.setattr(llm, "call", fake_call)
    return payloads


def lines(settings) -> list[dict]:
    path = settings.path("metrics")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_word_of_the_day_is_logged(settings, paid):
    paid["reply"] = {"word": "eldritch", "pos": "adj.", "definition": "weird, otherworldly"}
    content.word_of_day(settings, TODAY)

    (entry,) = lines(settings)
    assert entry["kind"] == "word"
    assert entry["ok"] is True
    assert entry["date"] == TODAY.isoformat()
    assert entry["metrics"]["input_tokens"] == 1_000
    assert entry["est_cost_usd"] > 0


def test_sentence_grading_is_logged(settings, paid):
    paid["reply"] = {
        "distinct": True,
        "per_sentence": [{"ok": True, "note": "fine"}] * 5,
        "verdict": "good",
    }
    content.grade_sentences(settings, TODAY, "eldritch", ["a", "b", "c", "d", "e"])

    (entry,) = lines(settings)
    assert entry["kind"] == "word-grade"
    assert entry["ok"] is True


def test_the_theorem_card_is_logged_with_its_topic(settings, paid):
    paid["reply"] = {"name": "Cauchy-Schwarz", "statement": "$|<x,y>|^2 \\le ...$", "topic": None}
    content.theorem_card(settings, TODAY)

    (entry,) = lines(settings)
    assert entry["kind"] == "theorem"
    assert entry["topic"] == settings.math.topic_for(TODAY)


def test_topic_links_are_logged_once_and_then_served_from_cache(settings, repo, paid):
    """`topic_info` is keyed by topic, not date, so the second call is free —
    and a free call must not write a line claiming a cost."""
    paid["reply"] = {
        "textbook": {"title": "generatingfunctionology", "url": "https://example.com/gfology"},
        "theorems": [{"title": f"t{i}", "url": f"https://example.com/{i}"} for i in range(6)],
    }
    settings = settings.model_copy(update={"topic_library": {}})  # force the generated path

    content.topic_info(settings, repo, TODAY)
    content.topic_info(settings, repo, TODAY)

    (entry,) = lines(settings)
    assert entry["kind"] == "topic"


def test_a_failed_generation_is_logged_with_the_tokens_it_burned(settings, monkeypatch):
    """The expensive case. A call that spends 40k tokens and then dies is a
    real charge on the invoice; logging nothing is how the log and the console
    drift apart."""

    def dies(settings_, system, user, *, metrics=None, **kw):
        metrics.input_tokens += 40_000
        metrics.web_searches += 4
        raise llm.LlmError("the model kept pausing")

    monkeypatch.setattr(llm, "call", dies)

    with pytest.raises(llm.LlmError):
        content.word_of_day(settings, TODAY)

    (entry,) = lines(settings)
    assert entry["ok"] is False
    assert entry["error"] == "the model kept pausing"
    assert entry["metrics"]["input_tokens"] == 40_000
    assert entry["est_cost_usd"] > 0


def test_the_hand_curated_pool_costs_nothing_and_says_so(settings, repo, monkeypatch):
    """A problem served from problems.json makes no API call, so it writes no
    metrics line at all — an empty log is the correct record of $0.00."""
    settings.path("problems").write_text(
        json.dumps(
            [
                {
                    "source": "AMC 2019 P5",
                    "tier": "amc",
                    "statement": "What is $1+1$?",
                    "answer": "2",
                    "hint1": "a",
                    "hint2": "b",
                    "hint3": "c",
                }
            ]
        ),
        encoding="utf-8",
    )

    def never(*a, **kw):
        raise AssertionError("the pool must be served before anything is paid for")

    monkeypatch.setattr(llm, "call", never)

    problem = content.next_problem(settings, repo, TODAY)
    assert problem.model == content.HAND_CURATED
    assert lines(settings) == []
