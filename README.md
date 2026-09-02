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
- **The week grid is clamped to 8am20138pm** and does not widen for outliers. It used to, and the page therefore got *longer* the busier the week was 2014 one 11:30pm event made every day on screen sixteen hours tall and pushed the year-so-far heatmap under the fold. Events lying wholly outside the window are not dropped: they go to a one-line strip above or below the grid, keeping their title and their time and giving up only their height. An event that merely straddles an edge stays in the grid and is clipped by it. `CLAMP` at the top of `calendar.js` turns the whole thing off and restores the old widening. The add box folds to a single line when nothing is being typed into it 2014 focus, hover or any text in it brings the picker, the buttons and the preview back.
- **Calendar**: runs gcalcli directly as an argv list (prefixed with `wsl -e` when the server is on Windows), so gcalcli must be authed in WSL. stdin is closed on every call — gcalcli drops to an interactive menu whenever a subcommand cannot resolve its target calendar, and with stdin inherited that menu *hangs* until the timeout instead of failing. Every write names its `--calendar`, for the same reason.
- **Quick add**: the box on the home hero takes one line per item ("fpm Thornhill Chapter 3 due thursday"), and a whole pasted list at once — Ctrl+Enter commits, the lines that could not be written stay behind with their errors while the rest disappear. `calparse.py` resolves the title, day and span locally; `todo.py` expands aliases and reads recurrence before it; `calclass.py` picks the calendar with a scikit-learn model fitted at boot on the account's own history — an event's calendar is its label, so the training set costs nothing. `POST /api/calendar/preview` resolves every line without writing, which is what the preview under the box shows.
- **A line with no day in it is refused, unless it starts with `todo`.** `calparse` still resolves a dateless line onto today — that is what makes it usable for an event — but it reports `date_explicit`, and the box rejects the line rather than inventing a due date. The exception is a word, not a mode: `todo fix the coffee table` (also `todo:`, in any case) says the omission was deliberate and writes **a todo and no Google event** — the one thing the box makes that Google never hears about. Those have no day, so no calendar and nothing to delete, and the schema enforces all four together. They land under the rule at the foot of the due list. It has to be the *first* word, so `add a todo item friday` is an ordinary Friday event. A repeat cannot be undated, so `todo water the plants every tuesday` is refused.
- **Aliases** (`[todo.subjects]` in `config.toml`) are whole-word substitutions, like variables: `os` → `Operating Systems` fires in "new os homework" and never in "buy banjos". Keys must be one word and are refused at load if they collide with a word the date parser reads (`sat`, `may`). Whether one fired is drawn on every previewed line and on the box's own border, because a subject you *forgot* to abbreviate looks exactly like ordinary text otherwise. The circled `?` lists them.
- **Todo or event is decided by the target calendar**, not by a mode or a sigil — `[todo].todo_calendars` names the ones that mean work, and those get a row in the due list. The picker beside the box defaults to `auto`, which is *not* the classifier: it is the subject's own calendar, then `calclass`'s tests rule, then Homework, because nineteen dated lines in twenty are homework. `learned` is the model, one Tab away, and choosing any calendar explicitly is stored in `calendar_correction` and refitted at a heavier weight. Only writable calendars are offered — a `reader` calendar is drawn in the grid and can never be written to. Expect `learned` to be right about 66% of the time and to hold the answer in its top two about 92% (5-fold CV, 338 titles, 56.5% majority baseline); the ceiling is label noise, not the model, since `Homework` holds six birthdays and "do taxes".
- **Recurrence is materialised**, because `gcalcli add` has no flag for it: "every tuesday" writes twelve events and twelve rows. That is also what makes deleting one week's reading leave the other weeks standing. `for N weeks` and `until <date>` bound it; `[todo].recur_weeks` is the default and 40 occurrences is the cap.
- **Ticking an item deletes its Google event**, and the delete is the careful half. gcalcli has no delete-by-id — `delete` takes a search string and `--iamaexpert` removes everything it matches without asking — so `feeds.delete_calendar_event` runs the search itself first and only proceeds when one calendar, one day and an exact title match leave exactly one event, whose id matches the one recorded at write time. The id comes from `add --details url`, which prints the event's htmlLink; its `eid` is base64 of "<event id> <calendar id>". Anything ambiguous is left alone and said out loud. The tick itself is local and commits first, so an unreachable Google cannot stop you marking homework done — the item then shows "on calendar" and can be retried.
- **gcalcli's calendar cache goes stale and is load-bearing**: it is written once and never expires, so a calendar deleted in Google keeps being queried and Google answers 404, which fails the *whole* agenda call. `feeds.gcalcli` recognises that one signature and retries once after `--refresh`. Panels are shown the last line of gcalcli's traceback rather than the first 200 characters, which were always `sys.exit(main())`.
- **Log**: `dashboard.log`, rotating. Panels show one line; the traceback is here.

