# Daily Dashboard

One-command personal dashboard, opens http://localhost:8502.

- **From WSL / Linux / macOS**: `./run.sh` (or `python3 dashboard.py`).
- **From Windows** (double-click in Explorer, or `cmd`): `run.cmd` — hops into WSL for you.

Do **not** run `run.cmd` from inside WSL — it's a Windows batch file and `wsl` doesn't exist there; use `run.sh` instead.

- **Config** (topic of the week, tickers, feeds, port, per-topic theorem library): the marked block at the top of `dashboard.py`.
- **Secrets**: `.env` with `ANTHROPIC_API_KEY=...` (gitignored).
- **Data**: `state.json` (ratings, streaks, logs — gitignored) and `problems.json` (hand-editable math problem log; append your own).
- **Calendar**: shells to gcalcli inside WSL (`wsl bash -lc "gcalcli ..."`), so gcalcli must be authed in WSL.
- **Files**: `dashboard.py` (server), `index.html` (shell), `style.css`, `app.js` (all panels), `chess.js` (vendored chess.js 0.13.4, ES module).

## Layout

Task chips (top) → gold when done; three columns: feeds (calendar/markets/news/reading) | hero (interactive chess board, math problem, piano of the day) | metrics (elo history, trophies & streaks, health).

## Key mechanics

- **Saves** go through `POST /api/state/merge` — panels write only the top-level keys they touched, so concurrent saves can't clobber each other. Never full-replace state from a panel.
- **Elo**: dynamic K = max(24, 96/(1+n/8)) — fast movement with little history, stabilizing over time. Every history entry stores the post-game rating for the charts.
- **Math problems**: LLM-generated with the web-search server tool (max 4 searches) so problems are verified against AoPS before being shown (~$0.02–0.04/day). Theorem/textbook links come from the hand-curated `TOPIC_LIBRARY`, not the LLM.
- **Chess**: lichess puzzle rendered on an in-page board (click-to-move, hints, give-up shows the solution). Clean solve = 1.0, with mistakes/hints = 0.4, fail = 0.
- **Feeds** (markets/calendar/news/paper) are cached server-side and serve stale data on refetch failure instead of crashing.
- **Adding a panel**: append one `PANELS.push({id, title, col, async render(el)})` block in `app.js`; add a server endpoint in `dashboard.py` only if it needs the network or secrets.
