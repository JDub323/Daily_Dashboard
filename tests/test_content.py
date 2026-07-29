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


# --- which rating difficulty is pitched at ---------------------------------


def _rate(repo, area, day_offset, score):
    """One finished result in `area`, so it has a rating of its own.

    `day_offset` is passed rather than derived: one attempt per date is a
    primary key, so two results landing on the same day silently overwrite
    each other and the test measures fewer events than it set up.
    """
    import datetime as dt

    from dashboard.models import domain as d
    from dashboard.models.common import MathOutcome

    p = repo.add_problem(
        d.MathProblem(
            source=f"seeded problem {day_offset}",
            tier="aime-late",
            anchor=1500,
            statement="s",
            answer="a",
            hints=["1", "2", "3"],
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 9, 0),
            topic="t",
            area=area,
        )
    )
    day = TODAY - dt.timedelta(days=day_offset + 1)
    repo.open_math_attempt(day, p.id)
    repo.finish_math(day, MathOutcome.solved if score else MathOutcome.failed)


def test_the_target_rating_is_the_area_not_the_pool(settings, repo):
    """The whole point: strong overall and weak at this week's area should
    produce a problem aimed at the weak number."""
    area = settings.math.area_for(TODAY)
    assert area is not None
    other = "geometry" if area != "geometry" else "algebra"

    for i in range(3):  # three failures in this week's area
        _rate(repo, area, i, score=0)
    for i in range(3, 8):  # five solves elsewhere, lifting the pool back up
        _rate(repo, other, i, score=1)

    pooled = repo.current_rating("math")
    target = content.target_rating(settings, repo, TODAY)
    assert target == repo.current_rating("math", area)
    assert target < pooled


def test_an_untouched_area_pitches_exactly_where_the_pool_does(settings, repo):
    """Week one of a new area must not suddenly serve 1500 problems to a
    1700 player -- the fallback is the pooled rating, not the seed."""
    assert content.target_rating(settings, repo, TODAY) == repo.current_rating("math")


def test_the_target_falls_back_to_the_pool_with_no_area_map(settings, repo):
    bare = settings.model_copy(update={"math": settings.math.model_copy(update={"areas": {}})})
    assert content.target_rating(bare, repo, TODAY) == repo.current_rating("math")


def test_a_generated_problem_records_its_topic_and_area(settings, repo, paid):
    paid["reply"] = {
        "source": "AIME 2011 Problem 7",
        "tier": "aime-late",
        "statement": "s",
        "answer": "1",
        "hint1": "a",
        "hint2": "b",
        "hint3": "c",
    }
    problem = content.to_problem(paid["reply"], settings, "test", TODAY)
    assert problem.topic == settings.math.topic_for(TODAY)
    assert problem.area == settings.math.area_for(TODAY)


def test_a_problem_built_without_a_date_carries_no_area(settings):
    """The batch CLI validates problems it is not serving; guessing a topic
    for them would put results in a bucket they were never drawn for."""
    raw = {
        "source": "x 1",
        "tier": "aime-late",
        "statement": "s",
        "answer": "1",
        "hint1": "a",
        "hint2": "b",
        "hint3": "c",
    }
    problem = content.to_problem(raw, settings, "test")
    assert problem.topic is None and problem.area is None


# --- undelimited LaTeX -----------------------------------------------------

VANDERMONDE_AS_STORED = (
    "\\sum_{k=0}^{r} \\binom{m}{k}\\binom{n}{r-k} = \\binom{m+n}{r}, "
    "\\quad \\text{for non-negative integers } m, n, r."
)
"""The real thing, copied out of the deck. The model returned the formula
with no `$` anywhere, so KaTeX left it as literal backslashes on the card."""


def test_a_bare_formula_is_wrapped_so_it_can_render(settings, paid):
    paid["reply"] = {
        "name": "Vandermonde's Identity",
        "statement": VANDERMONDE_AS_STORED,
        "topic": "combinatorics",
    }
    card = content.theorem_from_prompt(settings, TODAY, "vandermonde's")
    assert card.statement.startswith("$$") and card.statement.endswith("$$")
    assert VANDERMONDE_AS_STORED.strip() in card.statement


def test_a_statement_the_model_delimited_is_left_alone(settings, paid):
    """One `$` means the model made a decision; do not second-guess where the
    display breaks should go."""
    delimited = "For $n \\ge 0$: $$\\binom{m+n}{r}=\\sum_k\\binom{m}{k}\\binom{n}{r-k}.$$"
    paid["reply"] = {"name": "Vandermonde", "statement": delimited, "topic": "combinatorics"}
    assert content.theorem_from_prompt(settings, TODAY, "x").statement == delimited


def test_prose_without_maths_is_not_wrapped(settings, paid):
    plain = "Every finite integral domain is a field."
    paid["reply"] = {"name": "Wedderburn", "statement": plain, "topic": "algebra"}
    assert content.theorem_from_prompt(settings, TODAY, "x").statement == plain


def test_the_system_prompt_names_both_delimiters(settings):
    """The prompt is the actual fix; the wrapper is only the net. If this ever
    reverts to a bare "LaTeX ok", undelimited statements come back."""
    assert "$$" in content._THEOREM_SYSTEM
    assert "bare formula" in content._THEOREM_SYSTEM.lower()


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
