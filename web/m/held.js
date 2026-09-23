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
// --- two slots, and they do not have the same rule about days ----------------
//
// The health panel — the five logs — keeps today and nothing else, and reading
// past midnight is what clears it. That is not caution, it is the fix for a
// specific failure: open the app at 00:30, get yesterday's meals under today's
// heading, and start deleting rows that are not there. Every row on that screen
// has a delete button beside it.
//
// The day panel — the todo list, what today asks for, the schedule — is handed
// back *with the day it describes* and is allowed to be yesterday's. That is
// the whole point of it. The laptop is asleep most of the day, so a phone
// opened on Tuesday morning may not have spoken to it since Monday evening, and
// a schedule that went blank at midnight would be blank exactly when it was
// wanted. `GET /api/day` sends tomorrow's events alongside today's for the same
// reason, and `app.js` is what decides which half of a stale panel is still
// true — see `promote` there. This file's job is only to be honest about when
// the copy was taken.
//
// Neither is migrated across more than that. Keeping a week and paging back
// through it is a different screen with a day picker on it, and this app has
// never had a concept of a day other than the one you are standing in.

import { isDay, isPanel } from "./queue.js";
import { localISO } from "./when.js";

const KEY = "dashboard.m.panel";
const DAY_KEY = "dashboard.m.day";

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

/** Keep this `DayPanel` as the laptop's last word on the day it names.
 *
 *  The date comes off the panel rather than off this phone's clock, which is
 *  the one difference that matters: what is being written down is which day the
 *  laptop was answering about, and `recallDay` has to be able to tell that
 *  apart from when the answer arrived. */
export function rememberDay(day) {
  if (!isDay(day)) return false;
  try {
    localStorage.setItem(DAY_KEY, JSON.stringify({ at: new Date().toISOString(), day }));
    return true;
  } catch {
    return false;
  }
}

/**
 * The last `DayPanel` heard, whatever day it was about, or null.
 *
 * `{ day, at }`. Unlike `recall` this does not check the date — see the head of
 * the file. What it still checks is the shape, for the same reason: a hotel
 * portal's JSON is a thing that can end up in storage, and `day.date` is what
 * every decision above this is made from.
 */
export function recallDay() {
  let held;
  try {
    const raw = localStorage.getItem(DAY_KEY);
    if (!raw) return null;
    held = JSON.parse(raw);
  } catch {
    forgetDay();
    return null;
  }
  if (!held || !isDay(held.day)) {
    forgetDay();
    return null;
  }
  return { day: held.day, at: typeof held.at === "string" ? held.at : null };
}

/** Throw the cached day away. Nothing is lost that the database does not have. */
export function forget() {
  try {
    localStorage.removeItem(KEY);
  } catch { /* nothing to do, and nothing at stake — see the head of the file */ }
}

export function forgetDay() {
  try {
    localStorage.removeItem(DAY_KEY);
  } catch { /* as above */ }
}

// --- a third slot, with no rule about days at all ----------------------------
//
// The meal menu is not about a day. It is the list of what he eats, and the
// morning it is needed most — breakfast, laptop asleep since last night — is
// exactly the morning the health panel above has been thrown away. So it is
// kept apart, taken off every panel that carries one, and kept until a newer
// one replaces it. A week-old menu is a slightly stale menu; no menu is a meal
// log with nothing to tap.

const MENU_KEY = "dashboard.m.menu";

/** Every entry an object with a slug and a label — enough to draw a cap and to
 *  post it. Anything else is thrown away rather than drawn. */
const isMenu = (menu) =>
  Array.isArray(menu)
  && menu.every((item) => item && typeof item.slug === "string" && typeof item.label === "string");

/** Keep the laptop's last `meal_menu`. A no-op on a failed write, as above. */
export function rememberMenu(menu) {
  if (!isMenu(menu)) return false;
  try {
    localStorage.setItem(MENU_KEY, JSON.stringify(menu));
    return true;
  } catch {
    return false;
  }
}

/** The last menu heard, however old, or an empty one. */
export function recallMenu() {
  try {
    const held = JSON.parse(localStorage.getItem(MENU_KEY) ?? "null");
    if (isMenu(held)) return held;
  } catch { /* unreadable is the same as absent */ }
  return [];
}

/**
 * What is left of a cached day once the day has turned over.
 *
 * The rule from the head of this file, in one function, because it is the one
 * piece of this app that cannot be exercised by looking at it: reproducing it by
 * hand means a phone that has not spoken to the laptop since yesterday, and the
 * shell it would need to open in that state is a service worker, which does not
 * register in a headless browser. So it is a pure function over a date and it is
 * tested rather than tried.
 *
 * Returns `{ stale, events, owed }`:
 *
 *   `stale`  the panel describes a day that is over.
 *   `events` the rows still ahead of you. Compared against *today*, so
 *            yesterday's drop out on their own and what the route sent as
 *            tomorrow's becomes today's without being rewritten. An all-day
 *            event spanning the boundary is kept by its `end_date`, which is
 *            Google's half-open convention — a one-day holiday arrives as
 *            09-07 → 09-08.
 *   `owed`   whether "what today asks for" can still be shown. False on a stale
 *            panel: yesterday's "3 of 5 done" under today's heading is not old,
 *            it is wrong, and the day it describes cannot be finished any more.
 *
 * The todo list is not in here because nothing happens to it. An overdue reading
 * is still overdue in the morning, the one gesture on it is addressed by id, and
 * re-grouping is `groupByDay`'s job — it is given today's date rather than the
 * panel's and sorts itself out.
 */
export function stillTrue(day, todayISO = localISO(new Date())) {
  if (!day) return { stale: false, events: [], owed: false };
  return {
    stale: day.date !== todayISO,
    events: (day.events ?? []).filter(
      (e) => e.date >= todayISO || (e.end_date ?? "") > todayISO,
    ),
    owed: day.date === todayISO,
  };
}
