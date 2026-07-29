#!/usr/bin/env bash
# The overnight hand-off. Used by the scheduled task that generates the day's
# math problem so the dashboard never has to pay for one.
#
#   ./batch.sh --context             what the generator needs to know
#   ./batch.sh --validate FILE       the server's checks, run at 3am not 8am
#
# Deliberately a script rather than a `python -m ...` command line the task
# writes out for itself. The task reaches this repo as
#
#     wsl -e bash -lc "cd '...' && ./batch.sh --context"
#
# and every `$` in that string is expanded by the OUTER Git Bash shell before
# WSL ever sees it -- so `$HOME` resolves to a Windows-style path and the venv
# is not found. That is not hypothetical: it is exactly how the first
# capability probe failed. A command line containing no `$` at all cannot be
# mangled that way, which is the whole reason this file exists.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=venv.sh
. ./venv.sh

exec "$PY" -m dashboard.batch "$@"
