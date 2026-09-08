// A day drawn to scale: where a block goes, how wide it is, and what colour.
//
// This is the week grid's arithmetic with the week taken out of it. It lived
// in `panels/calendar.js` and was moved here for one reason: the phone's
// schedule screen is the same picture of one day, and `panels/calendar.js`
// imports `api.js`, `dom.js` and `panel.js` — the store, the DOM helpers and
// the panel harness — none of which may appear in the phone's service-worker
// shell. See the head of `m/sw.js`: nothing under `app/` may be imported there
// unless it imports nothing itself.
//
// So this file imports nothing, like `logs.js`, `day.js` and `todolist.js`
// beside it, and for exactly the same reason. `panels/calendar.js` re-exports
// what it used to declare, so the desktop and its tests are unchanged.
//
// What is *not* here is any pixel. `PX_PER_MIN`, the drawn window's policy and
// the height of a two-line block are the client's own: the laptop draws seven
// columns of a clamped twelve hours next to a year heatmap, and the phone
// draws one column of whatever the day actually spans and lets you scroll it.
// Those are different answers to a question this file does not ask. It deals
// in minutes past midnight, and nothing else.

// --- colour -----------------------------------------------------------------

// Twelve hues, none of them gold — gold means "done" everywhere else in this
// app and has one job, so a calendar that happened to sort seventh must not
// look finished. Twelve and no more: these have to be told apart at a glance
// on a 3px border, and a palette fine enough to never collide is a palette
// whose neighbours are the same colour.
export const HUES = [205, 262, 12, 152, 322, 96, 178, 288, 66, 232, 340, 128];

function hash(name) {
  // djb2, xor variant. Any stable string hash does; this one is short and has
  // no surprises for ASCII calendar names.
  let h = 5381;
  for (let i = 0; i < name.length; i++) h = ((h * 33) ^ name.charCodeAt(i)) >>> 0;
  return h >>> 0;
}

/**
 * Assign each name a hue: name -> colour, stable and distinct.
 *
 * Two properties are wanted and they pull against each other. A calendar
 * should keep its colour when *other* calendars come and go — otherwise
 * creating one repaints the whole week and the colour you had learned for
 * Classes now means something else. And no two should share, or the legend
 * stops being able to answer which is which.
 *
 * Assigning by position in the account list gets distinctness and loses
 * stability: a calendar inserted anywhere but the end shifts every colour
 * after it. Assigning by hash alone gets stability and loses distinctness —
 * on this account it collided twice with only nine calendars, because the
 * birthday paradox is unkind at these sizes.
 *
 * The two cannot both hold in general — a colour that never depends on the
 * rest of the set *is* a pure hash — so distinctness wins, because a repeated
 * colour stops the legend answering the one question it exists for. Hash picks
 * a *preferred* hue and ties probe forward, with the names walked in sorted
 * order so the result never depends on what gcalcli happened to list first.
 *
 * What that costs is bounded and measured: on this account's nine calendars,
 * deleting any one moves at most one other, and adding one moves none or two.
 * Both are pinned by tests. Past twelve names a repeat is unavoidable and the
 * earlier ones alphabetically keep theirs.
 *
 * Used for calendars on the week grid and for *subjects* on both todo lists,
 * which is the same problem wearing a different noun — one of a small set of
 * named categories that has to be told apart on a 3px rail.
 */
export function hueMap(names) {
  const taken = new Map();
  const used = new Set();
  for (const name of [...names].sort()) {
    const preferred = hash(name) % HUES.length;
    let hue = HUES[preferred];
    for (let step = 1; used.has(hue) && step < HUES.length; step++) {
      hue = HUES[(preferred + step) % HUES.length];
    }
    used.add(hue);
    taken.set(name, hue);
  }
  return taken;
}

// --- the clock ---------------------------------------------------------------

/** `"09:30"` -> `570`. Minutes past midnight is the unit everything below
 *  works in, because it is the one that subtracts. */
export const minutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

/** `570` -> `9:30am`, `600` -> `10am`. The hour alone when it is on the hour,
 *  because a ruler of `8:00am 9:00am` is a column of zeros. */
export const clock = (mins) => {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
};