## The phone log — `/m`

The five logs off the health tab — check-in, session, meals, drinks, in-and-out — as a screen you can use one-handed, standing up, with the laptop shut. Same server, same endpoints, same questions: `web/app/logs.js` holds what each log asks and both clients import it, so a step added there grows on both. It is *not* the desktop cell made narrow — that cell is a keyboard ladder (`m` `l` `4` is a large clean meal) and none of that argument survives a touchscreen, so the ladder is unrolled into rows of 48px buttons answered in any order.

The theme is identical by construction rather than by eye: `web/tokens.css` was pulled out of `style.css` and both stylesheets import it.

**The session card answers the plan.** Its first question is which of today's prescribed blocks this was, and the answer rides along as `plan_key` — without it a lift logged in the gym went up as a bare load, and the plan card on the laptop went on asking all evening for a lift that had already happened. That is a wrong answer which by construction only ever appears on the screen you did *not* log from. Nothing infers the link after the fact: a session logged off-plan carries no key and closes nothing, because pairing the two up by kind would be the load model guessing. Picking a block fills in the kind where the plan names one and narrows the choices where it deliberately does not (cardio is roadwork or shadowboxing, and the difference is only recoverable if it is recorded at the time); *something else* is the way back to the freehand form, which is also what a rest day gets. One entry per block rather than the laptop's one card per outing — the parts of a practice are logged as you finish them, and the card on the laptop closes once all of its rows have arrived. `answeredKeys`/`openBlocks` in `logs.js` are the single definition of "already logged" that both clients read, so they cannot come to different conclusions about it.

**Every write is queued, and the queue is drained when you press *send*.** Nothing in the app reaches the laptop on its own. Logging is entirely local and entirely instant — no `fetch` on the write path, so a tap cannot hang on a captive portal or a sleeping tailnet — and the one button in the hour bar is the only thing that talks to the server. It counts *everything* owed rather than today's entries, which matters the morning after a day logged offline; it reports a partial send as one; and when the laptop is not answering it says so, which is the point. An automatic flush fails silently into a counter that does not go down.

This is nearly free because of [Invariant 1] — every request already carries the browser's own date, and the three quick logs carry the hour, so a meal logged at 13:00 on Tuesday and flushed at 21:00 on Wednesday still lands at 13:00 on Tuesday. The server never consults its own clock. Queued entries are drawn on screen immediately, marked `queued`, alongside the ones the server already has. The queue drains oldest-first and stops at the first write it cannot send; a 4xx is dropped rather than retried forever, because an entry the server will never accept otherwise blocks everything behind it. There is **no de-duplication**: if the server commits a write and the response is lost, the flush retries and the row lands twice. The fix is one tap on the entry; the cure would be an idempotency column on four tables.

**Getting your phone to it** is the actual work, and none of it is in this repo. The server binds `127.0.0.1` and has no auth, which is correct for what it is.

1. **Tailscale, with `serve`** — the only answer that gets the whole feature. Phone and laptop join a private mesh; nothing is exposed publicly and there is no port forwarding.

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8502
   ```

   This is not a tidiness step. Service workers require a secure context, and a raw address is not one — **over `http://192.168.x.x` *or* `http://100.x.y.z` the app cannot install as a PWA or open offline**, which reduces it to a URL that needs a live laptop to draw a screen at all, with a perfect queue underneath that you cannot reach. `serve` gives it HTTPS on a real `*.ts.net` name and the worker registers. The config persists across reboots in `tailscaled`; the dashboard itself does not, and nothing on the machine starts it.

   Note what this does and does not buy. `serve` runs *on the laptop*: with the laptop off or asleep the name resolves and nothing answers, exactly as before. What it buys is that the phone stops needing an answer — install once over HTTPS and the app opens from cache from then on, on any network or none.
