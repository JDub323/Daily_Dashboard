@echo off
rem The dashboard at sign-in, for the phone's benefit.
rem
rem `run.cmd` is the one you type; this is the one Task Scheduler runs. The
rem difference is only the tab: the phone log queues writes locally and drains
rem them when you press send, which needs the server to be up whenever you
rem happen to press it -- and the failure mode of it not being up is silent
rem until you are standing somewhere with fourteen entries waiting. Starting it
rem at logon removes the one step that has to be remembered.
rem
rem It delegates to run.sh like everything else, so there is still one place
rem where "how to start the dashboard" is written down.
rem
rem DASHBOARD_NO_BROWSER because a tab per sign-in is a tab you close per
rem sign-in. It is set *inside* the WSL command rather than with `set`: a
rem Windows environment variable does not cross into WSL unless it is named in
rem WSLENV, and one variable is not worth a second mechanism to carry it.
rem Setting it here rather than in config.toml leaves `run.cmd` alone.
rem
rem Register or remove it with install-boot-task.ps1 beside this file.
wsl --cd "%~dp0" -e bash -lc "DASHBOARD_NO_BROWSER=1 ./run.sh"
