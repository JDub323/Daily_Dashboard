# Sharpness

One-command personal dashboard, opens http://localhost:8502.

- **From WSL / Linux / macOS**: `./run.sh` — creates the virtualenv on first run.
- **From Windows** (double-click in Explorer, or `cmd`): `run.cmd` — hops into WSL and calls `run.sh`.

Do **not** run `run.cmd` from inside WSL — it's a Windows batch file and `wsl` doesn't exist there; use `run.sh` instead.

**The virtualenv does not live in the project directory** when the project is on a Windows drive. Every import from a venv under `/mnt/c` is a syscall round trip through drvfs: `import fastapi` measured **13 s** from `/mnt/c` against **0.9 s** from the Linux filesystem, which made startup slow enough that the browser tab opened before the server was listening. `run.sh` puts it in `~/.local/share/daily-dashboard/venv` instead; `DASHBOARD_VENV` overrides, and on native Linux/macOS it stays at `./.venv`.

If the page says **"didn't send any data"**, something else already owns the port — most likely an older dashboard process. The server now refuses to start in that case and tells you; `ss -ltnp | grep 8502` names the offender.

- **Config**: `config.toml` — topic rotation, tickers, feeds, port, pricing, tier anchors, per-topic theorem library. No constant lives in source any more; `DASHBOARD_CONFIG` overrides the path.
- **Topic rotation**: `[math].rotation` is a 25-week cycle weighted by how much of the Putnam each area is — analysis 7 slots, algebra 6, combinatorics 5, number theory 4, geometry 2, probability 1 — with each area split into topics narrow enough to study in a week. It advances **every Sunday**, in the browser's timezone, indexed from `rotation_start` (which must itself be a Sunday, checked at startup). Empty the list to pin a single `topic_of_week` instead. A topic with no `[topic_library]` entry costs one LLM call the first time it comes up, then is cached in `topic_info` forever.
- **Training plan**: `training.toml` — what a day *asks* of you, as against `training_session`, which is what happened. Two halves: `[library]` says what a token means and which `SessionKind` a log of it lands in, and `[[block]]` is the week grid, transcribed from `8_10_26-12_31_26 TOURNAMENT PREP.xlsx`, which stays the source of truth. **Re-translating that sheet overwrites every `[[block]]`.** The schedule is written in the sheet's own shorthand (`LFC+N * A3` — `+` joins one outing, `*` splits two, `'` marks optional, `X'Y` regresses to Y, `A3` is 30 minutes) because filling in a week should cost one line. Absent is a supported state: no file means no camp, and every panel renders that as silence. Malformed is *not* — it raises, at boot and on re-read, because silence is already this feature's answer to a rest day and a typo must not be indistinguishable from one.
- **Standing commitments**: `[[recurring]]` in `training.toml` — club practice. Deliberately outside `[[block]]`: the blocks are a five-week programme that gets replaced, while the club schedule persists across whatever replaces it, and written into a block it would vanish through the eleven weeks nobody has written yet — precisely the stretch where it is the only training happening. `optional_days` is a day that may not be running yet: its parts are still offered, and they join the points denominator only on a day they are actually logged, so a Saturday practice that has not started cannot score you against points you could not earn.
- **Logging what was prescribed**: `web/app/panels/plan.js` draws one card per outing and `POST /api/session/card` writes the lot in one request. A practice splits into a conditioning row and a technique row so the two volumes stay separable; one card and one button is what keeps that from also costing two round trips and a half-recorded day when the second fails. **Only the kind is ever pre-filled** — load is rpe x minutes, and a default that survived into a submit would be a measurement nobody took. `training_session.plan_key` (`<date>:<token>`, UNIQUE where present) is the only place the plan and the log touch, so a double-clicked card is a 409 rather than a doubled day. The `session` panel is untouched and stays the way in for anything the plan never asked for.
- **Secrets**: `.env` with `ANTHROPIC_API_KEY=...` (gitignored).
- **Data**: `dashboard.db` (SQLite, gitignored). `problems.json` is a hand-curated *pool* — the server reads it and never writes to it; drop a problem in and it gets served before any LLM call is paid for.
- **Calendar**: runs gcalcli directly as an argv list (prefixed with `wsl -e` when the server is on Windows), so gcalcli must be authed in WSL. stdin is closed on every call — gcalcli drops to an interactive menu whenever a subcommand cannot resolve its target calendar, and with stdin inherited that menu *hangs* until the timeout instead of failing. Every write names its `--calendar`, for the same reason.
- **Quick add**: the box on the home hero takes one line per item ("fpm Thornhill Chapter 3 due thursday"), and a whole pasted list at once — Ctrl+Enter commits, the lines that could not be written stay behind with their errors while the rest disappear. `calparse.py` resolves the title, day and span locally; `todo.py` expands aliases and reads recurrence before it; `calclass.py` picks the calendar with a scikit-learn model fitted at boot on the account's own history — an event's calendar is its label, so the training set costs nothing. `POST /api/calendar/preview` resolves every line without writing, which is what the preview under the box shows.
- **A line with no day in it is refused.** `calparse` still resolves one onto today — that is what makes it usable for an event — but it reports `date_explicit`, and the box rejects the line rather than inventing a due date. This is the one rule the todo layer adds and it applies to everything the box writes.
- **Aliases** (`[todo.subjects]` in `config.toml`) are whole-word substitutions, like variables: `os` → `Operating Systems` fires in "new os homework" and never in "buy banjos". Keys must be one word and are refused at load if they collide with a word the date parser reads (`sat`, `may`). Whether one fired is drawn on every previewed line and on the box's own border, because a subject you *forgot* to abbreviate looks exactly like ordinary text otherwise. The circled `?` lists them.
- **Todo or event is decided by the target calendar**, not by a mode or a sigil — `[todo].todo_calendars` names the ones that mean work, and those get a row in the due list with a checkbox. The picker beside the box defaults to `auto`, which is *not* the classifier: it is the subject's own calendar, then `calclass`'s tests rule, then Homework, because nineteen dated lines in twenty are homework. `learned` is the model, one Tab away, and choosing any calendar explicitly is stored in `calendar_correction` and refitted at a heavier weight. Only writable calendars are offered — a `reader` calendar is drawn in the grid and can never be written to. Expect `learned` to be right about 66% of the time and to hold the answer in its top two about 92% (5-fold CV, 338 titles, 56.5% majority baseline); the ceiling is label noise, not the model, since `Homework` holds six birthdays and "do taxes".
- **Recurrence is materialised**, because `gcalcli add` has no flag for it: "every tuesday" writes twelve events and twelve rows. That is also what makes deleting one week's reading leave the other weeks standing. `for N weeks` and `until <date>` bound it; `[todo].recur_weeks` is the default and 40 occurrences is the cap.
- **Ticking an item deletes its Google event**, and the delete is the careful half. gcalcli has no delete-by-id — `delete` takes a search string and `--iamaexpert` removes everything it matches without asking — so `feeds.delete_calendar_event` runs the search itself first and only proceeds when one calendar, one day and an exact title match leave exactly one event, whose id matches the one recorded at write time. The id comes from `add --details url`, which prints the event's htmlLink; its `eid` is base64 of "<event id> <calendar id>". Anything ambiguous is left alone and said out loud. The tick itself is local and commits first, so an unreachable Google cannot stop you marking homework done — the item then shows "on calendar" and can be retried.
- **gcalcli's calendar cache goes stale and is load-bearing**: it is written once and never expires, so a calendar deleted in Google keeps being queried and Google answers 404, which fails the *whole* agenda call. `feeds.gcalcli` recognises that one signature and retries once after `--refresh`. Panels are shown the last line of gcalcli's traceback rather than the first 200 characters, which were always `sys.exit(main())`.
- **Log**: `dashboard.log`, rotating. Panels show one line; the traceback is here.

