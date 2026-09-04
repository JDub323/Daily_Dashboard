// The date and the hour, which on this client are the whole point.
//
// [Invariant 1] says the browser owns "today" and the server never asks its
// own clock. On the desktop that is a correctness rule about a machine in WSL
// disagreeing with the tab in front of it. Here it is the feature: the phone
// decides what day and what hour an entry happened, writes both into the body,
// and the entry lands there whenever it eventually reaches the server — an
// hour later, or tomorrow morning when the laptop wakes up.
//
// So these two functions are the smallest and the most load-bearing file in
// this directory, and they are a copy of `app/api.js`'s rather than an import
// of it: that module is the desktop's HTTP client, and pulling it in for two
// lines of date formatting would pull `fetch` wrappers and an error class the
// phone has its own opinions about.

export const localISO = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const today = () => localISO(new Date());

/** The current hour, rounded down — what the three quick logs record.
 *
 *  Rounded because that is what can be answered truthfully from memory a
 *  minute later, and because a minutes field is another thing to tap on a
 *  budget of about four. */
export const nowHour = () => new Date().getHours();

/**
 * The date an entry picked at that hour actually belongs to.
 *
 * These five logs record what has already happened, so an hour later than the
 * clock cannot mean today — it means the most recent time it *was* that hour,
 * which is yesterday. The case is the one the picker exists for, arrived at
 * from the other side: noticing at half past midnight that dinner never got
 * logged, scrolling back to 8pm, and having the entry filed at 8pm *tonight* —
 * nineteen hours in the future, and missing from the day it happened on.
 *
 * Only the three quick logs use this. The check-in and the two measurements
 * carry no hour and are always today's.
 */
export const dateForHour = (hour, now = new Date()) => {
  const day = new Date(now);
  if (hour > now.getHours()) day.setDate(day.getDate() - 1);
  return localISO(day);
};

/** `14` -> `2pm`, `0` -> `12am`. */
export const clock = (hour) => {
  const h = ((hour % 24) + 24) % 24;
  const suffix = h < 12 ? "am" : "pm";
  return `${h % 12 === 0 ? 12 : h % 12}${suffix}`;
};

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

export const dayLabel = (d = new Date()) =>
  `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}`;

/** `8:12pm` — a wall-clock time, for the one line on screen that prints one.
 *
 *  `clock` is the hour alone because that is the resolution the logs record at.
 *  This is minutes as well, because the thing it stamps is not a log entry: it
 *  is when the laptop last answered, and "8pm" for something that happened at
 *  8:59 is the wrong side of an hour on the only line that says how stale the
 *  screen is. */
export const timeLabel = (d = new Date()) => {
  const h = d.getHours();
  return `${h % 12 === 0 ? 12 : h % 12}:${String(d.getMinutes()).padStart(2, "0")}${h < 12 ? "am" : "pm"}`;
};