2. **`[server].host = "0.0.0.0"` on your own wifi** — one line, works immediately, LAN-only, no offline install, and an unauthenticated dashboard on the network. Fine for twenty minutes of deciding whether the thing is worth using.
3. Public exposure — no.

Then open `/m` and use the browser's *add to home screen*. `display: standalone` in the manifest is what drops the address bar.

**`web/m/sw.js` caches the shell and has a hand-bumped `VERSION`.** Change any file it lists and bump it, or the phone keeps the old copy until the cache is evicted — an edit that does not appear, which is the same failure `StaticAssets`'s `no-cache` exists to prevent on the desktop. The API is deliberately never cached: a stale `GET /api/health` is yesterday's meals presented as today's, and "the server is unreachable" already has an honest answer on that screen.

`SHELL` must cover the phone's whole module graph, because an ES module graph fails whole — one unlisted import is a blank screen, not a degraded one, and it shows up on the first cold open after a `VERSION` bump with no server, which is the one morning the cache exists for. It does not fail on wifi, because the fetch handler caches whatever it fetches on the way past. `tests/js/shell.test.js` walks the imports and asserts the closure, and the rule it enforces is: **nothing under `web/app/` may be imported by `/m` unless it imports nothing itself.** That is why `logs.js` imports nothing and why `applicableSteps`/`parseAmount` live there rather than in `quicklog.js` — the phone pulled them out of `quicklog.js` once, and silently brought `api.js`, `capture.js`, `nav.js`, `store.js` and `tabs.js` with them.

Fetches race a 2s timer against the cache rather than waiting on the network: an unreachable host refuses in milliseconds, but a network that *drops* rather than refuses holds the socket open for tens of seconds per file, and sixteen files of that is an app that appears not to launch. `install` adds each file separately rather than with `addAll`, which is atomic — one 404 in the list above and there would be no cache at all.

`run.cmd`, `boot.cmd` and `.claude/launch.json` all call `run.sh`, so there is one launch path. The port lives only in `config.toml` — `.claude/launch.json` has to repeat it so the preview tool knows where to connect, and a test fails if the two drift apart.

**`boot.cmd` is the same launch minus the browser tab, run by a logon task.** Register it with `powershell -ExecutionPolicy Bypass -File install-boot-task.ps1` (`-Remove` to undo). It exists because the phone holds its writes until you press *send*, and send needs the server up at the moment you press it — Tailscale's service comes back after a reboot on its own and this does not, so the cost of forgetting is a failed send from a gym, which is the exact situation the queue was built for. It runs as you, interactively, 30s after sign-in: not SYSTEM, no stored password, because the server reaches into WSL, your venv, `dashboard.db` and a Tailscale identity that belongs to your account. The tab is suppressed with `DASHBOARD_NO_BROWSER` rather than `open_browser = false`, so launching by hand is unaffected.

## Tests

```bash
PY=~/.local/share/daily-dashboard/venv/bin; $PY/python -m pytest && npm test && $PY/ruff check . && $PY/ruff format --check .
```

1038 Python tests and 171 JavaScript ones; none touch the network, the API, `dashboard.db`, or `state.json`. The JS runner is node's built-in `node --test`, so there is nothing to `npm install`.

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
  tokens.css    the theme: palette, type scale, spacing. Imported by both
                stylesheets, so the phone and the dashboard cannot drift
  style.css     every panel style is a class here
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
    piano.js    the sampler behind the scale and chord buttons
    logs.js     what the five logs ask — shared with the phone, imports nothing
    panels/     one file per panel
    modals/     theorems, math, tricks, day logs (the archives) + trickset,
                and the sheet on how the calendar box reads a line
  m/            the phone log — see 'The phone log' above
    app.js      the five logs as touch targets; the ladder, unrolled
    queue.js    writes that have not reached the server yet
    when.js     the date and the hour, which here are the whole point
    sw.js       caches the shell so it opens with the laptop asleep
  data/         piano.json — scale/chord content, out of the logic file
  vendor/katex  see PROVENANCE.txt
  vendor/piano  one mp3 per semitone, C42013Ab6 2014 see PROVENANCE.txt

