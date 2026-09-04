// The phone's writes, arriving on the laptop.
//
// Every log in this app is answered on the phone now, and the phone sends when
// you press its button — which is usually not while you are sitting at the
// laptop. So the dashboard's copy of the day is fresh at boot and then quietly
// wrong for the rest of it: the health tab still shows two meals, the ring
// still counts them, and a reminder that lunch is not logged goes on saying so
// for hours after lunch was logged in a kitchen. Reloading the page fixed all
// of it, which is the tell that nothing was broken except that nobody was
// asking.
//
// This asks. One dated GET of the health panel, on a slow beat while the tab is
// being looked at, and immediately whenever it is looked at again.
//
// --- why a poll ---------------------------------------------------------------
//
// The obvious alternative is a push — an SSE stream or a websocket the write
// endpoints broadcast on — and it would be instant rather than up-to-half-a-
// minute late. It is not obviously better here. A held-open connection has to
// survive a laptop that sleeps several times a day, a uvicorn restart on every
// backend edit, and a tab left open for a week; each of those is a reconnect
// path that is only exercised in exactly the situation where you are not
// watching. A GET that either answers or does not has one failure mode and
// recovers from all of them by happening again in thirty seconds.
//
// The latency that actually matters is not the interval anyway. The realistic
// shape of this is: log on the phone in the kitchen, press send, walk to the
// laptop — by which time the tab is being brought back to the front, and the
// listeners below make that the fast path. The interval is the backstop for a
// dashboard that is already on a second screen.
//
// --- why this endpoint --------------------------------------------------------
//
// `/api/health` is the phone's whole surface: all five logs write it and every
// one of those routes answers with this panel. Nothing else the phone can do
// touches any other slice of the boot payload, so re-fetching `/api/state` —
// the maths, the feeds, the ratings, the calendar — would be a page of work to
// find out whether a glass of water was logged.
//
// It is not free: assembling the derived block is a read over four weeks of
// history and costs a few hundred milliseconds of the laptop's own CPU. That is
// the reason for the beat being thirty seconds rather than five, and for the
// hidden-tab check — a dashboard on a monitor nobody is facing polls nothing.

import * as api from "./api.js";
import { put, store } from "./store.js";

/** The backstop beat. See above: the visibility handlers are the fast path, so
 *  this only has to be faster than "you notice it is stale", not instant. */
const EVERY_MS = 30_000;

let timer = null;
let beating = false;

/**
 * One look at the day, adopted only if it differs.
 *
 * The comparison is against what is *on screen* rather than against the last
 * response, so there is one source of truth for "does the dashboard already
 * know this" and no second copy to fall out of step with the store. It is a
 * string compare of two payloads the same serializer produced from the same
 * model, so the key order is stable and a difference is a real difference.
 *
 * Adopting means `put`, which notifies — and that is deliberately the whole of
 * the wiring. The subscriber in `main.js` already redraws the panels that watch
 * the health slice, rewrites the completion row and re-runs the reminders; a
 * write that arrived from the phone should do exactly what a write made here
 * does, and the way to guarantee that is to make it the same event.
 */
export async function beat() {
  if (beating || document.hidden || !store.state) return;
  // Between midnight and the rollover the store still describes yesterday, and
  // the panel this would fetch is today's. Dropping one into the other would
  // put an empty day under yesterday's date and hand the completion write a
  // count that belongs to neither. The rollover reloads everything a minute
  // later; there is nothing here worth racing it for.
  if (store.state.date !== api.today()) return;
  beating = true;
  try {
    const panel = await api.get("/api/health", { date: api.today() });
    if (JSON.stringify(panel) !== JSON.stringify(store.state.health)) put("health", panel);
  } catch {
    // The laptop asking its own server and being refused means the server is
    // down, and the panels say so on their own next draw. A reminder about it
    // every thirty seconds would be the same news repeated by something whose
    // entire job is to be quiet.
  } finally {
    beating = false;
  }
}

export function initPulse() {
  clearInterval(timer);
  timer = setInterval(beat, EVERY_MS);
  // Both events, and they are not the same one. `visibilitychange` fires for a
  // tab switch or a minimised window; `focus` fires for alt-tab between two
  // windows, where the tab never stopped being visible and the first event
  // never comes. The second is the common case for this app — the dashboard is
  // a window you turn back to, not a tab you re-open — and it was the one that
  // would have been missed.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) beat();
  });
  window.addEventListener("focus", beat);
}
