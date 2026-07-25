#!/usr/bin/env bash
# Launch from inside WSL (or any Linux/macOS shell):  ./run.sh
# You're already in the Linux environment here, so there's no `wsl` hop —
# that's what run.cmd (Windows double-click) is for.
cd "$(dirname "$0")"
exec python3 dashboard.py