`run.cmd` and `.claude/launch.json` both call `run.sh`, so there is one launch path. The port lives only in `config.toml` — `.claude/launch.json` has to repeat it so the preview tool knows where to connect, and a test fails if the two drift apart.

## Tests

```bash
PY=~/.local/share/daily-dashboard/venv/bin; $PY/python -m pytest && npm test && $PY/ruff check . && $PY/ruff format --check .
```

897 Python tests and 113 JavaScript ones; none touch the network, the API, `dashboard.db`, or `state.json`. The JS runner is node's built-in `node --test`, so there is nothing to `npm install`.

The suite is weighted toward the code that was hardest to check by hand: the elo curve, the hint penalty, the theorem draw, streaks, the LaTeX-in-JSON repair, the cache's exception policy and staleness flag, the day-rollover guards, the double-submit guards, and the `state.json` importer's PROVENANCE/PRESERVE rules.

## Layout

```
dashboard/            the server
  config.py     config.toml -> validated settings
  logs.py       logging setup
  scoring.py    elo, the theorem draw, streaks — pure, no DB/network/clock
  spelling.py   the bee: the pool, the draw, the marking — pure
  trainplan.py  training.toml -> what today asks for — pure, no DB/clock
  tricks.py     the trick of the day: the marking, the callback — pure
  llm.py        one streaming Anthropic client + cost accounting
  feeds.py      RSS, Yahoo, arXiv, lichess, gcalcli + the stale-tolerant cache
  health.py     load, readiness, hydration, intake — pure, no DB/clock
  garmin.py     the Forerunner sync — optional, best-effort, never blocking
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
    chips.js    what counts as a day — the completion write and the `n` order
    tabs.js     the six sections, and which pane a panel mounts into
    nav.js      the focus ring — what h/j/k/l actually move
    capture.js  a panel borrowing the keyboard while you fill it in
    keys.js     the bindings, as data; the cheatsheet is generated from them
    carousel.js the four logging panels taking turns in one card
    nudges.js   what the day is still waiting on, said out loud
    piano.js    the piano voice — inharmonic partials, hammer, two strings
    panels/     one file per panel
    modals/     theorems, math, tricks, day logs (the archives) + trickset,
                and the sheet on how the calendar box reads a line
  data/         piano.json — scale/chord content, out of the logic file
  vendor/katex  see PROVENANCE.txt

tests/          pytest, plus tests/js for node --test
```

