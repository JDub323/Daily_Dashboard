#!/usr/bin/env bash
# Resolves $PY -- the project interpreter -- creating the virtualenv on first
# use. Sourced, not executed: by run.sh and by batch.sh, so that "where is
# Python" is written down exactly once. Callers have already cd'd to the
# project root.
#
# NOT in the project directory when the project is on a Windows drive. Every
# import from a venv under /mnt/c is a Windows syscall round trip through
# drvfs: `import fastapi` measured 13s from /mnt/c against 0.9s from the Linux
# filesystem. That is not a tuning detail -- a 15-second startup is long
# enough that the browser tab opens before the server is listening.
#
# Elsewhere (native Linux, macOS) an in-project .venv is conventional and
# costs nothing, so keep it there.

VENV="${DASHBOARD_VENV:-}"
if [ -z "$VENV" ]; then
  case "$PWD" in
    /mnt/*) VENV="${XDG_DATA_HOME:-$HOME/.local/share}/daily-dashboard/venv" ;;
    *)      VENV=".venv" ;;
  esac
fi

PY="$VENV/bin/python"

# Ubuntu's python is externally managed and has no system-wide pip, so the
# venv is not optional. --system-site-packages keeps the apt-installed
# pydantic rather than building a second copy.
if [ ! -x "$PY" ]; then
  echo "creating a virtualenv at $VENV (first run only)" >&2
  python3 -m venv --system-site-packages "$VENV"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet --editable ".[dev]"
fi
