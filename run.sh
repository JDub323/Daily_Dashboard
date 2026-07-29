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

# Where the virtualenv lives, and how it gets built, is in venv.sh -- shared
# with batch.sh so there is one answer rather than two that drift.
# shellcheck source=venv.sh
. ./venv.sh

exec "$PY" -m dashboard
