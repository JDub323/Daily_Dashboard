"""Startup guards.

Both of these exist because of one incident: a pre-rewrite `python3
dashboard.py` was still holding port 8502 days later. The new server could not
bind and exited, while the browser tab -- opened on a fixed timer, without
regard to whether anything had started -- landed on the *old* server, which
answered with an empty reply because its static files had been deleted. The
symptom looked like a broken page rather than a failed start.
"""

from __future__ import annotations

import socket
import threading

import pytest

from dashboard import server


class FakeServer:
    """Stands in for uvicorn.Server, which only exposes these two flags."""

    def __init__(self, started=False, should_exit=False):
        self.started = started
        self.should_exit = should_exit


# --- port probe ------------------------------------------------------------


def test_a_free_port_is_reported_free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    # The socket is closed, so nothing is listening on that number now.
    assert server.port_is_free("127.0.0.1", free_port)


def test_an_occupied_port_is_reported_occupied():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert not server.port_is_free("127.0.0.1", port)


def test_the_probe_does_not_leave_the_port_bound():
    """It has to release what it binds, or the real server cannot start."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert server.port_is_free("127.0.0.1", port)
    assert server.port_is_free("127.0.0.1", port)  # twice, to be sure

    with socket.socket() as real:
        real.bind(("127.0.0.1", port))  # would raise if the probe held it


def test_main_refuses_to_start_on_an_occupied_port(settings, monkeypatch, caplog):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]

        cfg = settings.model_copy(
            update={"server": settings.server.model_copy(update={"port": port})}
        )
        monkeypatch.setattr(server, "settings", lambda: cfg)
        opened = []
        monkeypatch.setattr(server, "_open", opened.append)

        with pytest.raises(SystemExit) as exit_info:
            server.main()

    assert exit_info.value.code == 1
    assert "already in use" in caplog.text
    assert str(port) in caplog.text
    # And crucially: no tab was opened onto whatever is on that port.
    assert opened == []


# --- browser timing --------------------------------------------------------


def test_the_tab_waits_for_the_server_to_start(monkeypatch):
    """Was `threading.Timer(0.8, ...)`, which assumed the server was up 0.8s
    after main() began. Importing FastAPI takes longer than that."""
    opened = []
    monkeypatch.setattr(server, "_open", opened.append)
    fake = FakeServer()

    thread = threading.Thread(target=server._open_when_ready, args=(fake, "http://x"), daemon=True)
    thread.start()
    thread.join(0.3)
    assert opened == []  # still starting — no tab yet

    fake.started = True
    thread.join(2)
    assert opened == ["http://x"]


def test_no_tab_is_opened_when_startup_fails(monkeypatch):
    opened = []
    monkeypatch.setattr(server, "_open", opened.append)
    server._open_when_ready(FakeServer(should_exit=True), "http://x")
    assert opened == []


def test_giving_up_says_where_to_look(monkeypatch, caplog):
    opened = []
    monkeypatch.setattr(server, "_open", opened.append)
    server._open_when_ready(FakeServer(), "http://x", timeout=0.2)
    assert opened == []
    assert "not opening a tab" in caplog.text
