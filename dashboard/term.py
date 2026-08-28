"""A terminal editor on a pty, bridged to a websocket.

This is the one module in the app that hands a browser tab a real process. It
exists because the alternative was worse: a vim *emulation* in a textarea is
always a subset, and the subset you notice missing is whichever motion you
reached for without thinking. A pty running the real editor has no subset --
your config, your plugins and your muscle memory all arrive with it.

What that buys is worth being explicit about the cost. An editor is a shell:
`:!sh` inside nvim is a prompt, and nothing here can or should try to prevent
that. So the guards are about *who* reaches this, not what they do once here:

  * The command is assembled from config and one server-chosen path. Nothing
    the client sends is ever an argument -- the socket carries keystrokes and
    a window size, and that is the whole protocol.
  * The connection is refused from anywhere but loopback. Every other endpoint
    in this app is equally unauthenticated, but the rest of them expose a
    reading and a rating; this one exposes a shell, and `[server].host` is a
    config line somebody could plausibly widen without thinking about it.

Linux only, in practice. `pty` is a Unix module and the server runs in WSL --
which is the same reason `os.forkpty` is safe to reach for here at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import logging
import os
import pty
import shutil
import signal
import struct
import termios
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Bytes per read. One screenful of a repainting editor is a few KB; this is
#: large enough that a `:e` on a long file is one or two reads rather than
#: fifty, and small enough to stay well inside a websocket frame.
CHUNK = 65_536

#: How long to wait for the editor to die after the socket closes, before
#: SIGKILL. Long enough for nvim to clean up its swap file, short enough that
#: a wedged process cannot outlive the tab that opened it.
GRACE_SECONDS = 3.0


class TerminalError(RuntimeError):
    """No usable editor, or the pty could not be opened."""


def resolve_editor(candidates: list[str]) -> str:
    """The first configured editor actually on PATH.

    Raises rather than falling back to a default. "No editor" is a setup
    problem with a one-line fix, and guessing at `vi` would produce a panel
    that opens something the user did not ask for and cannot drive.
    """
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    raise TerminalError(
        f"none of {', '.join(candidates) or '(nothing)'} is on PATH — "
        "install one, or set [investigate].editor in config.toml"
    )


class Session:
    """One editor process and the pty it is attached to."""

    def __init__(self, pid: int, fd: int, path: Path) -> None:
        self.pid = pid
        self.fd = fd
        self.path = path

    @classmethod
    def spawn(
        cls, editor: str, commands: list[str], path: Path, *, cwd: Path | None = None
    ) -> Session:
        """Fork the editor onto a new pty.

        `-c` rather than `-u`: the commands run *after* the user's own config
        instead of replacing it, which is the difference between adjusting an
        editor and handing back a stranger's.

        The argument list is closed. `path` is chosen by the server from a
        database row, and there is no branch anywhere that appends something a
        client sent.
        """
        argv = [editor]
        for command in commands:
            argv += ["-c", command]
        argv.append(str(path))

        pid, fd = pty.fork()
        if pid == 0:  # child — never returns
            try:
                if cwd is not None:
                    os.chdir(cwd)
                # A terminal that says "dumb" gets an editor that draws like
                # it. This is what xterm.js implements, so it is also true.
                os.environ["TERM"] = "xterm-256color"
                os.execvp(argv[0], argv)
            except BaseException:
                # _exit, not sys.exit: an exception here would unwind back
                # through the parent's stack frames in a forked copy of the
                # server and run its cleanup twice.
                os._exit(127)
        return cls(pid, fd, path)

    def resize(self, rows: int, cols: int) -> None:
        """Tell the pty its new size, so the editor redraws to fit.

        Silently ignored once the process is gone: a resize racing a `:q` is
        ordinary, and the browser cannot know it lost.
        """
        with contextlib.suppress(OSError):
            fcntl.ioctl(
                self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
            )

    def write(self, data: bytes) -> None:
        with contextlib.suppress(OSError):
            os.write(self.fd, data)

    def read(self) -> bytes:
        """Whatever is waiting, or b"" once the child has gone.

        EIO is how a pty master reports "the slave side closed", which is the
        normal end of every session here -- it is what `:wq` looks like from
        this end, not an error.
        """
        try:
            return os.read(self.fd, CHUNK)
        except OSError as e:
            if e.errno == errno.EIO:
                return b""
            raise

    def close(self) -> None:
        """Stop the editor and reap it. Blocks, so call it off the event loop.

        SIGHUP first, which is what a terminal emulator sends when its window
        closes and what nvim reads as "write the swap file and go". SIGKILL
        only if that is ignored -- and a swap file left behind by the kill is
        the correct outcome there, since it is the only remaining record of
        whatever was unsaved.
        """
        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGHUP)
        if not self._reap(GRACE_SECONDS):
            log.warning("editor %d ignored SIGHUP; killing it", self.pid)
            with contextlib.suppress(OSError):
                os.kill(self.pid, signal.SIGKILL)
            self._reap(GRACE_SECONDS)
        with contextlib.suppress(OSError):
            os.close(self.fd)

    def _reap(self, timeout: float) -> bool:
        """waitpid until it exits or `timeout` passes. True once it is gone."""
        end = time.monotonic() + timeout
        while True:
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return True  # somebody already reaped it
            if pid:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.05)


async def bridge(
    send: asyncio.Queue[bytes | None], session: Session, loop: asyncio.AbstractEventLoop
) -> None:
    """Pump the pty into `send` until the editor exits.

    `add_reader` rather than a thread: the fd is a pty master on Linux and the
    loop can watch it directly, which keeps the ordering of the output frames
    the loop's problem rather than a queue-and-lock dance of ours.

    A `None` on the queue is the end-of-stream marker. The websocket handler
    turns it into a close, so "the editor exited" and "the socket dropped" stay
    two different events rather than one ambiguous silence.
    """
    done = loop.create_future()

    def readable() -> None:
        try:
            data = session.read()
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(session.fd)
            if not done.done():
                done.set_result(None)
            return
        send.put_nowait(data)

    loop.add_reader(session.fd, readable)
    try:
        await done
    finally:
        with contextlib.suppress(ValueError, OSError):
            loop.remove_reader(session.fd)
        send.put_nowait(None)