There is no bundler. The front end has no npm dependency at runtime — KaTeX is
vendored and everything else is hand-written — so native `<script type="module">`
does the whole job, and a build step would only add a thing that can break
between you and the page.

UI: six tabs, each a subject with one **hero** panel saying what the tab is for
and supporting panels under it — home (calendar, what is due, completion
heatmap, LLM spend) · health · math · politics · dailies · markets. Health sits
at 2 because it is the one section answered more than once a day. `dailies` is
the one tab with no hero, deliberately: it holds the day log, the word, the
bee, the two games and their streaks, the chess puzzle, the piano scale and the
day's arXiv article, and on a page of equal small obligations nothing is the
point. The content
column is capped at 1040px and centred, so the margins stay empty on a wide
screen.

Keyboard-first, and the mouse still works. `Tab`/`Shift-Tab` cycle sections,
`1`–`6` jump to one, `h`/`j`/`k`/`l` move a focus ring between panels — and,
after `Enter` descends into a panel, between the links and controls inside it.
`Enter` opens the focused thing, `Esc` backs out a level, `gg`/`G` go to the
first/last panel, `n` opens the next unfinished task, `?` lists the lot. `f`
letters every link in the panel and then opens the one you name — and carries
four letters of its own, `c`/`l`/`m`/`p`, which reach the check-in, the session
log, the meals and the in-and-out from any section. The
bindings are data in `keys.js`; the cheatsheet is generated from the same list
that dispatches them.

