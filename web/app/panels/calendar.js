// The week.
//
// Was a small card holding two flat lists — "today" and "this week", the
// second of which contained the first, because the server asked gcalcli for
// today and then for today-plus-seven. Neither list said when anything was,
// only what order it came in, and nothing on it said which calendar an event
// belonged to because the API had never been asked for one.
//
// It is now the hero of the home tab, drawn the way a week is drawn: seven
// columns, an hour ruler down the side, and a block whose height is how long
// the thing takes. That last part is the whole argument for the rewrite — a
// list can tell you that you have four things on Wednesday, but only a grid
// tells you they are stacked between 9 and noon and that the afternoon is
// free.
//
// Two pieces here are less obvious than they look, and both are noted where
// they happen: the window is computed from the day's own events rather than
// running 00:00–24:00, and colliding events are packed into sub-columns.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { feedStamp, refreshAfter } from "../freshness.js";
import { openCalendarHelp } from "../modals/calhelp.js";
import { rerender } from "../panel.js";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

// --- how much of the day is drawn to scale ----------------------------------
//
// These four numbers are the whole of the calendar's height, and they are
// grouped here so the decision is one place and reversible in one edit. What
// they replaced was a window that *grew*: it started at 8am–9pm and then
// widened to cover whatever the week actually contained, so a single 11:30pm
// event made every day on screen 16 hours tall and pushed the rest of the home
// tab under the fold. The page got longer the busier the week was, which is
// exactly backwards.
//
// The window is now fixed. Events outside it are not dropped — see
// `outsiders` below and the two strips the grid grew for them — they simply
// stop being drawn to scale, which is the right trade for a 7am gym session
// against thirteen hours of empty grid.
//
// To go back to the old behaviour: set CLAMP to false. `windowFor` then
// widens as it used to and both strips come up empty, because nothing falls
// outside a window that has stretched to fit.
const CLAMP = true;
const DEFAULT_OPEN = 8 * 60;
const DEFAULT_CLOSE = 20 * 60;

// The grid's height is this times the drawn window. At 0.9 the old
// 8am–9pm week was 700px before the legend and the box under it; at 0.42 a
// clamped twelve hours is ~300px, which is an hour to a line and a bit of
// text — readable as a week, and small enough that the year-so-far heatmap
// below it is on the same screen.
const PX_PER_MIN = 0.42;

// The height at which an event has room for a second line. Derived from the
// scale rather than fixed in minutes, because the question is how many pixels
// the block has and a minutes threshold silently means something different
// every time PX_PER_MIN moves.
const TWO_LINE_PX = 34;

// Twelve hues, none of them gold — gold means "done" everywhere else in this
// app and has one job, so a calendar that happened to sort seventh must not
// look finished. Twelve and no more: these have to be told apart at a glance
// on a 3px border, and a palette fine enough to never collide is a palette
// whose neighbours are the same colour.
const HUES = [205, 262, 12, 152, 322, 96, 178, 288, 66, 232, 340, 128];

function hash(name) {
  // djb2, xor variant. Any stable string hash does; this one is short and has
  // no surprises for ASCII calendar names.
  let h = 5381;
  for (let i = 0; i < name.length; i++) h = ((h * 33) ^ name.charCodeAt(i)) >>> 0;
  return h >>> 0;
}

/**
 * Assign each calendar a hue: name -> colour, stable and distinct.
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
 * a *preferred* hue and ties probe forward, with the calendars walked in name
 * order so the result never depends on what gcalcli happened to list first.
 *
 * What that costs is bounded and measured: on this account's nine calendars,
 * deleting any one moves at most one other, and adding one moves none or two.
 * Both are pinned by tests. Past twelve calendars a repeat is unavoidable and
 * the earlier names alphabetically keep theirs.
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

const minutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

const clock = (mins) => {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
};

const isoOf = (date) => api.localISO(date);

function parseISO(iso) {
  // Not `new Date(iso)`: that parses a bare date as UTC and then renders it in
  // local time, which moves every event back a day west of Greenwich.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

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
 * The drawn window.
 *
 * Fixed at the waking hours while `CLAMP` holds, and widened to fit the day's
 * own events when it does not. Exported and pure because which events fall
 * outside it is the other half of this decision, and both halves have to
 * agree about where the edges are.
 */
