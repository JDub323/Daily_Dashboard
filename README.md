# Daily Dashboard

One-command personal dashboard, opens http://localhost:8502.

- **From WSL / Linux / macOS**: `./run.sh` — creates the virtualenv on first run.
- **From Windows** (double-click in Explorer, or `cmd`): `run.cmd` — hops into WSL and calls `run.sh`.

Do **not** run `run.cmd` from inside WSL — it's a Windows batch file and `wsl` doesn't exist there; use `run.sh` instead.

**The virtualenv does not live in the project directory** when the project is on a Windows drive. Every import from a venv under `/mnt/c` is a syscall round trip through drvfs: `import fastapi` measured **13 s** from `/mnt/c` against **0.9 s** from the Linux filesystem, which made startup slow enough that the browser tab opened before the server was listening. `run.sh` puts it in `~/.local/share/daily-dashboard/venv` instead; `DASHBOARD_VENV` overrides, and on native Linux/macOS it stays at `./.venv`.

If the page says **"didn't send any data"**, something else already owns the port — most likely an older dashboard process. The server now refuses to start in that case and tells you; `ss -ltnp | grep 8502` names the offender.

- **Config**: `config.toml` — topic rotation, tickers, feeds, port, pricing, tier anchors, per-topic theorem library. No constant lives in source any more; `DASHBOARD_CONFIG` overrides the path.
- **Topic rotation**: `[math].rotation` is a 25-week cycle weighted by how much of the Putnam each area is — analysis 7 slots, algebra 6, combinatorics 5, number theory 4, geometry 2, probability 1 — with each area split into topics narrow enough to study in a week. It advances **every Sunday**, in the browser's timezone, indexed from `rotation_start` (which must itself be a Sunday, checked at startup). Empty the list to pin a single `topic_of_week` instead. A topic with no `[topic_library]` entry costs one LLM call the first time it comes up, then is cached in `topic_info` forever.
- **Secrets**: `.env` with `ANTHROPIC_API_KEY=...` (gitignored).
- **Data**: `dashboard.db` (SQLite, gitignored). `problems.json` is a hand-curated *pool* — the server reads it and never writes to it; drop a problem in and it gets served before any LLM call is paid for.
- **Calendar**: runs gcalcli directly as an argv list (prefixed with `wsl -e` when the server is on Windows), so gcalcli must be authed in WSL.
- **Log**: `dashboard.log`, rotating. Panels show one line; the traceback is here.

`run.cmd` and `.claude/launch.json` both call `run.sh`, so there is one launch path. The port lives only in `config.toml` — `.claude/launch.json` has to repeat it so the preview tool knows where to connect, and a test fails if the two drift apart.

## Tests

```bash
PY=~/.local/share/daily-dashboard/venv/bin; $PY/python -m pytest && npm test && $PY/ruff check . && $PY/ruff format --check .
```

248 Python tests and 20 JavaScript ones; none touch the network, the API, `dashboard.db`, or `state.json`. The JS runner is node's built-in `node --test`, so there is nothing to `npm install`.

The suite is weighted toward the code that was hardest to check by hand: the elo curve, the hint penalty, the theorem draw, streaks, the LaTeX-in-JSON repair, the cache's exception policy, the double-submit guards, and the `state.json` importer's PROVENANCE/PRESERVE rules.

## Layout

```
dashboard/            the server
  config.py     config.toml -> validated settings
  logs.py       logging setup
  scoring.py    elo, the theorem draw, streaks — pure, no DB/network/clock
  llm.py        one streaming Anthropic client + cost accounting
  feeds.py      RSS, Yahoo, arXiv, lichess, gcalcli + the stale-tolerant cache
  content.py    prompts; the only module that both calls the LLM and persists
  state.py      rows -> the boot payload (read-only)
  server.py     FastAPI routes and nothing else
  models/       domain (the tables) + api (what crosses HTTP)
  db/           schema, migrations, repository, state.json importer

web/                  the front end — native ES modules, no build step
  index.html
  style.css     the design tokens; every panel style is a class here
  app/
    dom.js      el()/svg()/link() — the only way anything reaches the DOM
    api.js      fetch + ndjson, and the definition of "today"
    store.js    a cache of the server's read model, never pushed back whole
    panel.js    mount / render / retry harness
    charts.js   sparkline + line chart
    parsers.js  Wordle & Pokedoku share text — pure functions
    chips.js    the daily checklist
    panels/     one file per panel
    modals/     word, games, theorems, daylog
  data/         piano.json — scale/chord content, out of the logic file
  vendor/katex  see PROVENANCE.txt

tests/          pytest, plus tests/js for node --test
```

There is no bundler. The front end has no npm dependency at runtime — KaTeX is
vendored and everything else is hand-written — so native `<script type="module">`
does the whole job, and a build step would only add a thing that can break
between you and the page.

UI: task chips (top) → gold when done; three columns: feeds (calendar/markets/news/reading) | hero (chess puzzle, math problem, piano of the day) | metrics (elo history, trophies & streaks, health).

## Key mechanics

- **One endpoint per write.** `POST /api/state` and `/api/state/merge` are gone — a browser tab can no longer replace the world with what it happened to be holding. Every write is a per-column `UPDATE` behind `Repository`.
- **The browser owns "today".** Every dated request carries the browser's local date. The server never calls `date.today()` to decide what day it is.
- **Elo**: dynamic K = max(24, 96/(1+n/8)) — fast movement with little history, stabilizing over time. The current rating is *derived* from the append-only `rating_event` log; the rating and the update are computed inside one transaction, so two tabs can't both read the same rating and clobber each other.
- **Math problems**: served from `problems.json` when an unseen one is there, otherwise LLM-generated with the web-search server tool (max 4 searches) and verified against AoPS before being shown (~$0.15–0.80 per generation — the search results dominate the bill, so the pool is the real cost control). Theorem/textbook links come from `[topic_library]` in `config.toml`, not the LLM.
- **Chess**: a lichess link plus three self-report buttons. Clean = 1.0, with mistakes = 0.4, fail = 0. The puzzle rating is still the elo opponent.
- **Feeds** (markets/calendar/news/paper) are cached server-side and serve stale data when the *source* is unreachable. A bug in our own parsing propagates instead — a stale panel and a broken parser used to look identical.
- **No `innerHTML`.** Every panel builds real nodes through `dom.js`, so text is a text node and escaping is structural rather than a hand-rolled `esc()` each render has to remember to call.
- **Offline-clean.** The page makes no request to anything but `127.0.0.1`.
- **Adding a panel**: a request/response model in `models/api.py`, a repository method, a route in `server.py`, and a module in `web/app/panels/` registered in `main.js`.