A panel can also **take the keyboard** while you fill it in (`capture.js`),
which is how the health forms are logged without the mouse. On the check-in,
`Enter` puts the caret in the weight box, `Enter` commits it and moves to hours
slept, and `Enter` there starts the questionnaire, where `1`–`5` answers the
highlighted row and drops to the next one (`j`/`k` to move without answering).
On the session log, `Enter` starts it, a letter picks the kind (`t` is strength
— sparring took `s`), a digit picks the RPE (`0` is 10) and lands the caret in
the minutes box, `Enter` goes to the note and `Enter` there logs it. While a
capture is up the digits belong to the panel; `Esc` — or any key the panel does
not use — gives them back to the tabs.

**A key the focused panel would have taken after an `Enter` takes it without
one.** `b` on the session log starts it *on boxing*; `s` on the meal log starts
it *on small*. Keys that take you somewhere else — `hjkl`, the section digits —
are never claimed this way, which is why the check-in's `1`–`5` still needs its
Enter first. A finished log lets go of the keyboard on its own, so the `Esc`
that used to follow every entry is one the entry no longer needs.

The meal log is quicker still, because it is answered three times a day and a
log that costs fifteen seconds is a log kept for nine days. **`m` logs a meal
from any tab** — one press switches section, lands on the panel and starts its
ladder, and every question after that is one key:

```
m l 4          a large, fairly unprocessed meal, now
m - - m 3      the lunch you forgot to log, an hour and a bit ago
f p  s 2 m     a soft stool, medium, now
f p  u 4 l     dark urine, large volume, now
```

There used to be a global `b` for the in-and-out log, and it is gone: `b` is
also the first letter of boxing, so on the session card it was genuinely
ambiguous which of the two you meant — and that ambiguity was the thing
standing in the way of the one-key rule above.

Each entry carries the hour it happened, rounded down, starting at now; `-` and
`+` walk it. Both panels are built from a list of steps in `quicklog.js` — the
bathroom one branches on its first answer (a stool is asked for solidity, urine
for colour) and nothing else about them differs.

**The four logging panels take turns.** Under the training hero the health tab
is two halves: the plan on the left, which is what you read, and on the right
one card that cycles through the check-in, the session log, the meals and the
in-and-out every five seconds — the shape of the Windows news strip, and for
its reason. Hovering pauses it, selecting it with the focus ring holds it, and
a ladder running on it freezes it outright, so nothing rotates through a
half-typed meal. The arrows turn it by hand once the ring is on it, and
`f c`/`f l`/`f m`/`f p` stop it on one from anywhere. Nothing is rebuilt on the
turn — only `hidden` moves — because these panels hold captures, half-typed
inputs and a cursor into a ladder, and tearing them down would drop all three
every five seconds.

**Nudges** (`nudges.js`) are the one thing here that arrives rather than
waiting to be looked at: the morning check-in still not done at eleven, a meal
not logged by lunchtime, the required half of the day still open at eight in
the evening, what is due today and what is overdue. Each has a clock on it and
a repeat interval measured in hours, because a notification you learn to
dismiss is worse than none; each is computed from the same `chipTasks()` that
defines a day, so the two cannot disagree about whether the maths is finished;
and each is a door — clicking one crosses to the section, stops the carousel on
the right card and lands the ring there. Cooldowns live in `localStorage` keyed
by the day, so a reload does not start the morning over.

## Key mechanics

