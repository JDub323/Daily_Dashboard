// The x-of-the-day cells, fetched at boot rather than on a press.
//
// Each of these used to open with a button whose only question was "would you
// like today's one?" — and on a dashboard you open once a morning the answer
// is always yes. Four presses to reach the state the page could have arrived
// in, on four different tabs, before any of the actual work started.
//
// So it arrives in it. `gatherDailies` runs once at boot and once per day
// rollover, asks for whatever today is missing, and drops each answer into the
// store as it lands — one panel at a time, so a slow word does not hold up the
// puzzle. The button is not gone: it is the fallback for a gather that failed,
// and `opener` builds it from the same table, so the manual path cannot come
// to mean something different from what the morning already tried.
//
// [Invariant] Gathering happens here — once per boot, once per rollover — and
// never inside a panel's `render`. Panels are redrawn by any store change at
// all: a live feed landing, a card added two panels down, the minute-by-minute
// day check. Two of these calls have consequences under that. `/api/chess/new`
// upserts the day's attempt, so a second one swaps the puzzle out from under a
// board you are half way through; the theorem draw ages the deck. `needed` is
// the first guard against that and the in-flight state below is the second.
//
// A day you never open the dashboard still gathers nothing, which is the
// property the theorem draw was built around and is worth keeping: the deck
// does not age, the puzzle that would have come up is still waiting, and no
// word is paid for. `scoring`'s lapse rule makes the same call about a math
// problem. What changed is only that *opening the page* is now the whole
// gesture, rather than opening it and then saying so four times.
//
// The math problem is deliberately not in this table, and being free and
// instant did not change that. Drawing it opens an attempt, and an attempt
// that is never marked lapses at 0.0 — the rule is that abandoning a problem
// you asked for costs a full result, while a week away costs nothing. A gather
// on boot would make opening a tab the asking, and charge for it.

import * as api from "./api.js";
import { el } from "./dom.js";
import { rerender } from "./panel.js";
import { put, store } from "./store.js";

/**
 * The cells that fetch themselves, and everything each one needs to.
 *
 * `key` is the store slice, `panel` the registered panel id. They differ for
 * the theorem card (`theorems` / `theorem`), which is why they are two fields
 * rather than one name used twice.
 *
 * `needed` reads the cache and answers "is today's already here?" — it is what
 * makes a second gather safe on the two endpoints that are not idempotent.
 * `open` is shared by the automatic path and the button, so the fallback
 * cannot drift from the thing it is falling back to.
 *
 * Adding a cell here is the whole job: the panel calls `opener(key)` where it
 * used to build a button, and gets the boot fetch, the busy line and the retry
 * from that one call.
 */
const DAILIES = [
  {
    key: "word",
    panel: "word",
    label: "get today's word",
    busy: "asking…",
    // The only paid gather, and the cheapest generation in the app — well
    // under a cent, against the math problem's $0.15–0.80. `/api/word/today`
    // hands back the stored row if there is one, so this is at most one
    // generation a day however many times the page is opened.
    needed: (state) => !state.word,
    open: () => api.post("/api/word/today"),
  },
  {
    key: "spelling",
    panel: "spelling",
    label: "start today's bee",
    busy: "drawing…",
    needed: (state) => state.spelling.enabled && !state.spelling.words.length,
    open: () => api.post("/api/spelling/new"),
  },
  {
    key: "theorems",
    panel: "theorem",
    label: "draw today's theorem",
    busy: "drawing…",
    // An empty deck has nothing to draw, and asking anyway would put an error
    // under a panel whose real answer is "add a theorem below".
    needed: (state) => state.theorems.deck_size > 0 && !state.theorems.today,
    open: () => api.get("/api/theorem/today", { date: api.today() }),
  },
  {
    key: "chess",
    panel: "chess",
    label: (state) => `get today's puzzle (near ${state.chess.summary.rating})`,
    busy: "fetching…",
    needed: (state) => !state.chess.puzzle_id,
    open: () => api.post("/api/chess/new"),
  },
];

const byKey = (key) => DAILIES.find((daily) => daily.key === key);

/**
 * Which cells today is missing, in table order.
 *
 * Pure, and separated from the requests for that reason: this is where the
 * guard against rerolling an open chess puzzle actually lives, and it is
 * checkable without a browser or a lichess round trip.
 */
export function missing(state) {
  return DAILIES.filter((daily) => daily.needed(state)).map((daily) => daily.key);
}

// --- what is in flight -----------------------------------------------------

const OPENING = "opening";

/**
 * `key -> OPENING | Error`, for the day the cache describes.
 *
 * Keyed by date so a rollover starts clean without anyone having to remember
 * to clear it: yesterday's failure is not a failure to report about today, and
 * yesterday's finished gather is not a reason to skip today's.
 */
let progress = { date: null, states: new Map() };

function states() {
  if (progress.date !== store.state.date) {
    progress = { date: store.state.date, states: new Map() };
  }
  return progress.states;
}

/**
 * Ask for whatever today is missing.
 *
 * Returns immediately. Called before the panels mount, so the first paint
 * already shows each cell's busy line rather than a button that is about to
 * answer itself.
 */
export function gatherDailies() {
  const live = states();
  for (const key of missing(store.state)) {
    if (live.get(key) === OPENING) continue;
    start(byKey(key));
  }
}

/** Fire one gather. The state is set synchronously, so a render between here
 *  and the response shows the busy line and not a second pressable button. */
function start(daily) {
  const live = states();
  live.set(daily.key, OPENING);
  run(daily, store.state.date, live);
}

async function run(daily, date, live) {
  let panel;
  try {
    panel = await daily.open();
  } catch (error) {
    if (store.state.date !== date) return;
    live.set(daily.key, error);
    rerender(daily.panel);
    return;
  }
  // Midnight, again. A gather that started yesterday must not drop yesterday's
  // panel into today's cache, nor repaint over a rollover that has already
  // redrawn everything — and nothing is lost by dropping it, because the
  // rollover re-runs the gather against the new day.
  if (store.state.date !== date) return;
  live.delete(daily.key);
  put(daily.key, panel);
  // `put` tells the subscribers, which is how the chips and the elo chart hear
  // about this. It does not redraw the panel that owns the slice — that is the
  // one panel nothing watches — so the redraw is here, and it is the same call
  // the failure path above needs anyway.
  rerender(daily.panel);
}

// --- what the panel draws in the meantime ----------------------------------

/**
 * The control a panel shows while its daily is not here yet.
 *
 * Three states in one node, and which you get says what happened: a line while
 * the fetch is in flight, the error *plus* the button when it failed, and the
 * button alone when nothing has been tried — which is what you are looking at
 * on a day the theorem deck was empty at boot and has since been filled.
 *
 * The error sits under the button rather than replacing it. None of these
 * panels are live, so a body that is only an error message stays one until the
 * page is reloaded — and pressing again is the server's own advice when the
 * model will not stop offering a word you have already had.
 */
export function opener(key) {
  const daily = byKey(key);
  if (!daily) return null;
  const status = states().get(key);
  if (status === OPENING) return el("div.faint", { text: daily.busy });

  const button = el("button.primary", {
    text: typeof daily.label === "function" ? daily.label(store.state) : daily.label,
    onclick: () => {
      start(daily);
      rerender(daily.panel);
    },
  });
  return el("div", null,
    el("div.row", null, button),
    status ? el("div.err", { text: status.message }) : null,
  );
}
