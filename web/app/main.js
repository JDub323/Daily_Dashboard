// Boot.
//
// One HTTP round trip fills the whole dashboard: GET /api/state?date=<local>
// returns a projection assembled server-side. The panels below read from that
// snapshot and never compute a rating, a score, or a streak themselves.
//
// The snapshot is a read, though, and some of the day's cells do not exist
// until something is written — a puzzle fetched, a card drawn, a word paid
// for. Those are `daily.js`, kicked off here beside the state load and landing
// in the same store a moment later.

import * as api from "./api.js";
import { renderChips } from "./chips.js";
import { gatherDailies } from "./daily.js";
import { $ } from "./dom.js";
import { initKeys } from "./keys.js";
import { invalidateHistory } from "./modals/daylogs.js";
import { invalidateTrickArchive } from "./modals/tricks.js";
import { initNav, resetToTab } from "./nav.js";
import { mountAll, refreshLive, register, rerender, rerenderAll, resetCollapse } from "./panel.js";
import bathroom from "./panels/bathroom.js";
import calendar from "./panels/calendar.js";
import checkin from "./panels/checkin.js";
import chess from "./panels/chess.js";
import daylog from "./panels/daylog.js";
import debate from "./panels/debate.js";
import debateStats, { invalidateDebateHistory } from "./panels/debatestats.js";
import deck from "./panels/deck.js";
import games from "./panels/games.js";
import health from "./panels/health.js";
import markets from "./panels/markets.js";
import meals from "./panels/meals.js";
import math from "./panels/math.js";
import metrics from "./panels/metrics.js";
import { finance, politics } from "./panels/news.js";
import patterns from "./panels/patterns.js";
import piano from "./panels/piano.js";
import portfolio from "./panels/portfolio.js";
import reading from "./panels/reading.js";
import plan from "./panels/plan.js";
import session from "./panels/session.js";
import spelling from "./panels/spelling.js";
import { activity } from "./panels/stats.js";
import theorem from "./panels/theorem.js";
import tricks from "./panels/tricks.js";
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
 * It shrank under tabs — the spend strip moved off the trophies panel to the
 * home tab, taking `metrics`' dependency on `math` with it — and again when the
 * rating charts came out, which took the two `elo-*` entries with them.
 */
const WATCHES = {
  metrics: ["games"],
  // Generating a problem is the one action in the app that spends real money,
  // and the spend strip lives in this panel.
  activity: ["math"],
  // A scored ballot changes every line on the trends panel, and it is the one
  // panel on the tab that does not read from the boot payload.
  "debate-stats": ["debate"],
  // …and it names the patterns the argument deck offers to add, so that panel
  // has to redraw when a ballot lands too.
  patterns: ["debate"],
  // The trick panel is the one that watches its *own* slice. The work happens
  // in a modal, so the write that changes the panel's progress line comes from
  // outside it — without this the sheet says 6/8 and the card behind it still
  // says the set is untouched.
  tricks: ["tricks"],
  // All three health panels read the one `health` slice, and every write to it
  // is a write to the whole thing: logging a session moves this week's load,
  // the ratio and the monotony on the hero, and logging a weight moves the
  // chart the hero draws. So each of the three watches the slice rather than
  // re-rendering itself after its own POST — otherwise the two panels that did
  // not do the writing sit there stale.
  health: ["health"],
  checkin: ["health"],
  session: ["health"],
  plan: ["health"],
  // The two quick logs read the same slice and write to it four to eight times
  // a day. They watch it rather than redrawing themselves after their own POST
  // for the same reason as the three above — and because the hero's hydration
  // and energy-balance lines are computed off exactly these rows, so a meal
  // logged here has to move a sentence up there in the same breath.
  bathroom: ["health"],
  meals: ["health"],
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
  // home — the year, and then the two cards about the day as a whole. The
  // checklist that used to head this tab is gone: the chips said the same thing
  // the statusline's ring says on every tab, and the heatmap under them was
  // already the better answer to "did I keep this up". `renderChips` still runs
  // on every store change — it owns the completion write and the statusline —
  // it simply has no panel to draw into now.
  // The week leads, and is the tab's hero: it is the only panel here that says
  // what is *about* to happen, where everything else counts something that
  // already has.
  calendar, activity, daylog,
  // math — the problem is the hero; the trick and the deck are the two things
  // that make the next one easier.
  math, tricks, theorem, deck,
  // politics
  debate, patterns, debateStats, politics,
  // dailies — no hero, in pairs: the two vocabulary cells, the two games cells,
  // the two hobbies, and then the day's article across the bottom.
  word, spelling, games, metrics, chess, piano, reading,
  // markets — the portfolio is the hero; the movements list is the frame it
  // is read against.
  portfolio, markets, finance,
  // health — the readout is the hero, and the things you type sit under it in
  // the order the day asks for them: the check-in in the morning, then what the
  // plan asked for, then the freehand log for anything it did not ask for.
  // `plan` sits above `session` because on most evenings it is the whole job —
  // `session` is the escape hatch for a session nobody prescribed.
  //
  // The two quick logs come last, and being last is not being least: they are
  // the two panels you will almost never be *looking* at when you use them.
  // `b` and `m` reach them from any tab in one keystroke, so their position in
  // the column is only about reading the day back afterwards — for which the
  // bottom of the page, under the training you are actually here for, is
  // exactly right.
  health, checkin, plan, session, meals, bathroom,
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
  // Before the redraw, as at boot: the new day's cells go straight to their
  // busy line rather than showing a button for the second it takes the answer
  // to arrive. This is also the retry for a gather that failed yesterday.
  gatherDailies();
  invalidateHistory();
  invalidateDebateHistory();
  invalidateTrickArchive();
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
  initNav();
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

  // Before the panels mount, so the first paint of each x-of-the-day cell is
  // already its busy line. Deliberately not awaited: the word is a generation
  // of a few seconds and the puzzle is a round trip to lichess, and neither is
  // a reason for the rest of the dashboard to sit blank.
  gatherDailies();

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
    // The archive caches past sets for the session, and today's row is one of
    // them the moment the set is marked.
    if (keys.includes("tricks")) invalidateTrickArchive();
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
