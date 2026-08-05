// Boot.
//
// One HTTP round trip fills the whole dashboard: GET /api/state?date=<local>
// returns a projection assembled server-side. The panels below read from that
// snapshot and never compute a rating, a score, or a streak themselves.

import * as api from "./api.js";
import { renderChips, todayPanel } from "./chips.js";
import { $ } from "./dom.js";
import { initKeys } from "./keys.js";
import { resetToTab } from "./nav.js";
import { mountAll, refreshLive, register, rerender, rerenderAll, resetCollapse } from "./panel.js";
import calendar from "./panels/calendar.js";
import chess from "./panels/chess.js";
import daylog from "./panels/daylog.js";
import debate from "./panels/debate.js";
import debateStats, { invalidateDebateHistory } from "./panels/debatestats.js";
import deck from "./panels/deck.js";
import { chessElo, mathElo } from "./panels/elo.js";
import games from "./panels/games.js";
import health from "./panels/health.js";
import markets from "./panels/markets.js";
import math from "./panels/math.js";
import metrics from "./panels/metrics.js";
import { finance, misc, politics } from "./panels/news.js";
import piano from "./panels/piano.js";
import reading from "./panels/reading.js";
import { activity, archive, areas, invalidateHistory } from "./panels/stats.js";
import theorem from "./panels/theorem.js";
import word from "./panels/word.js";
import { dayHasTurned, loadState, store, subscribe } from "./store.js";
import { initTabs, onTabChange } from "./tabs.js";
import { initModal, onModalClose, toast } from "./ui.js";

/**
 * Which panels care about which slice of state.
 *
 * This is the whole cross-panel coupling. Panels used to call
 * `rerender("elo")` and `updateChips()` directly after a write, so finishing a
 * puzzle meant knowing which other panels existed.
 *
 * It shrank under tabs: the elo charts split into one per track, so each now
 * watches only its own, and the spend strip moved off the trophies panel to
 * the home tab, taking `metrics`' dependency on `math` with it.
 */
const WATCHES = {
  "elo-math": ["math"],
  "elo-chess": ["chess"],
  metrics: ["games"],
  // Generating a problem is the one action in the app that spends real money,
  // and the spend strip lives in this panel.
  activity: ["math"],
  // A scored ballot changes every line on the trends panel, and it is the one
  // panel on the tab that does not read from the boot payload.
  "debate-stats": ["debate"],
};

/** Store keys whose writes make the cached /api/history fetch stale. */
const HISTORY_KEYS = ["math", "chess", "daylog"];

/**
 * The panel set, in the order each tab lays them out.
 *
 * Registration order is mount order within a slot, so this list *is* the
 * layout — there is no separate ordering table to disagree with it.
 */
const PANELS = [
  // home — the checklist, then the year, then everything backward-looking
  todayPanel, activity, word, calendar, daylog, areas, archive,
  // math
  math, theorem, deck, mathElo,
  // politics
  debate, debateStats, politics,
  // newsletters
  reading, misc,
  // games
  games, metrics,
  // markets
  markets, finance,
  // hobbies
  chess, piano, chessElo,
  // health
  health,
];

async function loadConfig() {
  // Dated: the topic rotates weekly, and the browser decides which week it is.
  store.config = await api.get("/api/config", { date: api.today() });
  $("#hdr-date").textContent = new Date().toDateString();
  $("#hdr-topic").textContent = `topic: ${store.config.topic}`;
}

/**
 * Topic links, in the background.
 *
 * Only needed once the math panel has a problem open, so this never blocks
 * first paint — and a failure leaves the panel's "loading…" line rather than
 * taking the boot down with it.
 */
function loadTopic() {
  return api
    .get("/api/topic", { date: api.today() })
    .then((topic) => {
      store.topic = topic;
      rerender("math");
    })
    .catch(() => {});
}

// --- the day rollover ------------------------------------------------------

const DAY_CHECK_MS = 60_000;

let rolling = null;

/**
 * Reload everything when the local date moves on.
 *
 * A dashboard is a tab you leave open, so midnight arrives with the page
 * already mounted. Without this the boot snapshot simply stayed: panels went
 * on showing yesterday's problem and yesterday's chips while every write they
 * triggered was stamped with today's date.
 *
 * The completion write was the one that did damage. It POSTed yesterday's
 * finished count against today's date, and `perfect_days` counts any row where
 * `done = total` — so a day nothing had been done on was recorded as perfect
 * and the 100%-days streak grew overnight on its own. `completionWrite` now
 * addresses that write to the day it describes, which makes the corruption
 * unreachable; this makes the *display* correct as well, so the fix is not
 * "the numbers are right but the screen disagrees".
 *
 * Config is reloaded with the rest because the topic rotates on Sunday: a
 * Saturday-night tab has the wrong one by morning. Reloading state also runs
 * the server's lapse reconciliation, so an abandoned problem is closed out at
 * midnight rather than waiting for the next time the tab is opened.
 */
async function rollOver() {
  await loadConfig();
  await loadState();
  invalidateHistory();
  invalidateDebateHistory();
  resetCollapse();
  rerenderAll();
  await renderChips();
  await loadTopic();
}

/** Start a rollover if the day turned. True if one is now in flight. */
function checkDay() {
  if (!dayHasTurned()) return false;
  // Serialised: the interval and the visibility handler both fire when a
  // laptop wakes up after midnight, and two concurrent reloads would race to
  // write the same completion row.
  rolling ??= rollOver()
    .catch((error) => toast(`could not load the new day: ${error.message}`))
    .finally(() => {
      rolling = null;
    });
  return true;
}

function watchForNewDay() {
  // Both, deliberately. The interval covers a tab sitting visible through
  // midnight; visibilitychange covers the suspended laptop, whose timers do
  // not fire while it is asleep and which would otherwise stay stale until the
  // minute after it woke.
  setInterval(checkDay, DAY_CHECK_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    // The day check wins: a rollover redraws every panel anyway, so refreshing
    // the live ones as well would fetch each of them twice.
    if (checkDay()) return;
    // Live panels stop polling while hidden. Coming back is the moment their
    // data is furthest out of date and the moment someone is looking at it.
    refreshLive();
  });
}

// --- boot ------------------------------------------------------------------

async function boot() {
  initTabs();
  initModal();
  onModalClose(renderChips);
  // The focus ring belongs to whichever section is showing; a switch parks it
  // on that tab's first panel rather than leaving it on a card you can no
  // longer see.
  onTabChange(resetToTab);

  try {
    await loadConfig();
  } catch {
    toast("could not load config — is the server running?");
  }

  await loadState();

  register(...PANELS);
  mountAll();
  resetToTab();

  subscribe((keys) => {
    // A write is the other moment worth checking the date on. Finishing a
    // puzzle just after midnight would otherwise be counted into yesterday's
    // chips for as long as the interval had left to run — harmless for today,
    // but it would rewrite yesterday's finished row with a mixed count. The
    // rollover re-renders everything itself, so there is nothing to do after.
    if (checkDay()) return;
    renderChips();
    if (keys.some((key) => HISTORY_KEYS.includes(key))) invalidateHistory();
    // Dropped before the WATCHES redraw below, so the trends panel re-fetches
    // rather than redrawing the numbers it already had.
    if (keys.includes("debate")) invalidateDebateHistory();
    for (const [id, watched] of Object.entries(WATCHES)) {
      if (keys.some((key) => watched.includes(key))) rerender(id);
    }
  });
  await renderChips();
  watchForNewDay();
  initKeys();

  loadTopic();
}

boot().catch((error) => {
  toast(`boot failed: ${error.message}`);
});
