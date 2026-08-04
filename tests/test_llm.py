"""JSON extraction and the LaTeX backslash repair.

`extract_json` is the single most defect-prone line the old server had: a
regex repairing invalid JSON produced by a model, run on every generation,
with no way to exercise it short of paying for one.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import time

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


def test_a_verified_generation_lands_in_the_expected_range():
    """The budget tripwire, rebuilt against a real invoice.

    It used to assert $0.02-0.04 for a 6k-input, 2-search generation — and it
    passed, because both the number and the shape came from a log that was
    undercounting search input tokens by ~5x. The estimate and the meter
    agreed with each other and not with the Anthropic console.

    These figures are reverse-engineered from an observed $0.80 charge: four
    searches drag in a few hundred thousand input tokens, and *that* is the
    bill. Output and the per-search fee together are under a quarter of it.
    """
    cfg = settings(web_search_cost_usd=0.01)
    typical = llm.Metrics(input_tokens=320_000, output_tokens=11_700, web_searches=4)
    cost = llm.est_cost(cfg, "claude-sonnet-5", typical)
    assert cost == pytest.approx(0.80, abs=0.01)
    assert 0.15 <= cost <= 1.00


def test_search_input_tokens_dominate_the_bill():
    """Why `max_uses` is a cost control and not just a latency guard: the
    per-search fee is noise next to what the results cost to read."""
    cfg = settings(web_search_cost_usd=0.01)
    m = llm.Metrics(input_tokens=320_000, output_tokens=11_700, web_searches=4)
    search_fees = m.web_searches * cfg.web_search_cost_usd
    assert search_fees / llm.est_cost(cfg, "claude-sonnet-5", m) < 0.1


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


# --- usage accounting ------------------------------------------------------


def sse(*events: dict) -> io.BytesIO:
    """A fake SSE body. `BytesIO` iterates by line and is a context manager,
    which is the whole interface `_stream_once` asks of a response."""
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return io.BytesIO(body.encode())


def stream(monkeypatch, *events: dict, into: llm.Metrics | None = None):
    """Run `_stream_once` against a canned event sequence."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **kw: sse(*events))
    m = into if into is not None else llm.Metrics()
    blocks, stop_reason = llm._stream_once("k", {}, m, lambda _s: None, 60)
    return m, blocks, stop_reason


def test_absorb_takes_the_larger_of_the_two_usage_reports():
    """`message_start` reports the input side as known at the start of the
    turn; `message_delta` reports it cumulatively for the whole request."""
    m = llm.Metrics()
    m.absorb({"input_tokens": 2679, "output_tokens": 3})
    m.absorb({"input_tokens": 10682, "output_tokens": 510})
    assert (m.input_tokens, m.output_tokens) == (10682, 510)


def test_absorb_does_not_let_an_omitted_field_zero_a_reported_one():
    """A `message_delta` carrying only `output_tokens` must not erase the
    input count `message_start` already gave us."""
    m = llm.Metrics()
    m.absorb({"input_tokens": 2679})
    m.absorb({"output_tokens": 15})
    assert m.input_tokens == 2679


def test_web_search_result_tokens_are_counted(monkeypatch):
    """The bug that made an $0.80 problem log as $0.17.

    Every token a server-side search pulls into the context is billed as
    input, and it only shows up in the final `message_delta`. Reading input
    off `message_start` alone counts the prompt and none of the search.

    The numbers are the ones in Anthropic's own streaming example for a
    single-search request: 2679 at the start, 10682 at the end.
    """
    m, _, _ = stream(
        monkeypatch,
        {"type": "message_start", "message": {"usage": {"input_tokens": 2679, "output_tokens": 3}}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": 10682,
                "output_tokens": 510,
                "server_tool_use": {"web_search_requests": 1},
            },
        },
    )
    assert m.input_tokens == 10682
    assert m.output_tokens == 510
    assert m.web_searches == 1


def test_usage_sums_across_pause_turn_continuations(monkeypatch):
    """Maxed *within* a request, summed *across* them. Two continuations of
    10k input each are 20k spent, not 10k."""
    m = llm.Metrics()
    for _ in range(2):
        stream(
            monkeypatch,
            {"type": "message_start", "message": {"usage": {"input_tokens": 2000}}},
            {"type": "message_delta", "delta": {}, "usage": {"input_tokens": 10_000}},
            into=m,
        )
    assert m.input_tokens == 20_000


def test_the_server_search_count_beats_counting_blocks(monkeypatch):
    """Blocks are counted as they stream so the status line can say "search
    2" while it happens; the server's own tally is authoritative at the end."""
    m, _, _ = stream(
        monkeypatch,
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "server_tool_use", "name": "web_search"},
        },
        {
            "type": "message_delta",
            "delta": {},
            "usage": {"server_tool_use": {"web_search_requests": 4}},
        },
    )
    assert m.web_searches == 4


def test_a_stream_that_dies_still_reports_what_it_spent(monkeypatch):
    """A generation that fails on its last search has still been paid for.
    Logging that as $0.00 is how spend goes missing."""
    m = llm.Metrics()
    with pytest.raises(llm.LlmError, match="stream error"):
        stream(
            monkeypatch,
            {"type": "message_start", "message": {"usage": {"input_tokens": 9_000}}},
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
            into=m,
        )
    assert m.input_tokens == 9_000


