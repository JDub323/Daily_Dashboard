# Daily Dashboard — Rewrite Plan (handoff)

Working doc for a rebuild. Delete this file when the rewrite lands.

---

## 1. What this project is

A single-user personal dashboard served at `http://localhost:8502`. Panels: daily
math problem (LLM-generated, web-search verified), chess puzzle, word of the day,
Wordle/Pokedoku result logging, theorem spaced-repetition, arXiv paper triage,
calendar, markets, news headlines, piano scale/chord of the day, elo history,
streaks, health logging.

Current implementation (the thing being replaced):

| File | Lines | Role |
|---|---|---|
| `dashboard.py` | 640 | stdlib `ThreadingHTTPServer`: config, dotenv, state persistence, HTTP cache, 2 LLM clients, 8 feed scrapers, prompts, router |
| `app.js` | 907 | vanilla JS: all panels, chess board engine, charts, modals, elo math |
| `chess.js` | 1,991 | vendored chess.js 0.13.4 (ES module) |
| `style.css` | 147 | dark/monospace theme with CSS custom properties |
| `index.html` | 35 | shell |
| `state.json` | — | **live user data** (gitignored) |
| `problems.json` | — | append-only LLM problem log (tracked in git) |
| `metrics.jsonl` | — | per-generation cost/token log (gitignored) |

Runs inside WSL (`wsl -e bash -lc "python3 dashboard.py"`, see `.claude/launch.json`).
`python` is not on the Windows PATH — use `wsl -e bash -lc "python3 …"` for any scripting.
Secrets live in `.env` (`ANTHROPIC_API_KEY`), gitignored, never committed — verified clean.

---

## 2. STOP — read before touching anything

**The frontend is not in version control.** `git ls-files` returns 7 files.
`app.js`, `chess.js`, `style.css`, and `run.sh` are **untracked**; a fresh clone is a
non-functional app. There is also an uncommitted ~500-line diff on `master`
(`index.html` −447, `dashboard.py` +462).

**First action, before any other work: commit the current tree as a baseline.**
Not because it's good — because it's the only reference for what to salvage, and
deleting untracked files is unrecoverable.

`state.json` holds real, irreplaceable data (elo history, streaks, day log, spaced-
repetition scheduling). Back it up before the SQLite migration and verify the import
round-trips before deleting it.

---

## 3. Decisions already made — do not relitigate

| Decision | Choice |
|---|---|
| Dependencies | **Small, boring dep set.** FastAPI + Pydantic + pytest server-side; a bundler client-side. The stdlib-purity constraint is dropped. |
| Storage | **SQLite now**, not `state.json`. |
| Deployment | **Single-device localhost, for now.** Bind `127.0.0.1`. |
| Chess panel | **Revert to a lichess link + self-reported score.** Delete the inline board. |
| Panel scope | **Keep every existing panel.** Nothing is being cut. |

### Explicitly OUT of scope

Do not build these. They were considered and deliberately deferred:

- **Auth / bearer tokens / CSRF.** Localhost-only, single user.
- **Google Calendar API migration.** `wsl_gcalcli` keeps shelling into WSL. It is
  machine-local and that is accepted.
- **Remote/multi-device deployment**, HTTPS, non-loopback binding.

SQLite is still adopted *now* even though the app stays single-device — multiple
browser tabs are already a live concurrency source, and that is precisely what caused
the historical lost-elo bug. Building the storage layer correctly now avoids migrating
a year of history later.

---

## 4. Why the chess board is being deleted

Dropping the inline GUI board is the largest single code deletion available:

- `chess.js` — 1,991 lines / 56KB vendored blob
- `mountPuzzle()` — `app.js:252-383`, ~130 lines
- The `<script type="module">` shim at `index.html:8` — which removes the classic-script
  vs. ES-module split from the HTML entirely
- The `/chess.js` entry in `STATIC` (`dashboard.py:528`)
- Board CSS — `style.css:101-119`

**The replacement already exists in the codebase.** The PGN-parse-failure fallback at
`app.js:255-263` renders exactly the target UI: a lichess link plus three buttons
(`solved clean` = 1.0, `solved w/ mistakes` = 0.4, `failed` = 0). Promote that path to
be the whole feature.

Keep calling the lichess API — `puzzle.rating` is still needed as the elo opponent.
Only the rendering changes. Accepted tradeoff: `mistakes` and `hints` were derived from
board interaction and leave the schema; score becomes self-reported.

