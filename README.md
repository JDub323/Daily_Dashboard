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

392 Python tests and 33 JavaScript ones; none touch the network, the API, `dashboard.db`, or `state.json`. The JS runner is node's built-in `node --test`, so there is nothing to `npm install`.

The suite is weighted toward the code that was hardest to check by hand: the elo curve, the hint penalty, the theorem draw, streaks, the LaTeX-in-JSON repair, the cache's exception policy and staleness flag, the day-rollover guards, the double-submit guards, and the `state.json` importer's PROVENANCE/PRESERVE rules.

## Layout

```
dashboard/            the server
  config.py     config.toml -> validated settings
  logs.py       logging setup
  scoring.py    elo, the theorem draw, streaks — pure, no DB/network/clock
  spelling.py   the bee: the pool, the draw, the marking — pure
  tricks.py     the trick of the day: the marking, the callback — pure
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
    panel.js    mount / render / retry / refresh harness
    daily.js    the x-of-the-day cells that fetch themselves at boot
    clipboard.js  copying a long prompt, and the two fallbacks it needs
    freshness.js  "as of 09:14", and when a live panel refetches
    charts.js   sparkline + line chart
    parsers.js  Wordle & Pokedoku share text — pure functions
    chips.js    the daily checklist
    tabs.js     the eight sections, and which pane a panel mounts into
    nav.js      the focus ring — what h/j/k/l actually move
    keys.js     the bindings, as data; the cheatsheet is generated from them
    panels/     one file per panel
    modals/     theorems, math, tricks (the archives) + trickset (the sheet)
  data/         piano.json — scale/chord content, out of the logic file
  vendor/katex  see PROVENANCE.txt

tests/          pytest, plus tests/js for node --test
```

There is no bundler. The front end has no npm dependency at runtime — KaTeX is
vendored and everything else is hand-written — so native `<script type="module">`
does the whole job, and a build step would only add a thing that can break
between you and the page.

UI: eight tabs, each a subject with one **hero** panel saying what the tab is
for and supporting panels under it — home (checklist, completion heatmap, LLM
spend, word of the day, calendar, day log, elo by area) · math · politics ·
newsletters · games · markets · hobbies · health. The content column is capped
at 1040px and centred, so the margins stay empty on a wide screen.

Keyboard-first, and the mouse still works. `Tab`/`Shift-Tab` cycle sections,
`1`–`8` jump to one, `h`/`j`/`k`/`l` move a focus ring between panels — and,
after `Enter` descends into a panel, between the links and controls inside it.
`Enter` opens the focused thing, `Esc` backs out a level, `gg`/`G` go to the
first/last panel, `n` opens the next unfinished task, `?` lists the lot. The
bindings are data in `keys.js`; the cheatsheet is generated from the same list
that dispatches them.

## Key mechanics

- **One endpoint per write.** `POST /api/state` and `/api/state/merge` are gone — a browser tab can no longer replace the world with what it happened to be holding. Every write is a per-column `UPDATE` behind `Repository`.
- **The browser owns "today".** Every dated request carries the browser's local date. The server never calls `date.today()` to decide what day it is.
- **Elo**: dynamic K = max(24, 96/(1+n/8)) — fast movement with little history, stabilizing over time. The current rating is *derived* from the append-only `rating_event` log; the rating and the update are computed inside one transaction, so two tabs can't both read the same rating and clobber each other.
- **Math problems**: two free sources and no third. The overnight batch (`batch.sh`, generated in the Claude app against the subscription) is tried first, then the hand-curated pool in `problems.json`; if neither has an unseen problem the panel says so and names the fix. There used to be a paid fallback — an agentic run with web search, verified against AoPS, ~$0.15–0.80 a morning, and by far the most expensive call in the app — and it is gone: `content.py` has no prompt that produces a problem, so no click can bill for one. Theorem/textbook links come from `[topic_library]` in `config.toml`, not the LLM.
- **Late submission**: a day you drew a problem and never marked is auto-scored 0. For **14 days** you can still submit it from the math archive, and being late costs exactly what two hints cost — the whole ladder scaled by 0.4, so 0–3 hints already taken give `0.40 / 0.28 / 0.16 / 0.04`. Each hint costs 30% of what the attempt is still worth rather than a flat 0.30, which is what keeps a three-hint late solve from scoring −0.5. The late result *replaces* the zero: the lapse row stays in the append-only log and an `unlapsed` row hands back exactly what it took. A late row counts toward the K-factor (you did the problem) but is kept out of `success_rate`, whose threshold is 0.5 and which would otherwise read every late solve as a failure.
- **Math archive**: a modal off the math panel, holding the last 60 problems with statements, answers and what each did to the rating. A closed day shows its answer outright — that is what an archive is for — while a day still open to late submission keeps it behind a reveal, because a self-reported "solved" means nothing if the answer was already on screen.
- **Chess**: a lichess link plus three self-report buttons. Clean = 1.0, with mistakes = 0.4, fail = 0. The puzzle rating is still the elo opponent.
- **The day's cells gather themselves.** The word, the bee, the theorem card and the chess puzzle are fetched at boot and again on rollover — `daily.js` — rather than each waiting behind a button whose only question was "yes, today's one, please". `needed` in that table is the guard that makes a second gather safe: `/api/chess/new` upserts the day's attempt, so an unguarded refetch would swap a half-finished puzzle for another one. The buttons survive as the fallback for a gather that failed, built from the same table so the two paths cannot diverge. A day you never open the page still draws nothing — the deck does not age and no word is paid for.
- **Feeds** (markets/calendar/news/paper) are cached server-side and serve stale data when the *source* is unreachable. A bug in our own parsing propagates instead — a stale panel and a broken parser used to look identical. The fallback now *says so*: every feed response carries `fetched_at`, `ttl_seconds` and `stale`, so the panel prints `as of 09:14` and turns it red when the source stopped answering. A stale panel and a live one used to look identical too.
- **Live panels** (markets/calendar/news) re-render themselves, scheduling the next fetch from the server's own `cache_seconds` — so the interval lives in `config.toml` alone, and a panel mounted late in a TTL window refreshes sooner rather than a full window later. Polling stops while the tab is hidden and resumes on the way back.
- **The day rolls over on its own.** The tab is one you leave open, so a 60s check plus `visibilitychange` reloads everything when the browser's local date moves on. The completion write is addressed to the day its counts were read off, never to the clock — otherwise a tab open past midnight posted yesterday's finished count against today, and `done = total` is what makes a perfect day.
- **No `innerHTML`.** Every panel builds real nodes through `dom.js`, so text is a text node and escaping is structural rather than a hand-rolled `esc()` each render has to remember to call.
- **Offline-clean.** The page makes no request to anything but `127.0.0.1`.
- **Adding a panel**: a request/response model in `models/api.py`, a repository method, a route in `server.py`, and a module in `web/app/panels/` exporting `{ id, title, tab, slot }` — added to the `PANELS` list in `main.js`, whose order *is* the layout within each slot. `slot: "hero"` claims the top of the tab; anything else lands in the grid below it. If it is another x-of-the-day, add one entry to `DAILIES` in `daily.js` and call `opener(key)` where the button would have gone — that buys the boot fetch, the busy line and the retry together.
- **Adding a tab**: one entry in `TABS` in `tabs.js` and one SVG in `web/assets/bg/`. The digit shortcut is the array index, so there is no second table of bindings to keep in sync.
