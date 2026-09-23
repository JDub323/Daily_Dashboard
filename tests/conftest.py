"""Fixtures.

Everything here is in-memory or in tmp_path. No test touches `dashboard.db`,
`state.json`, the network, or the Anthropic API.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from dashboard.config import Settings, load_settings
from dashboard.db import Database, Repository
from dashboard.models import domain as d

ROOT = Path(__file__).resolve().parent.parent

TODAY = dt.date(2026, 7, 26)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """No test may make a real LLM call. Removing the key turns any attempt
    into a loud LlmError rather than a bill."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """The real config.toml, redirected at a temp database."""
    cfg = load_settings(ROOT / "config.toml")
    return cfg.model_copy(
        update={
            "paths": cfg.paths.model_copy(
                update={
                    "db": str(tmp_path / "test.db"),
                    "metrics": str(tmp_path / "metrics.jsonl"),
                    "problems": str(tmp_path / "problems.json"),
                    "batches": str(tmp_path / "batches"),
                    # Redirected for the same reason as `db`: the app writes
                    # here, and these hold hours of typing. A suite run must
                    # not be able to touch the real write-ups.
                    "investigations": str(tmp_path / "investigations"),
                    # Absent by default, like the real one may be. A test that
                    # wants a profile writes it here -- pointing at the real
                    # path let a test create interests.md in the project root.
                    "interests": str(tmp_path / "interests.md"),
                    # Written by every test that posts a tally.
                    "usage": str(tmp_path / "usage.json"),
                }
            ),
            "server": cfg.server.model_copy(update={"log_file": None, "open_browser": False}),
            # Otherwise every app the suite builds spawns a thread that runs the
            # real gcalcli against the real Google account, several seconds at a
            # time. The tests that want a fitted classifier stub the history and
            # let the lazy fit on first use do it.
            "calendar": cfg.calendar.model_copy(update={"warm_on_start": False}),
        }
    )


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.path("db"))
    yield database
    database.close()


@pytest.fixture
def repo(db) -> Repository:
    return Repository(db.conn)


@pytest.fixture
def problem(repo) -> d.MathProblem:
    return repo.add_problem(
        d.MathProblem(
            source="Putnam 1998 B3",
            tier="putnam-early",
            anchor=1750,
            statement=r"Evaluate $\int_0^1 x^x\,dx$.",
            answer="42",
            hints=["think about it", "think harder", "the answer is 42"],
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 9, 0),
        )
    )


@pytest.fixture
def puzzle(repo) -> d.ChessPuzzle:
    return repo.upsert_puzzle(
        d.ChessPuzzle(
            id="abcd1", rating=1500, themes=["fork"], url="https://lichess.org/training/abcd1"
        )
    )


@pytest.fixture
def client(settings):
    """A TestClient over the real app, wired to the temp database.

    `raise_server_exceptions=False` so the 500 handler's response is what the
    test sees. Starlette's ServerErrorMiddleware calls the handler *and* then
    re-raises, which is right in production — uvicorn logs it — but under the
    default TestClient the re-raise wins and the response is never observed.
    """
    from fastapi.testclient import TestClient

    from dashboard.server import create_app

    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c