---

## 5. Work plan, in order

### Step 1 — Baseline commit
Commit the working tree, including all untracked frontend files, on a branch.
Message it as a pre-rewrite snapshot. This is the reference to salvage from.

### Step 2 — Pydantic models
Define models for the persisted state **and** every API request/response body.
This is the highest-leverage step and must come before Step 3, because the models
define the tables.

It simultaneously fixes three problems: the state schema is currently implicit in
`DEFAULT_STATE` + an ad-hoc `_migrate` (`dashboard.py:102`); POST handlers index raw
dicts (`body["word"]`, `body["sentences"]`, `body["text"]`) and `KeyError` into a 500;
and there is no typed contract to test against.

### Step 3 — SQLite behind a repository module
Replace `state.json` with SQLite. Use `PRAGMA user_version` plus an ordered, idempotent
migration list.

This one change subsumes four separate defects in the old design:

1. **Silent total data loss.** `_load_unlocked` (`dashboard.py:109-115`) catches all
   exceptions and returns `DEFAULT_STATE`; the next save then writes those defaults over
   your real file. One truncated write destroys everything, with no warning and no backup.
2. **Unordered migrations.** `_migrate` is a pile of `setdefault` calls plus
   `_backfill_ratings`, which *fabricates* rating history by linear interpolation and
   re-runs on every load with no way to know it already ran.
3. **`merge_state` doesn't do what the README claims.** It merges top-level keys
   (`dashboard.py:130`), but the client's `save("math")` ships the entire `STATE.math`
   subtree — so two tabs still clobber each other within a key. Per-column `UPDATE`
   makes the whole concept unnecessary; `merge_state` and its README paragraph disappear.
4. **Unbounded growth.** `daylog`, `completion`, `word.byDate`, `games.wordle`,
   `games.pokedoku`, `papers.archive`, `math.seen` grow forever and the entire file is
   re-serialized on every save. `updateChips()` fires a save on page load
   (`app.js:179-182`). Becomes `DELETE … WHERE date < …`.

**Deliverable: a one-time `state.json` → SQLite importer**, with verification that the
data round-trips before the JSON file is retired.

### Step 4 — Split the server
`dashboard.py` currently does six jobs in one file. Split into roughly
`config.py` / `state.py` / `llm.py` / `feeds.py` / `server.py`, on FastAPI.

Carry these fixes in during the split:

- **Delete `llm()`; keep `llm_stream()`.** `dashboard.py:159` and `:196` duplicate the
  key check, payload build, `pause_turn` continuation loop, and no-text error — with
  divergent continuation logic (only the streaming one calls `_clean_blocks`). One
  implementation, streaming optional.
- **Delete `POST /api/state`** (`dashboard.py:607`). Commented "legacy, avoid," still
  live, full-replaces the entire state.
- **Add real logging.** `log_message` is silenced (`dashboard.py:554`) and every error is
  flattened to `str(e)[:300]` and painted into a panel — no traceback survives anywhere.
- **Narrow `cached()`'s except clause** (`dashboard.py:140`). It catches *everything* to
  serve stale, so a `KeyError` from your own parser bug is indistinguishable from Yahoo
  being down, and the panel quietly serves week-old data forever.
