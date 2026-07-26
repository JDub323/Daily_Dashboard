"""`python -m dashboard` — the single entry point.

`dashboard.py` used to be both the package's future name and the whole
program. It is gone; this is what run.sh, run.cmd and .claude/launch.json all
invoke now.
"""

from .server import main

if __name__ == "__main__":
    main()
