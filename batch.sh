#!/usr/bin/env bash
# The overnight hand-off. Used by the scheduled task that generates the day's
# math problem and the day's debate, so the dashboard never has to pay for
# either.
#
#   ./batch.sh --context                  what the math generator needs
#   ./batch.sh --validate FILE            the server's checks, at 3am not 8am
#
#   ./batch.sh --debate-context           tonight's debate jobs, and which are due
#   ./batch.sh --validate-debate FILE     the same, for a motion or reading list
#
#   ./batch.sh --trick-context            tonight's trick set: topic, bands, callback
#   ./batch.sh --validate-trick FILE      the same, for a generated trick set
#
# The trick is the one job here with no free fallback -- the math problem has a
# curated pool behind it and the debate costs nothing to begin with -- so a
# night this does not run is the one night that costs money in the morning.
#
# `--debate-context` reports two nullable jobs: `motion` on every debate day,
# and `reading_list` only on the night before the reading day. The task writes
# whichever it was given and validates each with the one --validate-debate,
# which tells the two shapes apart by their fields.
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