- **One endpoint per write.** `POST /api/state` and `/api/state/merge` are gone — a browser tab can no longer replace the world with what it happened to be holding. Every write is a per-column `UPDATE` behind `Repository`.
- **The browser owns "today".** Every dated request carries the browser's local date. The server never calls `date.today()` to decide what day it is.
- **Elo**: dynamic K = max(24, 96/(1+n/8)) — fast movement with little history, stabilizing over time. The current rating is *derived* from the append-only `rating_event` log; the rating and the update are computed inside one transaction, so two tabs can't both read the same rating and clobber each other.
- **Math problems**: two free sources and no third. The overnight batch (`batch.sh`, generated in the Claude app against the subscription) is tried first, then the hand-curated pool in `problems.json`; if neither has an unseen problem the panel says so and names the fix. There used to be a paid fallback — an agentic run with web search, verified against AoPS, ~$0.15–0.80 a morning, and by far the most expensive call in the app — and it is gone: `content.py` has no prompt that produces a problem, so no click can bill for one. Theorem/textbook links come from `[topic_library]` in `config.toml`, not the LLM.
- **Late submission**: a day you drew a problem and never marked is auto-scored 0. For **14 days** you can still submit it from the math archive, and being late costs exactly what two hints cost — the whole ladder scaled by 0.4, so 0–3 hints already taken give `0.40 / 0.28 / 0.16 / 0.04`. Each hint costs 30% of what the attempt is still worth rather than a flat 0.30, which is what keeps a three-hint late solve from scoring −0.5. The late result *replaces* the zero: the lapse row stays in the append-only log and an `unlapsed` row hands back exactly what it took. A late row counts toward the K-factor (you did the problem) but is kept out of `success_rate`, whose threshold is 0.5 and which would otherwise read every late solve as a failure.
- **Math archive**: a modal off the math panel, holding the last 60 problems with statements, answers and what each did to the rating. A closed day shows its answer outright — that is what an archive is for — while a day still open to late submission keeps it behind a reveal, because a self-reported "solved" means nothing if the answer was already on screen.
- **Chess**: a lichess link plus three self-report buttons. Clean = 1.0, with mistakes = 0.4, fail = 0. The puzzle rating is still the elo opponent.
- **The trick of the day**: one free source and no other. The 3am task writes `batches/trick-<date>.json` (`batch.sh --trick-context`, checked with `--validate-trick`) and the page reads it at boot. The two other ways in are gone — the prompt the panel used to hand you to paste into the Claude app, and a ~$0.03 API generation behind a button — so a night the task missed is a morning that says so and names the two commands, on the math problem's reasoning. The set is worked on a full-width sheet (`modals/trickset.js`), marked by string comparison server-side, and rated in its own `tricks` bucket; the challenge is the one paid call left, and only if you answer it.
- **The day's cells gather themselves.** The word, the bee, the theorem card, the chess puzzle and the trick set are fetched at boot and again on rollover — `daily.js` — rather than each waiting behind a button whose only question was "yes, today's one, please". `needed` in that table is the guard that makes a second gather safe: `/api/chess/new` upserts the day's attempt, so an unguarded refetch would swap a half-finished puzzle for another one. The buttons survive as the fallback for a gather that failed, built from the same table so the two paths cannot diverge. A day you never open the page still draws nothing — the deck does not age and no word is paid for.
- **Feeds** (markets/calendar/news/paper) are cached server-side and serve stale data when the *source* is unreachable. A bug in our own parsing propagates instead — a stale panel and a broken parser used to look identical. The fallback now *says so*: every feed response carries `fetched_at`, `ttl_seconds` and `stale`, so the panel prints `as of 09:14` and turns it red when the source stopped answering. A stale panel and a live one used to look identical too.
- **Live panels** (markets/calendar/news) re-render themselves, scheduling the next fetch from the server's own `cache_seconds` — so the interval lives in `config.toml` alone, and a panel mounted late in a TTL window refreshes sooner rather than a full window later. Polling stops while the tab is hidden and resumes on the way back.
- **The day has two layers.** *Required* is the math tab minus the deck — the problem, the trick, the theorem recall — plus the morning check-in and three logged meals. Those five are the ring, `perfect ✦`, and the only thing the streak is counted off; a floor you can hold every day is worth more than a ceiling you reach twice a month. Everything else — chess, the word, the bee, the ballot or the issue reading, the argument pattern, the paper, Wordle, Pokedoku, the day log — is *optional* and worth **points** instead, 1–3 each by minutes-and-attention. The statusline reads `5/5 perfect ✦ · +6 of 14 pts`, and the total is stored in `day_completion.bonus` rather than recomputed, because a month from now the weights may have changed and what a given day earned would be unrecoverable. `n` still walks the required ones first and only then starts offering points. Three meals earns its place in the required layer by being the one item there that is a *report* rather than a task — he eats three times a day whatever the dashboard says, so a day with two rows in it is a day with a row missing, and estimated intake against measured expenditure is not an honest comparison on a series with holes in it.
- **The health tab answers three questions and then shuts up**: am I recovering, am I training hard enough, which way is my weight going. Load is session-RPE (`rpe × minutes`) rather than anything heart-rate derived, because wrist HR under wraps and a glove is the least trustworthy number the watch produces. Every derived figure returns `None` until it has the history to mean something, and `None` renders as *nothing* — not a dash, not a zero, not "collecting data" — so a quiet tab in week one is the correct tab. The in-and-out logs follow the same rule one step further: each of them first asks whether the log was being *kept*, because a day with no stool row means you forgot, and a panel that cannot tell that from constipation is a panel that warns you about your Sundays.
- **Food is buckets, and says so.** A meal is `small`/`medium`/`large` plus how processed it was, 1–5. Self-reported calorie counts are out by about a quarter in the direction that flatters you; three buckets is the same guess with its error bars showing. `health.MEAL_KCAL` is the one place they become numbers again, and the estimate is only ever printed *as a clause on the measured weight trend* — when the two disagree by more than a pound a week the panel says the estimate is wrong, because the scale is a measurement and this is arithmetic over three words.
- **The Garmin sync is optional, best-effort, and never blocking.** `pip install -e '.[garmin]'`, then `$PY -m dashboard.garmin login` once to mint a token store (that command is the only thing here that ever sees the password; the server logs in from tokens). After that, opening the dashboard kicks a background sync of sleep, HRV, resting HR, Body Battery, calories out and hydration — there is no 3am job, because the machine is asleep at 3am. **A hand entry is never overwritten by a sync**, enforced in the `WHERE` of one `UPSERT` in `Repository.log_health`, and that single rule is what makes correcting a bad reading worth doing at all. Activities are deliberately *not* imported: load is `rpe × minutes` and an RPE nobody answered is half a number. Every failure — missing library, stale token, an endpoint that changed shape overnight — degrades to a metric arriving absent, so the hero prints when the last sync worked and what went wrong if it did not.
- **The day rolls over on its own.** The tab is one you leave open, so a 60s check plus `visibilitychange` reloads everything when the browser's local date moves on. The completion write is addressed to the day its counts were read off, never to the clock — otherwise a tab open past midnight posted yesterday's finished count against today, and `done = total` is what makes a perfect day.
- **No `innerHTML`.** Every panel builds real nodes through `dom.js`, so text is a text node and escaping is structural rather than a hand-rolled `esc()` each render has to remember to call.
- **Offline-clean.** The page makes no request to anything but `127.0.0.1`.
- **Adding a panel**: a request/response model in `models/api.py`, a repository method, a route in `server.py`, and a module in `web/app/panels/` exporting `{ id, title, tab, slot }` — added to the `PANELS` list in `main.js`, whose order *is* the layout within each slot. `slot: "hero"` claims the top of the tab; anything else lands in the grid below it. If it is another x-of-the-day, add one entry to `DAILIES` in `daily.js` and call `opener(key)` where the button would have gone — that buys the boot fetch, the busy line and the retry together.
- **Adding a tab**: one entry in `TABS` in `tabs.js` and one SVG in `web/assets/bg/`. The digit shortcut is the array index, so there is no second table of bindings to keep in sync.
