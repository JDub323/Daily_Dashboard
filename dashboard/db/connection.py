"""Connection setup and transaction helpers.

WAL is the point of the exercise. The historical lost-elo bug was two browser
tabs reading a rating, both computing from it, and both writing back; WAL plus
BEGIN IMMEDIATE for read-modify-write turns that into a serialised update
instead of a silent overwrite.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .schema import migrate

DEFAULT_TIMEOUT_MS = 5_000


def connect(path: str | Path, *, migrate_on_open: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(
        path,
        # Autocommit: transaction boundaries are explicit and visible in the
        # code that needs them, rather than implied by statement type.
        isolation_level=None,
        # The server is threaded; each request may land on a different thread.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_TIMEOUT_MS}")
    if migrate_on_open:
        migrate(conn)
    return conn


@contextmanager
def transaction(
    conn: sqlite3.Connection, *, immediate: bool = True
) -> Iterator[sqlite3.Connection]:
    """Explicit transaction.

    `immediate` takes the write lock up front. Any read-modify-write -- above
    all the elo update, which reads the current rating and the event count
    before writing -- must use it, or two concurrent tabs can both read the
    same rating and the second write silently wins.
    """
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    """One connection per thread, opened lazily.

    FastAPI runs sync endpoints on a threadpool, so requests land on whatever
    thread is free. A single shared connection would work for the reads, but
    two threads inside `transaction()` at once would collide on `BEGIN
    IMMEDIATE` -- SQLite allows one transaction per *connection*, and the
    second would fail with "cannot start a transaction within a transaction"
    rather than waiting on the write lock like it should. Per-thread
    connections plus WAL turn that back into the intended `busy_timeout` wait.

    Migrations run once, on the connection opened here at startup.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        # Migrate up front so a schema failure is a startup failure, not a
        # surprise on whichever request happens to open the second connection.
        self._adopt(connect(self.path, migrate_on_open=True))

    def _adopt(self, conn: sqlite3.Connection) -> sqlite3.Connection:
        self._local.conn = conn
        with self._lock:
            self._all.append(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        return self._adopt(connect(self.path, migrate_on_open=False))

    def close(self) -> None:
        with self._lock:
            connections, self._all = self._all, []
        for conn in connections:
            with suppress(sqlite3.Error):
                conn.close()
        self._local = threading.local()
