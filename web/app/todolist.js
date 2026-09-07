// Reading a todo list: the grouping, the day headings, and the two ways a
// title gets shortened. No DOM, no fetching, no store.
//
// Lifted out of `panels/todo.js` when the phone grew its own todo screen, on
// the same argument as `day.js`: the rules for *where a row goes* are as much
// the definition of the list as the rows are, and two clients that group
// differently produce two different backlogs off one table. The desktop panel
// and `web/m/app.js` both read this.
//
// Imports nothing, and has to keep importing nothing — the phone's whole module
// graph lives in `web/m/sw.js`'s `SHELL` so the app opens with the laptop off,
// and an ES module graph fails whole. `tests/js/shell.test.js` enforces it.

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

export function parseISO(iso) {
  // Not `new Date(iso)`: that reads a bare date as UTC and renders it local,
  // which moves every due date back a day west of Greenwich.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export const dayLabel = (iso, todayISO) => {
  if (iso === todayISO) return "today";
  const date = parseISO(iso);
  const days = Math.round((date - parseISO(todayISO)) / 86400000);
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  const stamp = `${DAY_NAMES[date.getDay()]} ${MONTHS[date.getMonth()]} ${date.getDate()}`;
  // The year, but only when it is not this one. Two items on this account are
  // dated 2027 — "aug 25" typed on the 28th rolls forward, which is `calparse`
  // doing the right thing with an ambiguous input — and with no year on the
  // heading they read as *last* week rather than next year, sitting at the
  // bottom of a list sorted by date looking like a sorting bug.
  const year = date.getFullYear() === parseISO(todayISO).getFullYear()
    ? "" : ` ${date.getFullYear()}`;
  // Inside the coming week the weekday is the useful half, past that it is the
  // date — "Thu" a month out tells you nothing you can act on.
  return days > 1 && days < 7 ? `${DAY_NAMES[date.getDay()]}` : stamp + year;
};

/**
 * The subject's short name — what you typed, not what it expanded to.
 *
 * The panel is sent the alias table with the items (`TodoPanel.aliases`), so
 * this is a lookup rather than a guess. An item whose subject has since been
 * renamed or removed from config keeps its full name, which is the honest
 * answer: there is no key for it any more.
 */
export const keyFor = (aliases) => {
  const byName = new Map(aliases.map((a) => [a.name, a.key]));
  return (subject) => (subject ? (byName.get(subject) ?? subject) : null);
};

/**
 * The title with its own subject taken off the front.
 *
 * `fpm Thornhill Chapter 3` expands to `US National Security Policy Making
 * Thornhill Chapter 3` before it is ever stored, so the subject is the first
 * words of almost every title in the list — and the row then said it twice,
 * once in the title and once in the chip beside it. Stripped here rather than
 * at write time because the stored title is what is on the Google event, and
 * the event has to stand on its own in a calendar that knows nothing about
 * aliases.
 *
 * Only ever a prefix, and only when something is left over: a title that *is*
 * its subject keeps it, because an empty row is worse than a repeated one.
 */
export function shorten(title, subject) {
  if (!subject || !title.toLowerCase().startsWith(subject.toLowerCase())) return title;
  const rest = title.slice(subject.length).replace(/^[\s:—–-]+/, "");
  if (!rest) return title;
  // Recapitalised: what follows the subject is mid-sentence in the stored
  // title ("...Making: quiz on the Civil War") and is the start of the line
  // once the subject is gone.
  return rest[0].toUpperCase() + rest.slice(1);
}

export const clock = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
};

/**
 * Group into days, overdue first and then forward in time.
 *
 * Everything before today collapses into one "overdue" bucket rather than
 * keeping its own date heading. Six headings each holding one late item is a
 * panel that looks like a backlog of dates; one heading holding six is a
 * backlog of work, which is the thing that has to be dealt with.
 *
 * Undated items are not here at all. They are a second list under a rule —
 * `undatedItems` below — because a grouping by day has no honest place to put
 * something with no day, and the alternative was a `null` key sorting wherever
 * JavaScript felt like putting it. Skipped explicitly rather than by accident:
 * `null < "2026-08-28"` is false, so an undated row would otherwise have
 * become its own day group headed by the word "null".
 */
export function groupByDay(items, todayISO) {
  const overdue = [];
  const days = new Map();
  for (const item of items) {
    if (item.done || !item.due_date) continue;
    if (item.due_date < todayISO) {
      overdue.push(item);
      continue;
    }
    if (!days.has(item.due_date)) days.set(item.due_date, []);
    days.get(item.due_date).push(item);
  }
  const groups = [...days.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, list]) => ({ date, items: list, overdue: false }));
  return overdue.length ? [{ date: null, items: overdue, overdue: true }, ...groups] : groups;
}

/**
 * The other list: work that is owed and not on a day.
 *
 * Oldest first, which is the only ordering available and also the right one —
 * these have no due date to sort by, so what is left is how long you have been
 * not doing them.
 */
export function undatedItems(items) {
  return items.filter((i) => !i.done && !i.due_date);
}