export function windowFor(timed) {
  let open = DEFAULT_OPEN;
  let close = DEFAULT_CLOSE;
  if (!CLAMP) {
    for (const event of timed) {
      open = Math.min(open, event.from);
      close = Math.max(close, event.to);
    }
  }
  // Rounded out to the hour so the ruler carries whole labels.
  return [Math.floor(open / 60) * 60, Math.ceil(close / 60) * 60];
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

export default {
  id: "cal",
  // No heading. `.cal-head` below already names the week on screen, and a 21px
  // "week" above it was a second title for the same thing. See panel.js.
  title: "",
  tab: "home",
  slot: "hero",
  hero: true,
  live: true,

  // Which week is on screen, as an offset from the one containing today. Held
  // on the panel rather than in the store because nothing else in the app has
  // an opinion about it, and a redraw from the live timer must not silently
  // put you back on this week while you are reading next week's.
  offset: 0,

  // --- the weeks already fetched, by offset ---------------------------------
  //
  // The server has cached a week all along (`Feeds.cached("calendar:<start>")`)
  // and that cache is real — but it expires in five minutes and it sits on the
  // far side of an HTTP round trip that ends in a `gcalcli agenda` subprocess,
  // through WSL here. So paging back to the week you were reading ninety
  // seconds ago paid for it again, and the arrows felt like loading rather
  // than like turning a page.
  //
  // These weeks are held on the client too. A hit paints synchronously and
  // *then* asks the server whether it was still right; a miss is the old
  // behaviour exactly. Nothing is ever drawn that was not fetched — this is a
  // cache of answers, never a guess at one.
  weeks: new Map(),

  // One request per offset, however many callers want it. `prefetch` and a
  // keypress on `›` reach for the same week constantly, and without this they
  // would be two subprocesses for one answer.
  inflight: new Map(),

  // The date the map is keyed against. `offset` counts weeks from *today*, so
  // every key in it means a different week after midnight — see `expire`.
  anchor: null,

  // Bumped by `forget`. A fetch in flight when the calendar is written to
  // would otherwise resolve afterwards and put the week as it was *before* the
  // write back into the map.
  epoch: 0,

  // Whether the first, wider warm-up has happened. See `prefetch`.
  warmed: false,

  // The picker's selection, "" meaning auto. Held on the panel rather than in
  // the box because every add re-renders the panel and so rebuilds the box:
  // filing three things onto Homework in a row should not mean choosing it
  // three times.
  target: "",

  async render(node) {
    // The add box outlives the render, and has to. Building a fresh one each
    // time threw away whatever was half-typed the moment the five-minute live
    // refresh landed — and took the picker's selection with it.
    this.box ??= quickAdd(this, node);
    const box = this.box;
    const focus = box.focusState();
    this.expire();

    const held = this.weeks.get(this.offset);
    if (held) {
      // The whole point of the map: a week you have already looked at costs a
      // paint rather than a subprocess. Then ask the server whether it was
      // still right — see `revalidate`, which redraws only if it was not.
      this.paint(node, held, focus);
      this.revalidate(node, this.offset, held);
      this.prefetch();
      return refreshAfter(held.feed);
    }

    // Shown on the first paint only. Replacing the week with a one-line
    // placeholder on *every* render collapsed the card to a sliver and sprang
    // it back — on every add, every page of the arrows, and every five-minute
    // refresh. The week already on screen is a better thing to look at while
    // the next one loads than the word "loading", and the swap below is one
    // synchronous replace, so nothing flickers between them.
    if (!node.querySelector(".cal")) {
      mount(node, el("div.faint", { text: "loading the week…" }));
    }

    let week;
    try {
      week = await this.load(this.offset);
    } catch (error) {
      // Handled rather than thrown: the harness's error card would replace the
      // whole panel, and the add box is how you fix the thing that is broken.
      mount(node,
        el("div.err", { text: `calendar: ${error.message}` }),
        el("div.row", null, el("button", { text: "retry", onclick: () => this.render(node) })),
        box.node,
      );
      box.restoreFocus(focus);
      return;
    }

    this.paint(node, week, focus);
    this.prefetch();
    return refreshAfter(week.feed);
  },

  // --- the cache ------------------------------------------------------------

  /**
   * Fetch one week, remember it, and never ask for the same one twice at once.
   *
   * The de-duplication is not a micro-optimisation: `prefetch` and a keypress
   * on `›` reach for the same neighbouring week constantly, and each miss is a
   * `gcalcli agenda` subprocess on the far side of WSL.
   */
  load(offset) {
    const already = this.inflight.get(offset);
    if (already) return already;

    const epoch = this.epoch;
    const request = api
      .get("/api/calendar", { date: api.today(), weeks: offset })
      .then((week) => {
        // Dropped on the floor if the calendar has been written to since this
        // was asked for. What came back is the week as it was *before* the
        // write, and putting that in the map is how a just-added event
        // disappears again a second later.
        if (epoch === this.epoch) this.weeks.set(offset, week);
        return week;
      })
      .finally(() => {
        // Only if it is still this request's slot. `forget` clears the map,
        // and a newer request for the same offset may already have claimed it.
        if (this.inflight.get(offset) === request) this.inflight.delete(offset);
      });

    this.inflight.set(offset, request);
    return request;
  },

  /**
   * Ask again for a week that was painted from the map, and redraw only if the
   * answer moved.
   *
   * Unconditionally repainting would take the week out from under a screen
   * someone is reading, five minutes at a time, to replace it with the same
   * seven columns — which is exactly the flicker the loading placeholder was
   * narrowed to avoid. What *does* always move is the `as of` stamp, and that
   * is patched in place below rather than being a reason to redraw the grid.
   */
  revalidate(node, offset, held) {
    this.load(offset)
      .then((fresh) => {
        if (this.offset !== offset || !node.isConnected) return;
        if (!sameWeek(held, fresh)) return void this.paint(node, fresh);
        const stamp = node.querySelector(".stamp");
        if (stamp) stamp.replaceWith(feedStamp(fresh.feed) ?? stamp);
      })
      // A failed revalidation leaves what is on screen alone. It is a week
      // that was fetched successfully once; an unreachable gcalcli is not a
      // reason to replace it with an error card.
      .catch(() => {});
  },

  /**
   * Warm the weeks either side of the one on screen.
   *
   * Wider on the first pass than afterwards. The two extra calls at boot are
   * paid while you are reading the week you asked for, and they are what makes
   * stepping through a month feel like paging rather than loading; once that
   * is done, one either side is enough to stay ahead of the arrows.
   */
  prefetch() {
    const spread = this.warmed ? [-1, 1] : [-1, 1, -2, 2];
    this.warmed = true;
    for (const delta of spread) {
      const offset = this.offset + delta;
      if (this.weeks.has(offset) || this.inflight.has(offset)) continue;
      // A neighbour that cannot be fetched is the neighbour's problem. It must
      // never put an error card over a week that is already drawn — page onto
      // it and the failure is reported then, loudly, by `render`.
      this.load(offset).catch(() => {});
    }
  },

  /**
   * Throw the map away. Called after anything that writes to Google.
   *
   * `Feeds.add_calendar_event` already invalidates every cached window on the
   * server; this is the same statement made on this side of the wire, and
   * without it the client would faithfully redraw the week from before the
   * event was added.
   */
  forget() {
    this.weeks.clear();
    this.inflight.clear();
    this.epoch += 1;
  },

  /** Every key is an offset from *today*, so none of them survive midnight. */
  expire() {
    const today = api.today();
    if (this.anchor === today) return;
    this.anchor = today;
    this.forget();
  },

  /** Draw a week that is already in hand. Synchronous, and the only painter. */
  paint(node, week, focus = this.box.focusState()) {
    const box = this.box;
    const start = parseISO(week.start);
    const days = [...Array(week.days)].map((_, i) => {
      const date = new Date(start);
      date.setDate(date.getDate() + i);
      return date;
    });

    const hues = hueMap(week.calendars.map((c) => c.name));
    box.setCalendars(week.calendars.filter((c) => c.writable).map((c) => c.name));

    const byDay = new Map(days.map((d) => [isoOf(d), { allDay: [], timed: [] }]));
    for (const event of week.events) {
      const bucket = byDay.get(event.date);
      if (!bucket) continue;
      if (!event.start) {
        bucket.allDay.push(event);
      } else {
        const from = minutes(event.start);
        // An event with no end, or one ending before it starts (a span that
        // crossed midnight), still has to occupy something.
        const to = event.end ? Math.max(minutes(event.end), from + 15) : from + 30;
        bucket.timed.push({ ...event, from, to });
      }
    }

    const [open, close] = windowFor([...byDay.values()].flatMap((b) => b.timed));

    // Everything outside the drawn window, per day, worked out once and used
    // three times: the grid draws `inside`, and the two strips draw the rest.
    const split = new Map(
      days.map((d) => [isoOf(d), outsiders(byDay.get(isoOf(d)).timed, open, close)]),
    );
    const anyBefore = [...split.values()].some((s) => s.before.length);
    const anyAfter = [...split.values()].some((s) => s.after.length);

    /** One edge strip: a row of seven cells, each a line of chips. */
    const strip = (which, label) =>
      [
        el(`div.cal-edge-gutter.cal-edge-${which}`, { text: label }),
        ...days.map((date) =>
          el(`div.cal-edge.cal-edge-${which}`, null,
            ...split.get(isoOf(date))[which].map((event) => chip(event, hues, clock(event.from))),
          ),
        ),
      ];

    mount(node,
      header(this, node, days),
      el("div.cal-scroll", null,
        el("div.cal",
          {
            // The px-per-minute scale lives here, not in the stylesheet: the
            // hour gridlines are a CSS gradient and would drift out of step
            // with the event positions the moment the two disagreed.
            style: [
              `--cal-height:${(close - open) * PX_PER_MIN}px`,
              `--cal-hour:${60 * PX_PER_MIN}px`,
              `--cal-days:${week.days}`,
            ].join(";"),
          },
          el("div.cal-corner"),
          ...days.map((date) => dayHead(date)),
          el("div.cal-allday-gutter", { text: "all-day" }),
          ...days.map((date) =>
            el("div.cal-allday", null,
              ...byDay.get(isoOf(date)).allDay.map((event) => chip(event, hues)))),
          // The two strips are present only on weeks that need them, so a week
          // that starts at nine and ends at six is the plain grid it was — the
          // clamp costs nothing on the days it is not doing anything.
          ...(anyBefore ? strip("before", `before ${hourLabel(open)}`) : []),
          el("div.cal-ruler", null, ...ruler(open, close)),
          ...days.map((date) =>
            column(split.get(isoOf(date)).inside, date, open, close, hues)),
          ...(anyAfter ? strip("after", `after ${hourLabel(close)}`) : []),
        ),
      ),
      legend(week.calendars, hues),
      box.node,
      feedStamp(week.feed),
    );
    // `mount` detaches every child before re-appending, and detaching an input
    // blurs it — so a refresh that fired while you were typing would leave the
    // text in place and the caret nowhere.
    box.restoreFocus(focus);
  },
};

/**
 * Whether two fetches of the same week say the same thing.
 *
 * Deliberately not a comparison of the whole payload: `feed.fetched_at` moves
 * on every real fetch, so including it would make every revalidation a
 * difference and every difference a redraw. What is compared is what is drawn.
 */
function sameWeek(a, b) {
  return (
    a.start === b.start &&
    a.days === b.days &&
    JSON.stringify(a.events) === JSON.stringify(b.events) &&
    JSON.stringify(a.calendars) === JSON.stringify(b.calendars)
  );
}

function header(panel, node, days) {
  const first = days[0];
  const last = days[days.length - 1];
  const span = first.getMonth() === last.getMonth()
    ? `${MONTHS[first.getMonth()]} ${first.getDate()}–${last.getDate()}`
    : `${MONTHS[first.getMonth()]} ${first.getDate()} – ${MONTHS[last.getMonth()]} ${last.getDate()}`;

  const go = (delta) => {
    panel.offset += delta;
    panel.render(node);
  };
  return el("div.cal-head", null,
    el("div.cal-span", { text: `${span}, ${first.getFullYear()}` }),
    el("div.row", null,
      // Beside the week's own controls rather than beside the box: the question
      // it answers ("how does typing into this thing work") is asked about the
      // panel, and the `?` down by the box already means something narrower —
      // it is the alias table.
      el("button.ghost", {
        text: "?",
        title: "how the box works — events, todos and aliases",
        "aria-label": "how the box works",
        onclick: openCalendarHelp,
      }),
      el("button.ghost", { text: "‹", title: "previous week", onclick: () => go(-1) }),
      el("button.ghost", {
        text: "today",
        disabled: panel.offset === 0,
        onclick: () => {
          panel.offset = 0;
          panel.render(node);
        },
      }),
      el("button.ghost", { text: "›", title: "next week", onclick: () => go(1) }),
    ),
  );
}

function dayHead(date) {
  const today = isoOf(date) === api.today();
  return el(`div.cal-day-head${today ? ".is-today" : ""}`, null,
    el("span.cal-dow", { text: DAY_NAMES[(date.getDay() + 6) % 7] }),
    el("span.cal-dom", { text: String(date.getDate()) }),
  );
}

function ruler(open, close) {
  const rows = [];
  for (let m = open; m < close; m += 60) {
    rows.push(el("div.cal-hour", {
      style: `top:${(m - open) * PX_PER_MIN}px`,
      text: clock(m),
    }));
  }
  return rows;
}

/**
 * One day's column. `timed` is already the events the grid can draw — the ones
 * lying wholly outside the window went to the strips before this was called.
 *
 * What is left can still *overlap* an edge, and those are clamped to it: a 7:30
 * session on an 8am window starts at the top of the column and is drawn half an
 * hour shorter than it is. Drawing it at a negative offset would put it behind
 * the day headings, and moving it to a strip would exile an event that is
 * mostly on screen.
 */
function column(timed, date, open, close, hues) {
  const today = isoOf(date) === api.today();
  const blocks = pack(timed).map((event) => {
    const width = 100 / event.of;
    const from = Math.max(event.from, open);
    const to = Math.min(event.to, close);
    const short = (to - from) * PX_PER_MIN < TWO_LINE_PX;
    return el(`div.cal-event${short ? ".is-short" : ""}`, {
      style: [
        `top:${(from - open) * PX_PER_MIN}px`,
        `height:${(to - from) * PX_PER_MIN}px`,
        `left:${event.column * width}%`,
        `width:calc(${width}% - 2px)`,
        `--hue:${hues.get(event.calendar) ?? HUES[0]}`,
      ].join(";"),
      title: `${event.title}\n${clock(event.from)}–${clock(event.to)}\n${event.calendar}`,
    },
      el("span.cal-event-time", { text: clock(event.from) }),
      el("span.cal-event-title", { text: event.title }),
    );
  });

  return el(`div.cal-col${today ? ".is-today" : ""}`, null,
    ...blocks,
    today ? nowLine(open, close) : null,
  );
}

/**
 * The line across today. Absent from every other column — it would be a lie —
 * and absent outside the drawn window, on the same argument: at seven in the
 * morning a line pinned to the 8am edge would be claiming it is eight.
 */
function nowLine(open, close) {
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  if (mins < open || mins > close) return null;
  return el("div.cal-now", { style: `top:${(mins - open) * PX_PER_MIN}px` });
}

/**
 * One event as a line of text rather than a block.
 *
 * Used by the all-day gutter, which has no time to show, and by the two edge
 * strips, which do — an event that has given up its position on the grid has
 * to say when it is in words, or the strip is a list of things happening at
 * some unspecified point in the evening.
 */
function chip(event, hues, at = null) {
  return el("span.cal-chip", {
    style: `--hue:${hues.get(event.calendar) ?? HUES[0]}`,
    title: at
      ? `${event.title}\n${at}\n${event.calendar}`
      : `${event.title}\n${event.calendar}`,
  },
    at ? el("span.cal-chip-at", { text: at }) : null,
    el("span", { text: event.title }),
  );
}

/** `8am` — a window edge, said the way the ruler says its hours. */
const hourLabel = (mins) => clock(mins);

function legend(calendars, hues) {
  return el("div.cal-legend", null,
    ...calendars.map((cal) =>
      el("span.cal-key", { style: `--hue:${hues.get(cal.name)}` }, el("i"), cal.name)),
  );
}

// --- the add box -----------------------------------------------------------
//
// One box, and it writes two kinds of thing. Typing resolves a preview
// server-side — the same parse, the same aliases and the same classifier the
// write will use, so what is drawn under the box is what is about to happen
// rather than a second implementation of it that can drift.
//
// It is a textarea rather than an input because the case it has to be good at
// is a semester's reading list arriving in one paste. Every line previews
// independently, Ctrl+Enter commits the batch, and the lines that could not be
// written stay behind with their errors while the rest disappear — one typo in
// forty lines must not cost the other thirty-nine.
//
// --- todo or event ---------------------------------------------------------
//
// There is no mode switch and no sigil. The target calendar decides: `Homework`
// and `Tests` mean work, which gets a row in the list below and a checkbox;
// anything else is an ordinary event. The picker that chooses it was already
// on screen and already says where the thing is going, so the distinction
// costs no vocabulary at all.
//
// `auto` is the default and is *not* the model — it is subject, then the tests
// rule, then Homework, because nineteen dated lines in twenty are homework and
// a two-thirds-accurate guess is a poor way to overrule that. `learned` is the
// model, one Tab away, for the lines that are not work.
//
// Choosing a calendar explicitly is also the training signal: the server
// records it against what it would otherwise have picked, so the same phrasing
// stops being wrong.
//
// Only writable calendars are listed. A `reader` calendar — the US holidays
// feed, a shared course calendar — is drawn in the grid and can never be
// written to, and offering it would be offering a guaranteed failure.

// The picker's two sentinels. `AUTO` is the deterministic default policy and
// is what the box sits on; `MODEL` hands the line to the classifier.
const AUTO = "";
const MODEL = "model";

// How the calendar was arrived at, in the preview's own words. `default` and
// `fallback` are deliberately not the same phrase: the first is a confident
// prior — work is homework — and the second is the model admitting it has
// nothing, which is worth telling apart when you are deciding whether to Tab.
const SOURCE_LABEL = {
  rule: "rule",
  model: "learned",
  correction: "corrected",
  fallback: "no guess",
  subject: "subject",
  default: "default",
  chosen: "chosen",
  // Nothing decided a calendar, because the line does not want one.
  todo: "undated",
};


/**
 * The alias list, behind the circled `?`.
 *
 * A table rather than a tooltip because the question it answers is "what did I
 * call that course", which is asked while looking at an *empty* box — and the
 * answer is something you scan, not a sentence you read.
 *
 * Fetched on first open rather than with the week. It is configuration that
 * changes once a semester, nobody opens it on most days, and the panel it
 * hangs off already makes two requests on every five-minute refresh.
 */
function aliasList() {
  const box = el("div.cal-aliases", { hidden: true });
  let loaded = false;

  const load = async () => {
    try {
      const { aliases } = await api.get("/api/todo", { date: api.today() });
      mount(box,
        aliases.length
          ? el("dl.cal-aliases-list", null,
              ...aliases.flatMap((a) => [
                el("dt", { text: a.key }),
                el("dd", { text: a.calendar ? `${a.name} → ${a.calendar}` : a.name }),
              ]),
            )
          : el("span.faint", { text: "no aliases configured — see [todo.subjects]" }),
      );
      loaded = true;
    } catch (error) {
      mount(box, el("span.cal-pv-warn", { text: error.message }));
    }
  };

  return {
    node: box,
    toggle() {
      box.hidden = !box.hidden;
      if (!box.hidden && !loaded) load();
    },
  };
}

/** One previewed line: what it will write, or why it will not. */
function previewLine(line) {
  if (line.error) {
    return el("div.cal-pv.bad", null,
      el("span.cal-pv-raw", { text: line.raw }),
      el("span.cal-pv-warn", { text: line.error }),
    );
  }

  // A `todo` line has no date and no calendar, because it writes no event.
  // Both halves are stated rather than left blank: "no day" where the day goes,
  // and "not on the calendar" where the target does — a line that quietly
  // showed neither would look like a line that had failed to parse.
  const day = line.undated ? "no day" : (() => {
    const date = parseISO(line.date);
    return `${DAY_NAMES[(date.getDay() + 6) % 7]} ${MONTHS[date.getMonth()]} ${date.getDate()}`;
  })();
  const when = line.undated ? "no date" : line.start ? `${line.start}–${line.end}` : "all day";

  return el("div.cal-pv", null,
    // Whether an alias fired is drawn first and always, in one of two states.
    // That is the whole point of it: a subject you forgot to abbreviate and one
    // you misspelled both look like plain text, and the difference is only
    // visible if the absence is shown as loudly as the presence.
    line.aliases.length
      ? el("span.cal-alias.hit", { text: line.aliases.map((a) => a.key).join(" ") })
      : el("span.cal-alias.miss", { text: "—", title: "no alias in this line" }),
    el("span.cal-pill", {
      class: line.undated ? "none" : "",
      text: line.calendar ?? "not on the calendar",
    }),
    el("span.cal-pv-title", { text: line.title }),
    el("span.cal-pv-when", {
      text: line.recurrence ? `${day} · ${when} · ${line.recurrence}` : `${day} · ${when}`,
    }),
    line.is_todo ? el("span.cal-kind", { text: "todo" }) : null,
    el("span.cal-pv-src", { text: SOURCE_LABEL[line.source] ?? line.source }),
    line.warning ? el("span.cal-pv-warn", { text: line.warning }) : null,
  );
}

function quickAdd(panel, node) {
  const input = el("textarea.cal-input", {
    placeholder: "fpm Thornhill Chapter 3 due thursday   ·   one per line, ctrl+enter to add",
    spellcheck: false,
    autocomplete: "off",
    rows: 1,
  });
  const picker = el("select.cal-picker", {
    title: "which calendar to add to — Tab cycles",
    "aria-label": "target calendar",
  });
  // Was a second `?`, which stopped being legible the moment the header grew
  // one of its own: two identical buttons on one card asking you to guess
  // which is the smaller question. This one is named after its table.
  const help = el("button.ghost.cal-help", {
    text: "aliases",
    title: "what the aliases expand to",
    "aria-label": "alias list",
  });
  const preview = el("div.cal-preview");
  const aliases = aliasList();

  help.addEventListener("click", () => aliases.toggle());

  // The model behind `learned` refits itself at boot, when a calendar appears
  // or disappears, and after every correction — but none of those re-read the
  // event history, which is cached for a day and is most of what it learns
  // from. On a server left running, this is the only way to say "look at my
  // calendars again".
  const retrain = el("button.ghost.cal-retrain", {
    text: "retrain",
    title: "re-read the calendars and refit the model behind “learned”",
    onclick: async () => {
      retrain.disabled = true;
      const said = retrain.textContent;
      retrain.textContent = "retraining…";
      try {
        const status = await api.postRaw("/api/calendar/retrain", {});
        mount(preview,
          el("span.cal-pv-src", {
            text: status.fitted
              ? `refitted on ${status.samples} events across ${status.calendars.length}` +
                ` calendars${status.corrections ? `, ${status.corrections} corrections` : ""}`
              : (status.unavailable ?? "could not fit a model"),
          }),
        );
      } catch (error) {
        mount(preview, el("span.cal-pv-warn", { text: `retrain failed: ${error.message}` }));
      } finally {
        retrain.disabled = false;
        retrain.textContent = said;
      }
    },
  });

  // The controls sit in their own row so the box can shed them when nobody is
  // typing. Open, this block is 176px of the home tab held permanently by three
  // buttons, a dropdown and an empty preview line — for a box that is used a
  // few times a day and is one keystroke (`i`) from being ready. It comes back
  // whole on focus, on hover, and whenever there is text in it, so nothing is
  // ever hidden while it could matter.
  const controls = el("div.cal-add-controls", null, picker, help, retrain);
  const wrap = el("div.cal-add", null,
    el("div.cal-add-row", null, input, controls),
    aliases.node,
    preview,
  );

  let lines = [];
  let writable = [];
  let timer = null;
  let sequence = 0;

  /** Open while it holds anything, so a half-typed line cannot fold away. */
  const markFilled = () => wrap.classList.toggle("filled", input.value.trim() !== "");

  /** The picker is the single source of truth; `auto` defers to the default. */
  const chosen = () => picker.value || null;

  /**
   * Grow with the content. A one-line box that scrolls hides the paste.
   *
   * A zero measurement means there is no layout to measure — the panel is on a
   * hidden tab, or the title screen is still up — and writing it back would
   * pin the box shut at `height: 0` for the rest of the session, since nothing
   * re-measures until the next keystroke. Left alone, it keeps the `rows`
   * height CSS gave it and fits itself on the first key typed into it.
   */
  const fit = () => {
    input.style.height = "auto";
    const wanted = input.scrollHeight;
    if (wanted > 0) input.style.height = `${Math.min(wanted, 260)}px`;
  };

  const setCalendars = (names) => {
    // The picker outlives the render, so rebuilding its options on every
    // refresh would be replacing a control the user may have open, for no
    // reason. Only a genuine change to the account's calendars redraws it.
    if (names.length === writable.length && names.every((n, i) => n === writable[i])) return;
    writable = names;
    mount(picker,
      el("option", { value: AUTO, text: "auto" }),
      ...names.map((name) => el("option", { value: name, text: name })),
      // Last, so Tab reaches every real calendar before offering the guess.
      el("option", { value: MODEL, text: "learned" }),
    );
    // Restore the panel's choice, and fall back to auto if that calendar is
    // gone — deleting the calendar you had selected must not leave the box
    // pointed at something that cannot be written to.
    picker.value = names.includes(panel.target) || panel.target === MODEL ? panel.target : AUTO;
    panel.target = picker.value;
  };

  const draw = () => {
    input.classList.toggle("has-alias", lines.some((l) => l.aliases.length));
    input.classList.toggle("bad", lines.some((l) => l.error));
    if (!lines.length) return void mount(preview);
    mount(preview, ...lines.map(previewLine));
  };

  const resolve = async () => {
    const text = input.value.trim();
    if (!text) {
      lines = [];
      draw();
      return;
    }
    const mine = ++sequence;
    try {
      const got = await api.postRaw("/api/calendar/preview", {
        text,
        today: api.today(),
        ...(chosen() ? { calendar: chosen() } : {}),
      });
      // A slower earlier keystroke must not overwrite a newer answer.
      if (mine !== sequence) return;
      lines = got.lines;
      draw();
    } catch (error) {
      if (mine !== sequence) return;
      lines = [];
      mount(preview, el("span.cal-pv-warn", { text: error.message }));
    }
  };

  picker.addEventListener("change", () => {
    panel.target = picker.value;
    resolve();
  });

  input.addEventListener("input", () => {
    fit();
    markFilled();
    clearTimeout(timer);
    // Long enough that typing a sentence is one round trip rather than thirty,
    // short enough that stopping to look shows an answer immediately.
    timer = setTimeout(resolve, 220);
  });

  const commit = async () => {
    const text = input.value.trim();
    if (!text) return;
    // Enter can arrive before the debounce has fired, so a fast typist must
    // still write the events a slow one would have written.
    clearTimeout(timer);

    const target = chosen();
    input.disabled = true;
    picker.disabled = true;
    mount(preview, el("span.faint", { text: "adding…" }));
    try {
      const result = await api.postRaw("/api/calendar/add", {
        text,
        today: api.today(),
        // Omitted under `auto`: the server then uses its own policy and
        // records no correction, which is right — nothing was corrected.
        ...(target ? { calendar: target } : {}),
      });
      // What failed stays in the box, in the words it was typed in, so the fix
      // is an edit. What was written is gone, because it is on the calendar
      // now and the grid below is about to show it.
      input.value = result.failed.map((line) => line.raw).join("\n");
      lines = result.failed;
      input.disabled = false;
      picker.disabled = false;
      fit();
      // The picker keeps its selection across an add. Filing three things onto
      // Tests in a row should not mean choosing it three times.
      //
      // This render redraws the grid around the box rather than replacing it,
      // so the week does not blink out and back while the new events arrive.
      // `forget` first, and it has to be first: a line can name any date, so
      // the week that changed is not necessarily the week on screen, and every
      // held week is now a week from before this write.
      panel.forget();
      await panel.render(node);
      rerender("todo");
      input.focus();
      draw();
    } catch (error) {
      input.disabled = false;
      picker.disabled = false;
      mount(preview, el("span.cal-pv-warn", { text: `failed: ${error.message}` }));
    }
  };

  input.addEventListener("keydown", async (event) => {
    if (event.key === "Tab" && picker.options.length > 1) {
      // Tab switches dashboard tabs everywhere else, so this has to stop the
      // app-level handler as well as the browser's own focus move. It steps the
      // picker rather than keeping a parallel notion of the target, so the
      // keyboard and the mouse cannot disagree — and it wraps through `auto`,
      // which is how you get back to the default after overruling it.
      event.preventDefault();
      event.stopPropagation();
      const n = picker.options.length;
      picker.selectedIndex = (picker.selectedIndex + (event.shiftKey ? -1 : 1) + n) % n;
      panel.target = picker.value;
      resolve();
      return;
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      input.value = "";
      lines = [];
      picker.value = AUTO;
      panel.target = AUTO;
      fit();
      draw();
      return;
    }
    // Enter alone is a newline — the box holds a list. Ctrl/Cmd+Enter commits,
    // and so does a bare Enter while the box still holds a single line, which
    // is what the muscle memory from the old input expects.
    if (event.key !== "Enter") return;
    if (!event.ctrlKey && !event.metaKey && input.value.includes("\n")) return;
    event.preventDefault();
    await commit();
  });

  return {
    node: wrap,
    setCalendars,

    /** What the caret was doing before a re-render, or null if it was elsewhere. */
    focusState: () =>
      document.activeElement === input
        ? { start: input.selectionStart, end: input.selectionEnd }
        : null,

    restoreFocus: (state) => {
      if (!state) return;
      input.focus();
      input.setSelectionRange(state.start, state.end);
    },

    focus: () => input.focus(),
  };
}
