"""The pty bridge.

Everything here drives a real forked process on a real pty, because that is
the only thing worth testing about this module -- a mocked `pty.fork` would be
testing the mock. `cat` and `sh` stand in for the editor: they are on every
machine this runs on, and the module has no opinion about which program it is
holding open.

Skipped wholesale off Unix. `pty` is a Unix module and the server runs in WSL.
"""

from __future__ import annotations

import os
import time

import pytest

from dashboard import term

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty is Unix-only")


def drain(session, until: bytes, timeout: float = 5.0) -> bytes:
    """Read until `until` shows up, or give up. Returns everything seen."""
    seen = b""
    end = time.monotonic() + timeout
    while until not in seen and time.monotonic() < end:
        chunk = session.read()
        if not chunk:
            break
        seen += chunk
    return seen


# --- choosing the editor ---------------------------------------------------


def test_the_first_editor_on_path_wins():
    assert term.resolve_editor(["definitely-not-a-program", "cat"]).endswith("cat")


def test_no_editor_at_all_says_what_to_do():
    with pytest.raises(term.TerminalError, match=r"config.toml"):
        term.resolve_editor(["definitely-not-a-program"])


def test_an_empty_editor_list_is_the_same_failure():
    """A config that emptied the list should be told so, not crash later on an
    IndexError somewhere in the websocket handler."""
    with pytest.raises(term.TerminalError):
        term.resolve_editor([])


# --- the session -----------------------------------------------------------


def test_the_process_sees_the_file_and_its_typing(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("the file's own contents\n")

    session = term.Session.spawn("/bin/cat", [], path)
    try:
        assert b"the file's own contents" in drain(session, b"contents")
    finally:
        session.close()


def test_the_configured_commands_become_arguments(tmp_path):
    """`-c` is how the editor is adjusted after the user's own config loads,
    so the ordering of the argv this builds is the whole contract."""
    path = tmp_path / "note.md"
    path.write_text("x\n")

    # `sh -c <cmd> <file>` runs the command with the file as $0, which is a
    # cheap way to observe that both made it through in the right order.
    session = term.Session.spawn("/bin/sh", ["echo ARG-CAME-THROUGH"], path)
    try:
        assert b"ARG-CAME-THROUGH" in drain(session, b"ARG-CAME-THROUGH")
    finally:
        session.close()


def test_writing_reaches_the_process(tmp_path):
    session = term.Session.spawn("/bin/cat", [], tmp_path / "missing.md")
    try:
        session.write(b"typed at it\n")
        assert b"typed at it" in drain(session, b"typed at it")
    finally:
        session.close()


def test_a_closed_process_reads_as_end_of_stream(tmp_path):
    session = term.Session.spawn("/bin/sh", ["exit 0"], tmp_path / "missing.md")
    try:
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            if session.read() == b"":
                break
        else:
            pytest.fail("the pty never reported end of stream")
    finally:
        session.close()


def test_resizing_a_dead_session_is_ignored(tmp_path):
    """A resize racing a `:q` is ordinary — the browser cannot know it lost,
    so this must not raise."""
    session = term.Session.spawn("/bin/sh", ["exit 0"], tmp_path / "missing.md")
    session.close()
    session.resize(40, 100)  # no exception


def test_closing_twice_is_safe(tmp_path):
    session = term.Session.spawn("/bin/cat", [], tmp_path / "missing.md")
    session.close()
    session.close()


def test_a_process_that_ignores_sighup_is_killed(tmp_path):
    """The grace period has to end. Otherwise a wedged editor outlives every
    tab that ever opened it, and they accumulate one a day."""
    ignores_hup = "trap '' HUP; while :; do sleep 1; done"
    session = term.Session.spawn("/bin/sh", [ignores_hup], tmp_path / "x")

    started = time.monotonic()
    session.close()

    assert time.monotonic() - started < term.GRACE_SECONDS * 3
    with pytest.raises(ChildProcessError):
        os.waitpid(session.pid, 0)  # reaped by close(), so nothing is left


def test_a_missing_binary_does_not_take_the_server_with_it(tmp_path):
    """The child either execs or `_exit`s. It must never unwind back through a
    forked copy of the server's stack and run its cleanup a second time."""
    session = term.Session.spawn("/definitely/not/here", [], tmp_path / "x")

    _, status = os.waitpid(session.pid, 0)
    assert os.WEXITSTATUS(status) == 127

    os.close(session.fd)
