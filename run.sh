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

# Ubuntu's python is externally managed and has no system-wide pip, so the
# venv is not optional. --system-site-packages keeps the apt-installed
# pydantic rather than building a second copy.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "no .venv yet — creating one and installing from pyproject.toml" >&2
  python3 -m venv --system-site-packages .venv
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet --editable ".[dev]"
fi

exec "$PY" -m dashboard
