@echo off
rem One-command launch from Windows: runs the server inside WSL, opens the tab
rem itself. Delegates to run.sh so the launch logic exists in one place, and
rem `--cd` pins the working directory -- this used to depend on whatever WSL's
rem default cwd happened to be.
wsl --cd "%~dp0" -e ./run.sh
