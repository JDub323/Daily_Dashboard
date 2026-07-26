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