- **`wsl_gcalcli` (`dashboard.py:361`): pass an argv list to `subprocess.run`**, not a
  hand-quoted string through `bash -lc`. (The existing POSIX quote-escaping is actually
  correct, but it's fragile, and `-l` reloads your full login profile on every call.)
- **Move config out of source.** `TOPIC_OF_WEEK`, `TICKERS`, `NEWS_FEEDS`,
  `MATH_ANCHORS`, `PRICING`, `TOPIC_LIBRARY` all require editing Python to change —
  and `TOPIC_OF_WEEK` is a *manual weekly edit*. Move to `config.toml` (`tomllib`).
- **Resolve `problems.json`'s dual identity.** It is tracked in git and described as
  hand-editable, but `dashboard.py:495-497` appends LLM output to it on every generation,
  so it churns on every commit. Its entries also carry stale `textbook`/`theorems` keys
  the current prompt no longer produces. Pick one role; split the other into a gitignored log.

### Step 5 — Frontend rewrite
Rebuild `app.js` (907 lines, everything global) as ES modules. Without the chess board
this is ~400 lines of real work, not 900.

- **Replace `innerHTML` + manual `esc()` with real DOM construction.** Every panel
  rebuilds its subtree from a template string and re-attaches `onclick` handlers on each
  render (`app.js:167`, `:300`, `:627`). The hand-rolled `esc()` (`app.js:12`) covers only
  `&<>"` and is applied inconsistently — `p.statement`/`p.answer` go in raw for KaTeX, and
  a URL pasted into the paper queue is attribute-escaped but never scheme-validated, so
  `javascript:` in that box executes. Make escaping structural.
- **Move data out of code.** `SCALES` (`app.js:459`) and `CHORDS` (`app.js:474`) are ~35
  rows of static content sitting in a logic file.
- **Use the existing CSS system.** `style.css` has clean design tokens; `app.js` then
  overrides them with inline `style="margin:6px 0"`, `style="font-size:11.5px"`,
  `style="flex:1"` in a dozen places. Each is a missing class.
- **Fix the two-clocks bug.** The server stamps `prob["date"]` with `dt.date.today()` in
  WSL's timezone (`dashboard.py:492`); the client compares it against
  `localISO(new Date())` in the browser's (`app.js:14`). Any skew silently breaks every
  "done today" check. **Make the browser the sole authority on "today"** and pass the
  date to the server.

### Step 6 — Tests
There are currently zero. The most defect-prone code is pure and trivially testable —
today, verifying the elo math requires actually solving a chess puzzle.

Extract and cover, with no DOM or network:
`eloUpdate` / `kFactor` (`app.js:87-91`), `streak()` (`app.js:124`), the Wordle and
Pokedoku share-text parsers (`app.js:789-813`), the SM-2 scheduler (`app.js:834-841`),
and `llm_json`'s LaTeX backslash-repair regex (`dashboard.py:294-302`).

### Step 7 — Tooling and the bug list
Add the missing manifest layer: `pyproject.toml`, a Python version pin, `package.json`,
formatter/linter config, a `LICENSE` (chess.js was vendored under BSD with no provenance
record — moot once it's deleted, but the repo still needs one), and a `.gitattributes`
(git warns LF→CRLF on every file because the tree is edited from both Windows and WSL).

Collapse the three launch paths (`run.sh`, `run.cmd`, `.claude/launch.json`) into one.
The README currently spends a paragraph warning which not to run; `run.cmd` doesn't `cd`,
so it depends on WSL's default cwd; and the port is defined twice — `dashboard.py` reads
`$PORT` while `.claude/launch.json` hardcodes `8502`. They can disagree.

---

## 6. Concrete bugs to fix along the way

- **The hint counter charges you for the textbook link.** `app.js:419` makes `hints[0]`
  the topic/textbook links, but scoring is `1 - 0.15 * hints_revealed` (`app.js:453`) —
  so opening reference material costs the same elo as a real hint.
- **`_fetch_markets` can `IndexError`.** `dashboard.py:345` reads `closes[-2]` when
  `regularMarketPreviousClose` is absent; a ticker with a single close crashes it.
- **KaTeX loads from CDN with no SRI and no fallback** (`index.html:5-7`) — the app is
  offline-broken, and after chess.js is deleted this is the only remaining external
  runtime dependency. Vendor it.
- **`_fetch_markets` error shadowing.** The error dict from the `query1` attempt is
  overwritten by the `query2` attempt, so the reported failure may not be the real one.

---

## 7. Preferences to respect

- **Dark mode + monospace are non-negotiable.** Current theme: near-black `#0b0e14`,
  gold `#e8b458`, cyan `#5ccfe6`. Keep the design tokens in `style.css`.
- **Visual hierarchy over flat uniform grids** — hero widgets, grouped columns, gold
  completion states on task chips.
- **Token-cost-conscious.** State the $/day tradeoff before adding any LLM call. Prefer
  deterministic/hand-curated data over LLM regeneration. Web-search verification of the
  daily math problem is approved at ~$0.02–0.04/day.
- **Elo philosophy: high variance early, stabilizing over time** — `K = max(24, 96/(1+n/8))`.
  Preserve this exactly. Elo history charts must stay.
- **Directionally-correct instructions.** Judgement calls are expected and large sweeping
  changes are welcome; do not follow this document so literally that you do something dumb.
