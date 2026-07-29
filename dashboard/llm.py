"""The Anthropic Messages client. One implementation.

`dashboard.py` had two -- `llm()` and `llm_stream()` -- duplicating the key
check, the payload build, the `pause_turn` continuation loop and the no-text
error, with *divergent* continuation logic: only the streaming one cleaned
thinking blocks out of the replayed assistant turn, so the non-streaming path
could be rejected by the API on any continuation. That is the whole reason for
deleting it rather than keeping it as the "simple" option.

What is left is `call()`, which always streams over the wire. Streaming is not
a feature here, it is what keeps the socket warm through a multi-minute
web-search run; callers who only want the text call `text()` and ignore the
metrics. `json_call()` adds the extraction and LaTeX repair on top.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from . import logs
from .config import LlmSettings
from .models.common import Base

log = logs.get(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LlmError(RuntimeError):
    """Anything that stops a generation producing usable text."""


class Metrics(Base):
    """Per-generation accounting, written to metrics.jsonl."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    web_searches: int = 0
    pause_continuations: int = 0

    def absorb(self, usage: dict[str, Any]) -> None:
        """Merge one request's `usage` object, keeping the larger of each field.

        Usage arrives twice per request and the second report supersedes the
        first. `message_start` carries the input side as known at the *start*
        of the turn; `message_delta` carries the cumulative usage for the whole
        request -- including every token a server-side tool pulled into the
        context mid-turn.

        Those search results are the bulk of a verified generation's bill.
        Reading only `output_tokens` off `message_delta` -- which is what this
        did -- undercounted a four-search problem by roughly 5x, which is how
        an $0.80 generation came to be logged at $0.17.

        `max` rather than assignment, because a `message_delta` that omits a
        field must not zero what `message_start` already reported.
        """
        self.input_tokens = max(self.input_tokens, usage.get("input_tokens", 0))
        self.output_tokens = max(self.output_tokens, usage.get("output_tokens", 0))
        self.cache_read = max(self.cache_read, usage.get("cache_read_input_tokens", 0))
        self.cache_creation = max(self.cache_creation, usage.get("cache_creation_input_tokens", 0))
        # The server's own tally, which beats counting blocks as they stream by.
        served = usage.get("server_tool_use") or {}
        self.web_searches = max(self.web_searches, served.get("web_search_requests", 0))

    def add(self, other: Metrics) -> None:
        """Accumulate one request into a whole generation's running total.

        Separate from `absorb` because the two compose differently: fields are
        *maxed* within a request (two reports of the same number) and *summed*
        across the pause_turn continuations that make up one generation.
        """
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_creation += other.cache_creation
        self.web_searches += other.web_searches


StatusFn = Callable[[str], None] | None


