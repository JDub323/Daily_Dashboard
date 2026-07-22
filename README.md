# Daily Dashboard

One-command personal dashboard: `python dashboard.py` → opens http://localhost:8501.

- **Config** (topic of the week, tickers, feeds, port): the marked block at the top of `dashboard.py`.
- **Secrets**: `.env` with `ANTHROPIC_API_KEY=...` (gitignored).
- **Data**: `state.json` (ratings, streaks, logs — gitignored) and `problems.json` (hand-editable math problem log; append your own).
- **Calendar**: shells to gcalcli inside WSL (`wsl bash -lc "gcalcli ..."`), so gcalcli must be authed in WSL.
- **Adding a panel**: append one `PANELS.push({id, title, async render(el)})` block in `index.html`; add a server endpoint in `dashboard.py` only if it needs the network or secrets.
