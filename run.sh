#!/usr/bin/env bash
# The one launch path. From inside WSL (or any Linux/macOS shell):  ./run.sh
#
# run.cmd and .claude/launch.json both call this rather than repeating it, so
# there is a single place where "how to start the dashboard" is written down.
# The port is not one of them: that lives in config.toml. (.claude/launch.json
# has to repeat it so the preview tool knows where to connect; the test
# `test_the_real_config_and_launch_json_agree_on_the_port` fails if the two
# ever drift apart.)
set -euo pipefail
cd "$(dirname "$0")"

# --- where the virtualenv lives ---------------------------------------------
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

exec "$PY" -m dashboard