def web_search_tool(max_uses: int) -> list[dict[str, Any]]:
    return [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LlmError("ANTHROPIC_API_KEY missing from .env")
    return key


def _clean_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assistant content to replay on a `pause_turn` continuation.

    Keeps text and paired server-tool blocks; drops thinking (an empty text
    block under omitted display can be rejected) and the internal `_partial`
    accumulator.
    """
    out = []
    for b in blocks:
        if b.get("type") in ("thinking", "redacted_thinking"):
            continue
        out.append({k: v for k, v in b.items() if not k.startswith("_")})
    return out


def call(
    settings: LlmSettings,
    system: str,
    user: str,
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    on_status: StatusFn = None,
    metrics: Metrics | None = None,
) -> tuple[str, Metrics]:
    """Run one Messages request to completion and return (text, metrics).

    Follows `pause_turn` continuations, which is how server-side web search
    reports back mid-turn, up to `max_pause_continuations`.

    Pass `metrics` to keep the accounting when this raises. A generation that
    fails on its third continuation has still been paid for, and a caller that
    only reads the return value logs that spend as zero.
    """
    key = _api_key()

    def status(s: str) -> None:
        if on_status:
            on_status(s)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    m = metrics if metrics is not None else Metrics()

    for _ in range(settings.max_pause_continuations):
        payload: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": max_tokens or settings.max_tokens,
            "stream": True,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        blocks, stop_reason = _stream_once(settings, key, payload, m, status)

        if stop_reason == "pause_turn":
            m.pause_continuations += 1
            messages = [
                {"role": "user", "content": user},
                {"role": "assistant", "content": _clean_blocks(blocks)},
            ]
            status("still verifying…")
            continue

        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise LlmError(f"LLM returned no text (stop_reason={stop_reason})")
        return text, m

    raise LlmError("LLM kept pausing (too many web searches?)")


def _stream_once(
    settings: LlmSettings,
    key: str,
    payload: dict[str, Any],
    m: Metrics,
    status: Callable[[str], None],
) -> tuple[list[dict[str, Any]], str | None]:
    """One SSE request. Accumulates content blocks and usage into `m`.

    Usage for *this* request is gathered separately and folded into `m` at the
    end, because the two reports of it have to be reconciled (`Metrics.absorb`)
    before they can be added to the running total for the generation.
    """
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
    )
    blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    stop_reason: str | None = None
    used = Metrics()

    try:
        resp = urllib.request.urlopen(request, timeout=settings.timeout_seconds)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        log.error("Anthropic API %s: %s", e.code, detail)
        raise LlmError(f"Anthropic API returned {e.code}") from e
    except OSError as e:
        raise LlmError(f"could not reach the Anthropic API: {e}") from e

    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                t = ev.get("type")

                if t == "message_start":
                    used.absorb(ev["message"].get("usage", {}))
                    status("thinking about a problem…")

                elif t == "content_block_start":
                    cb = ev["content_block"]
                    cur = {"type": cb["type"]}
                    if cb["type"] == "text":
                        cur["text"] = ""
                        status("writing it up…")
                    elif cb["type"] == "thinking":
                        cur["thinking"] = ""
                    elif cb["type"] == "server_tool_use":
                        cur.update(
                            {
                                "id": cb.get("id"),
                                "name": cb.get("name"),
                                "input": cb.get("input", {}),
                                "_partial": "",
                            }
                        )
                        if cb.get("name") == "web_search":
                            # Provisional: counted as the blocks stream by so the
                            # status line can say "search 2" while it is happening.
                            # `message_delta` reports the server's own tally at the
                            # end, and `absorb` prefers whichever is larger.
                            used.web_searches += 1
                            status(
                                "verifying against AoPS… "
                                f"(search {m.web_searches + used.web_searches})"
                            )
                    elif cb["type"] == "web_search_tool_result":
                        cur.update(
                            {"tool_use_id": cb.get("tool_use_id"), "content": cb.get("content")}
                        )
                        status("checking the source…")
                    else:
                        cur.update(dict(cb))

                elif t == "content_block_delta":
                    if cur is None:
                        continue
                    d = ev.get("delta", {})
                    if d.get("type") == "text_delta":
                        cur["text"] = cur.get("text", "") + d.get("text", "")
                    elif d.get("type") == "thinking_delta":
                        cur["thinking"] = cur.get("thinking", "") + d.get("thinking", "")
                    elif d.get("type") == "input_json_delta":
                        cur["_partial"] = cur.get("_partial", "") + d.get("partial_json", "")

                elif t == "content_block_stop":
                    if cur is not None:
                        if cur.get("type") == "server_tool_use" and cur.get("_partial"):
                            try:
                                cur["input"] = json.loads(cur["_partial"])
                            except json.JSONDecodeError:
                                log.warning("could not parse server_tool_use input json")
                        blocks.append(cur)
                        cur = None

                elif t == "message_delta":
                    stop_reason = ev.get("delta", {}).get("stop_reason", stop_reason)
                    used.absorb(ev.get("usage", {}))

                elif t == "error":
                    err = ev.get("error", {})
                    raise LlmError(f"stream error: {err.get('type')}: {err.get('message')}")
    finally:
        # A stream that dies halfway has still spent every token it read. Fold
        # the partial tally in on the way out so the failure is logged with its
        # real cost rather than as free.
        m.add(used)

    return blocks, stop_reason


def text(settings: LlmSettings, system: str, user: str, **kw: Any) -> str:
    """`call()` for the callers that never look at the metrics."""
    out, _ = call(settings, system, user, **kw)
    return out


# --- JSON extraction -------------------------------------------------------

_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.S)

# Backslashes that a model meant as LaTeX but wrote as if they were JSON
# escapes. Alternatives, in order:
#
#   1. `\\` -- already escaped. Captured and passed through untouched, which
#      is what stops the substitution from mangling correct input.
#   2. `\b \f \n \r \t` followed by a *lowercase* letter -- `\frac`,
#      `\binom`, `\theta`, `\times`, `\neq`, `\rho`. These are the dangerous
#      ones: they are legal JSON escapes, so the document parses, and the
#      maths is silently replaced by a control character. Control words that
#      begin with a capital (`\Delta`, `\Sigma`, `\Rightarrow`) are safe
#      already, since D/S/R are not JSON escape letters.
#   3. `\u` not followed by four hex digits -- `\underbrace`.
#   4. Anything else that is not a legal escape at all -- `\dfrac`, `\sum`.
_UNESCAPED = re.compile(
    r"(\\\\)"
    r"|\\([bfnrt])(?=[a-z])"
    r"|\\(u)(?![0-9a-fA-F]{4})"
    r'|\\(?![\\"/bfnrtu])'
)


def _repair_latex(text: str) -> str:
    """Double the backslashes a model left singly-escaped."""
    return _UNESCAPED.sub(lambda m: m.group(1) or ("\\\\" + (m.group(2) or m.group(3) or "")), text)


def extract_json(raw: str) -> Any:
    """Parse the JSON object or array out of a model response.

    Pure, and separated from the network call precisely so it can be tested
    without one -- this is the single most defect-prone code in the old
    server, and it had a bug that only a test could have found.

    The old version repaired *only after* a strict parse had failed. That
    misses the case the repair exists for: `{"s": "$\\frac{n}{2}$"}` is
    perfectly valid JSON, because `\\f` is a formfeed, so it parsed happily
    and handed back a statement with the maths replaced by a control
    character. `\\theta`, `\\times`, `\\neq`, `\\binom` and `\\rho` all did
    the same. It only ever looked correct because a document usually *also*
    contains something like `\\sum` or `\\dfrac`, which is not a legal escape
    and so failed the strict parse and sent the whole thing through repair.

    Repair now runs first, and the strict parse is the fallback.

    Known trade-off: a genuine newline written as `\\n` immediately before a
    lowercase word ("first\\nsecond") is read as LaTeX and comes out as a
    literal `\\n`. That is a visible cosmetic wart in a hint, where the old
    behaviour was silent corruption of a formula. The lowercase restriction
    keeps the common "\\nStep 2" case working.
    """
    m = _BLOCK.search(raw)
    candidate = m.group(0) if m else raw
    try:
        return json.loads(_repair_latex(candidate))
    except json.JSONDecodeError:
        # Repair made it worse, or it was never JSON. Let the unmodified text
        # produce the error the caller sees.
        return json.loads(candidate)


def json_call(settings: LlmSettings, system: str, user: str, **kw: Any) -> Any:
    """A Messages call whose response is expected to be JSON."""
    out, _ = call(settings, system, user, **kw)
    try:
        return extract_json(out)
    except json.JSONDecodeError as e:
        log.error("unparseable LLM JSON: %s", out[:1000])
        raise LlmError("the model did not return usable JSON") from e


# --- cost accounting -------------------------------------------------------


class GenerationRecord(Base):
    """One line of metrics.jsonl. Was an ad-hoc dict."""

    ts: str
    date: str
    model: str
    kind: str
    ok: bool
    wall_seconds: float
    topic: str | None = None
    rating: int | None = None
    source: str | None = None
    tier: str | None = None
    error: str | None = None
    est_cost_usd: float | None = None
    metrics: Metrics = Field(default_factory=Metrics)


def est_cost(settings: LlmSettings, model: str, m: Metrics) -> float:
    p = settings.price(model)
    return round(
        m.input_tokens * p.input
        + m.output_tokens * p.output
        + m.web_searches * settings.web_search_cost_usd,
        4,
    )


def log_generation(path: Path, rec: GenerationRecord) -> None:
    """Append to metrics.jsonl. Never raises: a failed metrics write must not
    lose a problem that was successfully generated and paid for."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")
    except OSError:
        log.exception("could not append to %s", path)


def record(
    *,
    kind: str,
    date: dt.date,
    model: str,
    started: float,
    settings: LlmSettings,
    metrics: Metrics | None = None,
    ok: bool,
    error: str | None = None,
    **extra: Any,
) -> GenerationRecord:
    m = metrics or Metrics()
    return GenerationRecord(
        ts=dt.datetime.now().isoformat(timespec="seconds"),
        date=date.isoformat(),
        model=model,
        kind=kind,
        ok=ok,
        wall_seconds=round(time.time() - started, 1),
        error=error,
        metrics=m,
        # `is not None`, not a truthiness test: a Metrics of all zeros is a
        # real measurement (the call never reached the API) and costs $0.00,
        # which is a different claim from "not measured".
        est_cost_usd=est_cost(settings, model, m) if metrics is not None else None,
        **extra,
    )
