#!/usr/bin/env bash
# The overnight hand-off. Used by the scheduled task that generates the day's
# math problem and the day's trick, so the dashboard never has to pay for
# either.
#
#   ./batch.sh --week                     the whole coming week, Sunday first
#   ./batch.sh --context                  one day of it
#   ./batch.sh --validate FILE            the server's checks, at 3am not 8am
#   ./batch.sh --validate-reading FILE    the same, for Sunday's reading plan
#
#   ./batch.sh --trick-context            tonight's trick set: topic, bands, callback
#   ./batch.sh --validate-trick FILE      the same, for a generated trick set
#
# The math job is weekly now and the trick's is still nightly, and the split is
# not arbitrary. What the week asks for is week-shaped -- Sunday's reading is
# what the proof days are meant to make use of, the two-day problem spans two
# nights whatever the schedule does, and the four proof days read one section of
# one book. A trick is a day's object and has no such coupling.
#
# The trick is the one job here with nothing at all behind it -- the math
# problem has a curated pool. The panel used to offer the same prompt to carry
# into the Claude app by hand; it does not any more, so a night this does not
# run is a morning with no trick, and the panel says so and names these two
# commands.
#
# The debate's two jobs used to live here as well. The debate is gone, and the
# investigation that replaced it needs no overnight anything: its topics come
# from the class, not from a generator.
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