tests/          pytest, plus tests/js for node --test
```

There is no bundler. The front end has no npm dependency at runtime — KaTeX and
the piano samples are vendored and everything else is hand-written — so native
`<script type="module">` does the whole job, and a build step would only add a
thing that can break between you and the page.

**The piano is recorded, not synthesised.** `web/vendor/piano` is one mp3 per
semitone from middle C to Ab6 off the FluidR3 GM grand — 800 KB, MIT, served
from this origin and cached immutable beside KaTeX. One file per semitone
rather than the usual handful pitch-shifted to fill the gaps, because
resampling drags the formants of the body along with the pitch of the string:
a note shifted up three semitones is not a piano playing higher, it is a
smaller piano, and the seam is most audible inside a chord, which is the only
thing that panel plays. At 25 KB a note there is nothing to resample.

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
first/last panel, `n` opens the next unfinished task, `?` lists the lot.

`f` is three layers over one press, and they do not collide because only one of
them is an alphabet: **letters** open the links it has just lettered,
`c`/`d`/`l`/`m`/`p` reach the check-in, the drinks, the session log, the meals
and the in-and-out from any section, and **digits** open that numbered
notification.
Standing on an article you get `f a`–`f e` on the headlines, `f m` to the meal
log and `f 1` to whatever is in the corner. The mode line prints all three
ranges while the layer is up. The
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

So the flow is: **move the ring to a cell, then one key enters it.** `i` for a
text box; `b` on the session log starts it *on boxing*; `s` on the in-and-out
log starts it *on stool*; `w` on the drinks log starts it *on water*; `m`/`p` on
the meal log open it standing on the hour, one step earlier or later. Enter enters whatever the cell's first question is.
After that a single-answer field advances by itself and a text box advances on
Enter, and the last answer commits — a finished log lets go of the keyboard on
its own, so the `Esc` that used to follow every entry is one the entry no
longer needs.

Keys that take you somewhere else — `hjkl`, the section digits — are never
claimed by a panel this way, which is why the check-in's `1`–`5` still needs
its Enter first. `m` is the one exception in the other direction: it is global
("go to the meal log") on every card except the meal log, where there is
nowhere left to go and it means minus an hour.

**`m` logs a meal from any tab** — one press switches section, lands on the
panel and starts its ladder, and every question after that is one key:

```
m l 4          a large, fairly unprocessed meal, now
m m m ⏎ l 4    a large meal two hours ago — m and p are minus and plus
f p  s 2 m     a soft stool, medium, now
f p  u 4 l     dark urine, large volume, now
f d  w ⏎       one drink of water, now
f d  c 1.4 ⏎   a 16.9oz coffee
b 7 45 ⏎ ⏎     45 minutes of boxing at RPE 7
```

The meal log asks the hour *first*, which is why `m` at that card steps it back
rather than picking a size, and why `⏎` — "the hour is already right" — is what
skips past it. Only the meal log does this: the other two have no global key of
their own and are answered more often, so a question in front of either would
cost a keystroke on every entry to save one on a few.

There used to be a global `b` for the in-and-out log, and it is gone: `b` is
also the first letter of boxing, so on the session card it was genuinely
ambiguous which of the two you meant — and that ambiguity was the thing
standing in the way of the one-key rule above.

Each entry carries the hour it happened, rounded down, starting at now; `-` and
`+` walk it. All three panels are built from one list of steps in `quicklog.js`,
and they differ in exactly two ways: the bathroom one branches on its first
answer (a stool is asked for solidity, urine for colour), and the drinks one
ends in a **typed** answer rather than a row of buttons.

**Drinks are counted in drinks**, where a drink is twelve ounces of it — a can.
What was in it is a set of **tags** rather than a kind: milk, sugar,
electrolyte, coffee, alcohol, any of them together, because coffee with sugar
in it is both and a sports drink is salt and sugar both. There is no `water`
tag — every drink here is water with something in it, so a tag saying so would
be on every row — and an untagged entry *is* the glass of water, which makes
the commonest drink of the day the one that needs no answer at all. `gatorade`
is gone the same way: it was `electrolyte` and `sugar` together and nothing
else, and the rows already logged under it carry both tags now. The box takes
`1.5`, `.5` and `1 1/2` alike and arrives holding `1`, so the number is only
typed when there is something to say. It is the one bucketless field in either
log, and the reason is that the answer is *printed on the bottle*: sizes and
volumes are buckets because nobody knows how big their lunch was, and rounding
a measurement to small/medium/large would throw away accuracy that cost
nothing. Alcohol changes the unit to the standard drink — a shot, a beer and a
glass of wine are each 1 — and takes the whole row out of the day's fluid,
mixer included. Those rules live behind the `?` on the card rather than on it,
for the reason the calendar's sheet gives: a
panel whose whole claim is that it costs nothing cannot open with a paragraph
of definitions. The tab carries no dot and asks nothing of the day — a
hydration target you hit by *logging* is a target that teaches you to log, and
the panel already has a measurement of whether it worked one tab over.

What it feeds: the hero quotes the drink log's ml/day on the dark-urine line in
preference to the watch's hydration glyph (that one is tapped by hand, so a day
nobody tapped it is indistinguishable from a day nobody drank), and it adds two
lines of its own — a week averaging over three coffees a day, and four or more
standard drinks in seven days. Both are there as the cheap explanations for
lines already above them: a flat HRV week and a sleep score that does not match
the hours usually have a duller cause than overtraining. Neither says anything
until the log has been kept five days out of seven.

**The five logging panels take turns.** Under the training hero the health tab
is two halves: the plan on the left, which is what you read, and on the right
one card that cycles through the check-in, the session log, the meals, the
drinks and the in-and-out every three seconds — the shape of the Windows news strip, and for
its reason. Hovering pauses it, selecting it with the focus ring holds it, and
a ladder running on it freezes it outright, so nothing rotates through a
half-typed meal. The arrows turn it by hand once the ring is on it, and
`f c`/`f d`/`f l`/`f m`/`f p` stop it on one from anywhere. Nothing is rebuilt on the
turn — only `visibility` moves — because these panels hold captures,
half-typed inputs and a cursor into a ladder, and tearing them down would drop
all three every three seconds. `visibility` and not `display`, because the
slides share one grid cell so the card is always as tall as the tallest of
them: hidden the other way they contribute no height and the card resized 200px
under the pointer every time it turned. It also stops turning entirely when its
section is not the one showing.

**Nudges** (`nudges.js`) are the one thing here that arrives rather than
waiting to be looked at: the morning check-in still not done at eleven,
breakfast/lunch/dinner unlogged at 8am/2pm/9pm, the required half of the day
still open at eight in the evening, what is due today and what is overdue. Each
is numbered and `f` plus that digit opens it. Each has a clock on it and
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
- **The week has a shape.** Every day used to ask for the same thing — a ladder centred on the rating, nearest rung served — and it converged exactly as a thermostat should: every batch generated between 2026-08-20 and 2026-08-28 offered the identical three tiers, and the middle one was served. Five days a week of AIME problems is a variance problem, not a difficulty one, and no prompt fixes it while the selection rule is what removes the variance. `[math.week]` names what each weekday asks for: **Sunday** reads — three half-hour segments, one or two expected, unrated, and doing any of it ticks the day; **Monday and Tuesday** keep the calibrated ladder, which is the right mechanism for a day whose job is to be calibrated; **Wednesday to Saturday** come out of *Putnam and Beyond*, with Thursday and Friday sharing one problem across two days.
- **The two-day problem.** Thursday offers no hints at all — you earn the first by having spent a day without one, and the second, which more or less hands the argument over, only on Friday. Enforced server-side: tomorrow's hints are not in today's payload, unlike the old panel, which shipped all three and merely declined to render them. Solving it on day one multiplies K by 3 — about **+58** once the track is mature, **+78** early, capped at what a first-ever result gets — and buys Friday off. An unmarked Thursday *carries* rather than lapsing: forgetting to press a button should not cost a full result on a problem you were meant to still be holding.
- **Proofs rate into their own bucket.** A proof is scored three ways — proved / partial / couldn't — against a rubric the batch writes from the book's own printed solution, with hints costing 0.30 then 0.60, so the ladder is 1.0 / 0.70 / 0.10 and a fully-hinted proof lands exactly where a fully-hinted compute problem lands. Those results go to `proofs` and nowhere else, which is `tricks`' move made for a sharper reason: honest proof scores sit below honest numeric ones, so letting them into the `[math.areas]` buckets would drag the very numbers that pitch Monday and Tuesday, and four hard days a week would make the two calibrated days *easier*. No timer on a proof day either — a reference time for a problem designed to be sat with would pay for hurrying on the one day of the week that exists for not hurrying.
- **The generator reads the book; it never recalls it.** A model asked at 3am for "a problem from Gelca–Andreescu on polynomials" writes one that reads exactly like the book and is not in it — a drifted statement, an invented number, a solution that was never printed — and `verified_url`, the batch's one provenance check, does not exist for a textbook. So `dashboard/proofbook.py` parses the contents into a committed `proofbook.json` (91 leaf sections, printed *and* PDF pages, problem-number spans) and the batch hands over a page range to read. The PDF is gitignored and nothing at runtime opens it. Two things the parse had to measure rather than assume: the printed-to-PDF offset drifts from 15 at page 1 to 10 by the solutions, and problem numbers run once through the whole volume — which is what makes `P&B 512` a unique `source` and therefore a working do-not-repeat key.
- **Math problems**: two free sources and no third. The overnight batch — `batch.sh --week`, generated in the Claude app against the subscription — is tried first, then the hand-curated pool in `problems.json`; if neither has an unseen problem the panel says so and names the fix. There used to be a paid fallback, an agentic run with web search at ~$0.15–0.80 a morning, and it is gone. The one paid call left is the level check for a problem you bring yourself on the showcase day: a few hundred tokens on Haiku, fired only when you paste something. A self-declared tier is the one anchor nobody can check, and "this felt hard" is a different claim from "this is Putnam-level".
- **Late submission**: a day you drew a problem and never marked is auto-scored 0. For **14 days** you can still submit it from the math archive, and being late costs exactly what two hints cost — the whole ladder scaled by 0.4, so 0–3 hints already taken give `0.40 / 0.28 / 0.16 / 0.04`. Each hint costs 30% of what the attempt is still worth rather than a flat 0.30, which is what keeps a three-hint late solve from scoring −0.5. The late result *replaces* the zero: the lapse row stays in the append-only log and an `unlapsed` row hands back exactly what it took. A late row counts toward the K-factor (you did the problem) but is kept out of `success_rate`, whose threshold is 0.5 and which would otherwise read every late solve as a failure.
- **Math archive**: a modal off the math panel, holding the last 60 problems with statements, answers and what each did to the rating. A closed day shows its answer outright — that is what an archive is for — while a day still open to late submission keeps it behind a reveal, because a self-reported "solved" means nothing if the answer was already on screen.
- **Chess**: a lichess link plus three self-report buttons. Clean = 1.0, with mistakes = 0.4, fail = 0. The puzzle rating is still the elo opponent.
- **The trick of the day**: one free source and no other. The 3am task writes `batches/trick-<date>.json` (`batch.sh --trick-context`, checked with `--validate-trick`) and the page reads it at boot. The two other ways in are gone — the prompt the panel used to hand you to paste into the Claude app, and a ~$0.03 API generation behind a button — so a night the task missed is a morning that says so and names the two commands, on the math problem's reasoning. The set is worked on a full-width sheet (`modals/trickset.js`), marked by string comparison server-side, and rated in its own `tricks` bucket; the challenge is the one paid call left, and only if you answer it.
- **The day's cells gather themselves.** The word, the bee, the theorem card, the chess puzzle and the trick set are fetched at boot and again on rollover — `daily.js` — rather than each waiting behind a button whose only question was "yes, today's one, please". `needed` in that table is the guard that makes a second gather safe: `/api/chess/new` upserts the day's attempt, so an unguarded refetch would swap a half-finished puzzle for another one. The buttons survive as the fallback for a gather that failed, built from the same table so the two paths cannot diverge. A day you never open the page still draws nothing — the deck does not age and no word is paid for.
- **Feeds** (markets/calendar/news/paper) are cached server-side and serve stale data when the *source* is unreachable. A bug in our own parsing propagates instead — a stale panel and a broken parser used to look identical. The fallback now *says so*: every feed response carries `fetched_at`, `ttl_seconds` and `stale`, so the panel prints `as of 09:14` and turns it red when the source stopped answering. A stale panel and a live one used to look identical too.
- **Live panels** (markets/calendar/news) re-render themselves, scheduling the next fetch from the server's own `cache_seconds` — so the interval lives in `config.toml` alone, and a panel mounted late in a TTL window refreshes sooner rather than a full window later. Polling stops while the tab is hidden and resumes on the way back.
- **The day has two layers.** *Required* is the math tab minus the deck — the day's math, the trick, the theorem recall — plus the morning check-in and three logged meals. Those five are the ring, `perfect ✦`, and the only thing the streak is counted off; a floor you can hold every day is worth more than a ceiling you reach twice a month. Everything else — chess, the word, the bee, the ballot or the issue reading, the argument pattern, the paper, Wordle, Pokedoku, the day log — is *optional* and worth **points** instead, 1–3 each by minutes-and-attention. The statusline reads `5/5 perfect ✦ · +6 of 14 pts`, and the total is stored in `day_completion.bonus` rather than recomputed, because a month from now the weights may have changed and what a given day earned would be unrecoverable. `n` still walks the required ones first and only then starts offering points. What ticks the math item depends on the day: a marked problem on five of them, one segment of the reading plan on Sunday, and carrying the two-day problem on Thursday — you did the day's work, and the result lands tomorrow. A day two that a day-one solve turned into a day off is ticked outright, because a reward that costs you a perfect day is not one. Three meals earns its place in the required layer by being the one item there that is a *report* rather than a task — he eats three times a day whatever the dashboard says, so a day with two rows in it is a day with a row missing, and estimated intake against measured expenditure is not an honest comparison on a series with holes in it.
- **The health tab answers three questions and then shuts up**: am I recovering, am I training hard enough, which way is my weight going. Load is session-RPE (`rpe × minutes`) rather than anything heart-rate derived, because wrist HR under wraps and a glove is the least trustworthy number the watch produces. Every derived figure returns `None` until it has the history to mean something, and `None` renders as *nothing* — not a dash, not a zero, not "collecting data" — so a quiet tab in week one is the correct tab. The in-and-out logs follow the same rule one step further: each of them first asks whether the log was being *kept*, because a day with no stool row means you forgot, and a panel that cannot tell that from constipation is a panel that warns you about your Sundays.
- **Food is buckets, and says so.** A meal is `small`/`medium`/`large` plus how processed it was, 1–5. Self-reported calorie counts are out by about a quarter in the direction that flatters you; three buckets is the same guess with its error bars showing. `health.MEAL_KCAL` is the one place they become numbers again, and the estimate is only ever printed *as a clause on the measured weight trend* — when the two disagree by more than a pound a week the panel says the estimate is wrong, because the scale is a measurement and this is arithmetic over three words.
- **The Garmin sync is optional, best-effort, and never blocking.** `pip install -e '.[garmin]'`, then `$PY -m dashboard.garmin login` once to mint a token store (that command is the only thing here that ever sees the password; the server logs in from tokens). After that, opening the dashboard kicks a background sync of sleep, HRV, resting HR, Body Battery, calories out and hydration — there is no 3am job, because the machine is asleep at 3am. **A hand entry is never overwritten by a sync**, enforced in the `WHERE` of one `UPSERT` in `Repository.log_health`, and that single rule is what makes correcting a bad reading worth doing at all. Activities are deliberately *not* imported: load is `rpe × minutes` and an RPE nobody answered is half a number. Every failure — missing library, stale token, an endpoint that changed shape overnight — degrades to a metric arriving absent, so the hero prints when the last sync worked and what went wrong if it did not.
- **The day rolls over on its own.** The tab is one you leave open, so a 60s check plus `visibilitychange` reloads everything when the browser's local date moves on. The completion write is addressed to the day its counts were read off, never to the clock — otherwise a tab open past midnight posted yesterday's finished count against today, and `done = total` is what makes a perfect day.
- **No `innerHTML`.** Every panel builds real nodes through `dom.js`, so text is a text node and escaping is structural rather than a hand-rolled `esc()` each render has to remember to call.
- **Offline-clean.** The page makes no request to anything but `127.0.0.1`.
- **Adding a panel**: a request/response model in `models/api.py`, a repository method, a route in `server.py`, and a module in `web/app/panels/` exporting `{ id, title, tab, slot }` — added to the `PANELS` list in `main.js`, whose order *is* the layout within each slot. `slot: "hero"` claims the top of the tab; anything else lands in the grid below it. If it is another x-of-the-day, add one entry to `DAILIES` in `daily.js` and call `opener(key)` where the button would have gone — that buys the boot fetch, the busy line and the retry together.
- **Adding a tab**: one entry in `TABS` in `tabs.js` and one SVG in `web/assets/bg/`. The digit shortcut is the array index, so there is no second table of bindings to keep in sync.
