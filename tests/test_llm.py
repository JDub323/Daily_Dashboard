"""JSON extraction and the LaTeX backslash repair.

`extract_json` is the single most defect-prone line the old server had: a
regex repairing invalid JSON produced by a model, run on every generation,
with no way to exercise it short of paying for one.
"""

from __future__ import annotations

import json

import pytest

from dashboard import llm
from dashboard.config import LlmSettings, ModelPricing


def test_a_plain_object_parses():
    assert llm.extract_json('{"a": 1}') == {"a": 1}


def test_a_plain_array_parses():
    assert llm.extract_json("[1, 2, 3]") == [1, 2, 3]


def test_prose_around_the_object_is_discarded():
    """Models preface JSON with "Here is the problem:" more often than not."""
    assert llm.extract_json('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}


def test_a_fenced_code_block_is_unwrapped():
    assert llm.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_singly_escaped_latex_is_repaired():
    """`\\dfrac` is what a model writes and is not a legal JSON escape."""
    raw = r'{"statement": "Find $\dfrac{n}{100}$ where $\sum_{i=1}^4 x_i = 98$."}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    parsed = llm.extract_json(raw)
    assert parsed["statement"] == r"Find $\dfrac{n}{100}$ where $\sum_{i=1}^4 x_i = 98$."


@pytest.mark.parametrize(
    "command",
    [r"\frac{n}{2}", r"\binom{n}{k}", r"\theta", r"\times", r"\neq", r"\rho", r"\bmod"],
)
def test_latex_that_looks_like_a_legal_escape_is_not_silently_corrupted(command):
    """The bug the old implementation had.

    `{"s": "$\\frac{n}{2}$"}` is *valid* JSON — `\\f` is a formfeed — so the
    strict parse succeeded and returned a statement with the maths replaced
    by a control character. Repairing only after a failed parse never got the
    chance to fire.
    """
    raw = '{"s": "$' + command + '$"}'
    assert llm.extract_json(raw)["s"] == f"${command}$"


def test_legal_escapes_survive_the_repair():
    raw = r'{"a": "quote\"inside", "b": "slash\/", "c": "back\\slash"}'
    parsed = llm.extract_json(raw)
    assert parsed == {"a": 'quote"inside', "b": "slash/", "c": "back\\slash"}


def test_a_newline_before_a_capital_is_still_a_newline():
    """The lowercase restriction: `\\nStep` is prose, `\\neq` is LaTeX."""
    assert llm.extract_json(r'{"a": "one\nStep two"}')["a"] == "one\nStep two"


def test_a_newline_before_whitespace_is_still_a_newline():
    assert llm.extract_json(r'{"a": "one\n two"}')["a"] == "one\n two"


def test_a_correctly_escaped_document_is_left_alone():
    """A doubled backslash must pass through untouched, or the repair would
    corrupt input that was already right."""
    raw = r'{"statement": "$\\frac{1}{2}$"}'
    assert llm.extract_json(raw)["statement"] == r"$\frac{1}{2}$"


def test_a_unicode_escape_survives():
    assert llm.extract_json(r'{"a": "é"}') == {"a": "é"}


def test_a_latex_command_starting_with_u_is_repaired_not_read_as_a_codepoint():
    assert llm.extract_json(r'{"a": "\underbrace{x}"}')["a"] == r"\underbrace{x}"


def test_mixed_latex_and_a_real_newline_in_one_document():
    raw = r'{"hint": "Use \binom{n}{k}", "note": "first line\nSecond"}'
    parsed = llm.extract_json(raw)
    assert parsed["hint"] == r"Use \binom{n}{k}"
    assert parsed["note"] == "first line\nSecond"


def test_unrepairable_garbage_still_raises():
    with pytest.raises(json.JSONDecodeError):
        llm.extract_json("{this is not json at all")


def test_the_first_object_wins_when_the_text_has_a_trailing_note():
    # The pattern is greedy, so it spans to the last brace — which is what
    # keeps nested objects intact.
    assert llm.extract_json('{"a": {"b": 2}} trailing') == {"a": {"b": 2}}


# --- cost ------------------------------------------------------------------


def settings(**kw):
    return LlmSettings(
        model="claude-sonnet-5",
        pricing={"claude-sonnet-5": ModelPricing(input=2e-6, output=10e-6)},
        **kw,
    )


def test_cost_sums_tokens_and_searches():
    cfg = settings(web_search_cost_usd=0.01)
    metrics = llm.Metrics(input_tokens=10_000, output_tokens=2_000, web_searches=3)
    # 10k * 2e-6 + 2k * 10e-6 + 3 * 0.01 = 0.02 + 0.02 + 0.03
    assert llm.est_cost(cfg, "claude-sonnet-5", metrics) == pytest.approx(0.07)


def test_an_unpriced_model_falls_back_to_the_standard_rate():
    """Over-reporting is the safe direction for a log whose purpose is
    noticing spend."""
    cfg = settings()
    metrics = llm.Metrics(input_tokens=1_000_000, output_tokens=0)
    assert llm.est_cost(cfg, "some-future-model", metrics) == pytest.approx(3.0)


def test_a_verified_generation_lands_in_the_expected_daily_range():
    """The plan approved web-search verification at ~$0.02-0.04/day; this is
    the tripwire for that budget quietly changing."""
    cfg = settings(web_search_cost_usd=0.01)
    typical = llm.Metrics(input_tokens=6_000, output_tokens=1_500, web_searches=2)
    assert 0.02 <= llm.est_cost(cfg, "claude-sonnet-5", typical) <= 0.05


# --- pause_turn continuation ----------------------------------------------


def test_thinking_blocks_are_dropped_from_a_replayed_turn():
    """An empty text block under omitted display can be rejected by the API,
    which is why the continuation replays only text and server-tool blocks."""
    blocks = [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "redacted_thinking", "data": "…"},
        {"type": "text", "text": "hello"},
        {"type": "server_tool_use", "id": "t1", "name": "web_search", "_partial": "{}"},
    ]
    cleaned = llm._clean_blocks(blocks)
    assert [b["type"] for b in cleaned] == ["text", "server_tool_use"]


def test_internal_accumulator_keys_do_not_go_back_to_the_api():
    cleaned = llm._clean_blocks([{"type": "server_tool_use", "id": "t1", "_partial": "{"}])
    assert "_partial" not in cleaned[0]
    assert cleaned[0]["id"] == "t1"


def test_a_missing_api_key_is_a_clear_error_not_a_traceback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LlmError, match="ANTHROPIC_API_KEY"):
        llm.call(settings(), "sys", "user")
