// The client-side cache of the server's read model.
//
// This is a *cache*, not the source of truth. The old front end held the only
// live copy of state.json in a global and posted whole subtrees back, so a
// stale tab could overwrite the world. Here every write returns the panel it
// affected and we drop that in; nothing is ever pushed back wholesale.

import * as api from "./api.js";

export const store = {
  config: { topic: "?", tickers: [] },
  state: null,
  topic: null,
};

/**
 * Is the dashboard paused? `paused` in config.toml: the home tab alone, nothing
 * gathered, nothing counted, no reminders but the todo list's. False until the
 * config has loaded, which is also what a config that failed to load gets — a
 * dashboard that could not be told it is paused shows everything.
 */
export const paused = () => Boolean(store.config.paused);

const listeners = new Set();

/** Panels that must re-render when a *different* panel changes shared data. */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify(...keys) {
  for (const fn of listeners) fn(keys);
}

export async function loadState() {
  store.state = await api.get("/api/state", { date: api.today() });
  return store.state;
}

/**
 * Replace one panel's slice from a write response, then tell subscribers.
 *
 * `notify` is what keeps the elo chart and the chips honest after a math or
 * chess result without re-fetching the whole payload.
 */
export function put(key, value) {
  store.state[key] = value;
  notify(key);
  return value;
}

// --- the day the cache describes -------------------------------------------

/**
 * Has the local date moved past the day `store.state` was fetched for?
 *
 * `state.date` is the date the server was *asked* about, echoed back in the
 * payload. It shipped from the beginning and nothing ever read it, so a tab
 * left open past midnight went on serving yesterday's snapshot while every
 * write it triggered carried today's date.
 */
export function dayHasTurned() {
  return store.state !== null && store.state.date !== api.today();
}

/**
 * The completion row `state` implies, or null when the stored row already says
 * it — the caller writes only on a real change.
 *
 * The date is taken from the state the counts were read off, never from the
 * clock. They are the same date except in the window between midnight and the
 * rollover check, and there the clock is the wrong answer: it stamps
 * yesterday's finished count onto a day nothing has been done on, and
 * `perfect_days` (done = total) then counts that day as perfect. The streak
 * grew on its own overnight.
 */
export function completionWrite(state, done, total, bonus = 0) {
  const date = state.date;
  const stored = state.completion.find((row) => row.date === date);
  // `bonus` is compared like the other two, and it has to be: it moves on days
  // the required count does not — finishing a Wordle after a perfect 4/4 is
  // the ordinary case — and comparing done/total alone would drop that write
  // on the floor. Rows written before the column existed report 0, so the
  // first optional task of such a day writes once and then settles.
  if (stored && stored.done === done && stored.total === total
      && (stored.bonus ?? 0) === bonus) {
    return null;
  }
  return { date, done, total, bonus };
}
