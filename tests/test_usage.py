"""`POST /api/usage` -- the phone's screen-open counts.

The phone sends every day it holds on every send, so the whole contract is that
sending the same thing twice, or an older copy after a newer one, changes
nothing. See `dashboard/usage.py`.
"""

from __future__ import annotations

import json

import pytest


def held(settings):
    return json.loads(settings.path("usage").read_text(encoding="utf-8"))


def test_a_tally_is_kept_by_day_and_screen(client, settings):
    body = {"days": {"2026-09-22": {"open": 4, "eat": 3}, "2026-09-23": {"agenda": 1}}}
    reply = client.post("/api/usage", json=body)
    assert reply.status_code == 200
    assert reply.json() == {"usage_days": 2}
    assert held(settings) == body["days"]


def test_a_resend_and_a_stale_copy_change_nothing(client, settings):
    newer = {"days": {"2026-09-23": {"open": 5, "eat": 4}}}
    older = {"days": {"2026-09-23": {"open": 2, "eat": 4, "todo": 1}}}
    client.post("/api/usage", json=newer)
    client.post("/api/usage", json=newer)
    client.post("/api/usage", json=older)
    # The larger of each, and a screen only the older copy had is still kept.
    assert held(settings) == {"2026-09-23": {"open": 5, "eat": 4, "todo": 1}}


@pytest.mark.parametrize(
    "days",
    [
        {"2026-09-23": {"Eat": 1}},             # not a name tally.js would write
        {"2026-09-23": {"eat": -1}},
        {"not-a-date": {"eat": 1}},
        {"2026-09-23": {f"s{i}": 1 for i in range(41)}},
    ],
)
def test_a_malformed_tally_is_a_422(client, settings, days):
    assert client.post("/api/usage", json={"days": days}).status_code == 422
    assert not settings.path("usage").exists()


def test_an_unreadable_file_is_started_over(client, settings):
    settings.path("usage").write_text("{half a file", encoding="utf-8")
    client.post("/api/usage", json={"days": {"2026-09-23": {"open": 1}}})
    assert held(settings) == {"2026-09-23": {"open": 1}}
