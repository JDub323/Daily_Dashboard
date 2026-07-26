"""Logging setup.

The old server had none. `log_message` was overridden to `pass`, and every
exception anywhere was flattened to `str(e)[:300]` and painted into a panel,
so no traceback survived anywhere in the process. A parser bug and a network
outage produced the same red text and left nothing to read afterwards.

Two rules hold everywhere above this module:

1. The panel gets a short message. The log gets the traceback. Never only the
   first of those.
2. `logger.exception` inside an `except`, never `logger.error(str(e))`.

Named `logs` rather than `logging` so that `import logging` inside this
package keeps meaning the stdlib.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
CONSOLE_DATEFMT = "%H:%M:%S"  # the date is today; the file keeps the full one
MAX_BYTES = 2_000_000
BACKUPS = 3

_configured = False


def setup(level: str = "INFO", file: str | Path | None = None) -> None:
    """Configure root logging: always stderr, plus a rotating file if given.

    Idempotent -- uvicorn's reloader and the test suite both import this more
    than once, and stacking duplicate handlers would double every line.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(FORMAT, CONSOLE_DATEFMT))
    root.addHandler(stream)

    if file:
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
        rotating.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(rotating)

    # The access log is one line per request on a single-user localhost app;
    # it drowns everything worth reading. Errors from uvicorn still surface.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
