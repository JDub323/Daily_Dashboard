// The last thing the laptop said, kept on the phone.
//
// The queue holds what the laptop has *not* heard yet. This holds what it told
// us the last time it was reachable, and the two together are the day: a meal
// sent an hour ago is not in the queue any more, and until this file existed it
// was not anywhere else on the phone either. It lived in one `let` in `app.js`,
// so sending a day's log and then walking away from the laptop emptied the
// screen — the entries were safe in the database and unreadable from the only
// device in the room. "What did I already log?" is the question this app is
// opened to answer, and the answer used to require the server that the rest of
// the app is built to do without.
//
// --- this is a cache, and the queue is not -----------------------------------
//
// Worth stating, because the two files sit next to each other and look alike.
// `queue.js` is a *record*: the only copy of a write that exists anywhere, and
// every judgement in it is settled by the fact that losing one loses a meal.
// Nothing here is the only copy of anything. The rows came from the database
// and are still in it. So this file gets the opposite defaults — a failed
// write is shrugged off, an unreadable value is thrown away rather than
// nursed, and a panel that might be wrong is never preferred to one that came
// off the wire.
//
// --- one day, and it is today's ---------------------------------------------
//
// The screen draws today and nothing else, so this keeps today and nothing
// else. The date is stored beside the panel and checked on the way out, which
// is what stops the interesting failure: open the app at 00:30, get yesterday's
// meals under today's heading, and start deleting rows that are not there. A
// panel from another day is not shown, and reading past it is what clears it.
//
// It is dropped rather than migrated across days on purpose. The alternative --
// keeping a week and paging back through it -- is a different screen with a
// day picker on it, and this one has never had a concept of a day other than
// the one you are standing in.

import { isPanel } from "./queue.js";
import { localISO } from "./when.js";

const KEY = "dashboard.m.panel";

/**
 * Keep this panel as the laptop's last word on `date`.
 *
 * Silently a no-op when storage will not take it — quota, or storage switched
 * off. That is the whole difference between this and `queue.js`, which reports
 * the same failure to the screen: there, the write is gone; here, the app
 * carries on with the panel in memory exactly as it did before this file
 * existed, and the only cost is that closing it forgets the day early.
 */
export function remember(panel, date = localISO(new Date())) {
  if (!isPanel(panel)) return false;
  try {
    localStorage.setItem(KEY, JSON.stringify({ date, at: new Date().toISOString(), panel }));
    return true;
  } catch {
    return false;
  }
}

/**
 * What the laptop last said about `date`, or null.
 *
 * `{ panel, at }` — `at` being when it was heard, which the screen prints. It
 * is a wall-clock time on this phone and not a server timestamp, which is the
 * right one of the two: the sentence it produces is "the laptop last answered
 * me at 8:12pm", and the phone is the one who was there.
 *
 * Three things are rejected, all of them by throwing the value away: a panel
 * for another day, a body that is not the dashboard's shape — a captive portal
 * can end up in here the same way it nearly ended up in the queue — and JSON
 * that will not parse. Each of them can only be produced by something already
 * wrong, and none of them is worth keeping over the empty screen that is the
 * app's honest cold start.
 */
export function recall(date = localISO(new Date())) {
  let held;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    held = JSON.parse(raw);
  } catch {
    forget();
    return null;
  }
  if (!held || held.date !== date || !isPanel(held.panel)) {
    forget();
    return null;
  }
  return { panel: held.panel, at: typeof held.at === "string" ? held.at : null };
}

/** Throw the cached day away. Nothing is lost that the database does not have. */
export function forget() {
  try {
    localStorage.removeItem(KEY);
  } catch { /* nothing to do, and nothing at stake — see the head of the file */ }
}
