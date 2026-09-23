// Which screens get opened, counted on the phone.
//
// The database records what is written and nothing about what is read, so a
// screen that only shows things — the agenda, the todo list on a day nothing
// was ticked — looks the same in the data whether it is opened ten times a day
// or never. This is the other half: a count per screen per day, kept here and
// carried to the laptop by the send button, so the next question about what
// earns its place in the bar has an answer.
//
// A *cache*, in `held.js`'s sense, not a record: losing it loses a statistic,
// not a meal. So a refused write is shrugged off, an unreadable value is
// thrown away, and nothing on screen ever says anything about it.
//
// Counts, never events. `{ "2026-09-23": { open: 3, eat: 5 } }` rather than a
// list of timestamps, because the question is "how often", and a count sent
// twice is still the same count — the laptop keeps the larger of what it has
// and what it is sent, so a send that lands twice, or lands after a later one,
// cannot inflate anything. That is also why nothing here is ever cleared on a
// successful send: the phone keeps its last four weeks and sends all of them,
// and the laptop's copy is the one that outlives them.

import { localISO } from "./when.js";

const KEY = "dashboard.m.tally";

/** How many days the phone keeps. The laptop keeps everything it is sent. */
export const KEEP_DAYS = 28;

/** A screen name the laptop will take — `UsageRequest` says the same. */
const NAME = /^[a-z][a-z._-]{0,31}$/;

/** Everything held, `{date: {screen: n}}`, or `{}`. */
export function tallies() {
  try {
    const held = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return held && typeof held === "object" && !Array.isArray(held) ? held : {};
  } catch {
    return {};
  }
}

/** One more opening of `screen` on `date` (today by default). */
export function count(screen, date = localISO(new Date())) {
  if (!NAME.test(screen)) return;
  const all = tallies();
  const day = all[date] && typeof all[date] === "object" ? all[date] : {};
  day[screen] = (Number.isInteger(day[screen]) ? day[screen] : 0) + 1;
  all[date] = day;
  try {
    localStorage.setItem(KEY, JSON.stringify(keep(all, date)));
  } catch { /* a statistic, not a meal — see the head of this file */ }
}

/** The newest `KEEP_DAYS` days, counted back from `today`. */
export function keep(all, today) {
  const floor = new Date(`${today}T12:00:00`);
  floor.setDate(floor.getDate() - (KEEP_DAYS - 1));
  const from = localISO(floor);
  return Object.fromEntries(Object.entries(all).filter(([date]) => date >= from && date <= today));
}