// --- laying a day out --------------------------------------------------------

/**
 * Lay overlapping events out side by side, the way gcal does.
 *
 * Without this a 10:30 lecture and a 10:30 lab draw exactly on top of each
 * other and the one underneath is simply invisible — and this account has real
 * collisions, since a course calendar and a personal one both carry the same
 * class.
 *
 * Events are swept in start order into *clusters* of mutual overlap. Within a
 * cluster each event takes the first column whose last event has already
 * finished, which is the usual interval-graph colouring; the cluster's column
 * count then sets everyone's width. Column count is per cluster rather than
 * per day so that one crowded morning does not narrow a quiet afternoon.
 */
export function pack(events) {
  const sorted = [...events].sort((a, b) => a.from - b.from || b.to - a.to);
  const out = [];
  let cluster = [];
  let columns = [];
  let clusterEnd = -1;

  const flush = () => {
    for (const item of cluster) item.of = columns.length;
    out.push(...cluster);
    cluster = [];
    columns = [];
    clusterEnd = -1;
  };

  for (const event of sorted) {
    if (cluster.length && event.from >= clusterEnd) flush();
    let column = columns.findIndex((end) => end <= event.from);
    if (column === -1) {
      column = columns.length;
      columns.push(0);
    }
    columns[column] = event.to;
    clusterEnd = Math.max(clusterEnd, event.to);
    cluster.push({ ...event, column });
  }
  if (cluster.length) flush();
  return out;
}

/**
 * The drawn window, as `[open, close]` in minutes, rounded out to whole hours
 * so a ruler drawn against it carries whole labels.
 *
 * `open` and `close` are the seed and `clamp` decides what happens to it.
 * Clamped, the seed *is* the answer and events outside it are somebody else's
 * problem — see `outsiders`, and the two edge strips the week grid grew for
 * them. Unclamped, the seed widens to swallow every event it was given.
 *
 * Both clients want one of those and neither wants the other's, which is why
 * the policy is an argument rather than a constant in here. The laptop clamps,
 * because a single 11:30pm event was making every day on the home tab sixteen
 * hours tall and pushing the year heatmap under the fold. The phone does not,
 * because it draws one day in a column you scroll — nothing below it is
 * competing for the space, and an event pushed into a strip is an event whose
 * position on the page has stopped meaning anything, which on a screen you
 * skim is the whole of what it was there to say.
 */
export function dayWindow(timed, { open, close, clamp = false } = {}) {
  let from = open;
  let to = close;
  if (!clamp) {
    for (const event of timed) {
      from = Math.min(from, event.from);
      to = Math.max(to, event.to);
    }
  }
  return [Math.floor(from / 60) * 60, Math.ceil(to / 60) * 60];
}

/**
 * Split a day's timed events into the ones the grid can draw and the ones it
 * cannot.
 *
 * `before` and `after` are the two strips above and below the grid. An event
 * that merely *overlaps* an edge — a 7:30 session that runs to 8:30 — stays in
 * the grid and is clipped by it, because it is mostly inside and a clipped
 * block still says roughly when it is. Only an event lying wholly outside
 * moves to a strip, where it keeps its title and its time and gives up only
 * its height.
 *
 * Trivially all-`inside` against a window that was not clamped, which is the
 * honest way for the phone to call it rather than a branch it has to remember.
 */
export function outsiders(timed, open, close) {
  const before = [];
  const after = [];
  const inside = [];
  for (const event of timed) {
    if (event.to <= open) before.push(event);
    else if (event.from >= close) after.push(event);
    else inside.push(event);
  }
  const byStart = (a, b) => a.from - b.from;
  return { before: before.sort(byStart), after: after.sort(byStart), inside };
}

/**
 * A calendar event as the grid wants it: `from` and `to` in minutes.
 *
 * The `to` is where the guessing is. An event with no end, or one that ends
 * before it starts because the span crossed midnight, still has to occupy
 * something — a zero-height block is an event that is not on the screen.
 */
export const timedEvent = (event) => {
  const from = minutes(event.start);
  const to = event.end ? Math.max(minutes(event.end), from + 15) : from + 30;
  return { ...event, from, to };
};