def test_cost_of_a_failed_call_is_recorded_not_left_unknown():
    """`record` distinguishes "measured, and it was zero" from "not
    measured" — the second is what a missing metrics object means."""
    cfg = settings()
    spent = llm.record(
        kind="math",
        date=dt.date(2026, 7, 26),
        model="claude-sonnet-5",
        started=time.time(),
        settings=cfg,
        metrics=llm.Metrics(input_tokens=6_000, output_tokens=1_000),
        ok=False,
        error="boom",
    )
    assert spent.ok is False
    assert spent.est_cost_usd == pytest.approx(0.022)

    unmeasured = llm.record(
        kind="math",
        date=dt.date(2026, 7, 26),
        model="claude-sonnet-5",
        started=time.time(),
        settings=cfg,
        ok=False,
        error="never reached the API",
    )
    assert unmeasured.est_cost_usd is None


# --- timeouts and retries --------------------------------------------------
#
# The reported failure was "could not reach the Anthropic API: The read
# operation timed out" on a word grading -- a call that should take seconds.
# Two defects behind it: the timeout was read as a budget for the whole call
# when `urlopen` makes it a *per-read* socket timeout (so a dead connection
# hung for the full 300s), and nothing retried.


class Stall(io.BytesIO):
    """An SSE body that yields `before` and then times out, the way a socket
    whose peer stopped talking does."""

    def __init__(self, *before: dict):
        super().__init__("".join(f"data: {json.dumps(e)}\n\n" for e in before).encode())

    def __next__(self):
        line = self.readline()
        if line:
            return line
        raise TimeoutError("timed out")


def responder(monkeypatch, *bodies):
    """Serve one canned response per call, so a retry sees the next one.
    Returns a list the attempts are appended to."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    attempts = []

    def fake(*_a, **_kw):
        body = bodies[min(len(attempts), len(bodies) - 1)]
        attempts.append(body)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    return attempts


DONE = (
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
)


def test_a_timeout_before_anything_streamed_is_retried_not_surfaced(monkeypatch):
    """The reported bug. Nothing was billed, so trying again is free and is
    what a person would do by hand."""
    attempts = responder(monkeypatch, TimeoutError("The read operation timed out"), sse(*DONE))
    text, _ = llm.call(settings(), "sys", "user")
    assert text == "ok"
    assert len(attempts) == 2


def test_a_retry_is_not_charged_against_the_pause_budget(monkeypatch):
    """A flaky socket must not report itself as 'LLM kept pausing', which
    sends you looking at web search instead of the network."""
    responder(monkeypatch, TimeoutError("timed out"), sse(*DONE))
    text, _ = llm.call(settings(max_pause_continuations=1), "sys", "user")
    assert text == "ok"


def test_retries_are_bounded_and_end_as_a_clean_llm_error(monkeypatch):
    attempts = responder(monkeypatch, TimeoutError("The read operation timed out"))
    with pytest.raises(llm.LlmError, match="could not reach the Anthropic API"):
        llm.call(settings(max_retries=2), "sys", "user")
    assert len(attempts) == 3  # the first go, plus two retries


def test_a_stall_after_content_arrived_is_not_retried(monkeypatch):
    """Those tokens are billed. Retrying would buy them twice and risk
    stitching two half-generations together."""
    attempts = responder(
        monkeypatch,
        Stall(
            {"type": "message_start", "message": {"usage": {"input_tokens": 900}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        sse(*DONE),
    )
    with pytest.raises(llm.LlmTransportError) as caught:
        llm.call(settings(), "sys", "user")
    assert caught.value.spent is True
    assert len(attempts) == 1


def test_a_mid_stream_stall_is_an_llm_error_not_a_bare_timeout(monkeypatch):
    """The socket timeout governs the streaming reads too, and that loop used
    to sit outside the `except OSError` -- so this escaped as a raw
    TimeoutError, missed the LlmError handler and became a 500."""
    responder(monkeypatch, Stall())
    with pytest.raises(llm.LlmError, match="stopped responding mid-stream"):
        llm.call(settings(max_retries=0), "sys", "user")


def test_what_a_stalled_generation_spent_is_still_recorded(monkeypatch):
    """A stream that dies halfway has still paid for everything it read."""
    responder(
        monkeypatch,
        Stall({"type": "message_start", "message": {"usage": {"input_tokens": 4_200}}}),
    )
    m = llm.Metrics()
    with pytest.raises(llm.LlmError):
        llm.call(settings(max_retries=0), "sys", "user", metrics=m)
    assert m.input_tokens == 4_200


def test_a_rate_limit_is_retried_but_a_bad_request_is_not(monkeypatch):
    def http(code):
        return llm.urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b"{}"))

    attempts = responder(monkeypatch, http(429), sse(*DONE))
    text, _ = llm.call(settings(), "sys", "user")
    assert text == "ok"
    assert len(attempts) == 2

    attempts = responder(monkeypatch, http(400))
    with pytest.raises(llm.LlmError, match="400"):
        llm.call(settings(), "sys", "user")
    assert len(attempts) == 1  # a malformed request fails identically every time


def test_a_toolless_call_gets_the_short_silence_budget(monkeypatch):
    """The reported bug in one line. Word grading has never taken over 12.2s,
    and it was sharing the 300s budget sized for a four-search math
    generation — so a stall sat there for five minutes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}

    def fake(_request, timeout=None):
        seen["timeout"] = timeout
        return sse(*DONE)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    cfg = settings(timeout_seconds=300, quick_timeout_seconds=60)

    llm.call(cfg, "sys", "grade these")
    assert seen["timeout"] == 60

    # Search is what makes a call slow, so the presence of tools — not the
    # caller's memory — is what buys the long budget.
    llm.call(cfg, "sys", "find a problem", tools=llm.web_search_tool(4))
    assert seen["timeout"] == 300

    llm.call(cfg, "sys", "u", timeout=5)
    assert seen["timeout"] == 5
