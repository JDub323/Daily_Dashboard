@echo off
rem One-command launch: runs the server inside WSL, opens the tab itself.
wsl -e bash -lc "python3 dashboard.py"
