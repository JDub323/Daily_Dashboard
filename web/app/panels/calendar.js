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

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

// The clamp on the drawn window. A calendar rendered midnight-to-midnight
// spends two thirds of its height on hours nobody has ever scheduled anything
// in, which is exactly the space the events needed.
const DEFAULT_OPEN = 8 * 60;
const DEFAULT_CLOSE = 21 * 60;
const PX_PER_MIN = 0.9;

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

/** The drawn window: waking hours, widened to fit whatever is actually on. */
export function windowFor(timed) {
  let open = DEFAULT_OPEN;
  let close = DEFAULT_CLOSE;
  for (const event of timed) {
    open = Math.min(open, event.from);
    close = Math.max(close, event.to);
  }
  // Rounded out to the hour so the ruler carries whole labels.
  return [Math.floor(open / 60) * 60, Math.ceil(close / 60) * 60];
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
      week = await api.get("/api/calendar", { date: api.today(), weeks: this.offset });
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
          el("div.cal-ruler", null, ...ruler(open, close)),
          ...days.map((date) => column(byDay.get(isoOf(date)), date, open, hues)),
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
    return refreshAfter(week.feed);
  },
};

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

function column(bucket, date, open, hues) {
  const today = isoOf(date) === api.today();
  const blocks = pack(bucket.timed).map((event) => {
    const width = 100 / event.of;
    const short = event.to - event.from <= 45;
    return el(`div.cal-event${short ? ".is-short" : ""}`, {
      style: [
        `top:${(event.from - open) * PX_PER_MIN}px`,
        `height:${(event.to - event.from) * PX_PER_MIN}px`,
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
    today ? nowLine(open) : null,
  );
}

/** The line across today. Absent from every other column — it would be a lie. */
function nowLine(open) {
  const now = new Date();
  return el("div.cal-now", {
    style: `top:${(now.getHours() * 60 + now.getMinutes() - open) * PX_PER_MIN}px`,
  });
}

function chip(event, hues) {
  return el("span.cal-chip", {
    style: `--hue:${hues.get(event.calendar) ?? HUES[0]}`,
    text: event.title,
    title: `${event.title}\n${event.calendar}`,
  });
}

function legend(calendars, hues) {
  return el("div.cal-legend", null,
    ...calendars.map((cal) =>
      el("span.cal-key", { style: `--hue:${hues.get(cal.name)}` }, el("i"), cal.name)),
  );
}

// --- the add box -----------------------------------------------------------
//
// An input, and a picker for where the event goes. Typing resolves a preview
// server-side — the same parse and the same classifier the write will use, so
// the line under the box is what is about to happen rather than a second
// implementation of it that can drift. Enter commits.
//
// The picker's default is `auto`, meaning "whatever the classifier decided",
// and the preview then names the calendar it landed on so `auto` is never a
// black box. On this account's history `auto` is right about two thirds of the
// time and has the right answer in its top two about nine tenths of the time,
// which is exactly why the picker exists beside it rather than instead of it.
//
// Choosing a calendar explicitly is also the training signal: the server
// records it against what it would have guessed, so the same phrasing stops
// being wrong.
//
// Only writable calendars are listed. A `reader` calendar — the US holidays
// feed, a shared course calendar — is drawn in the grid and can never be
// written to, and offering it would be offering a guaranteed failure.

const AUTO = "";

const SOURCE_LABEL = {
  rule: "rule",
  model: "learned",
  correction: "corrected",
  fallback: "no guess",
};

function quickAdd(panel, node) {
  const input = el("input.cal-input", {
    placeholder: "add real analysis test wednesday 9:25-10:30",
    spellcheck: false,
    autocomplete: "off",
  });
  const picker = el("select.cal-picker", {
    title: "which calendar to add to — Tab cycles",
    "aria-label": "target calendar",
  });
  const preview = el("div.cal-preview");
  const wrap = el("div.cal-add", null, el("div.cal-add-row", null, input, picker), preview);

  let resolved = null;
  let writable = [];
  let timer = null;
  let sequence = 0;

  /** The picker is the single source of truth; `auto` defers to the model. */
  const chosen = () => picker.value || null;

  const setCalendars = (names) => {
    // The picker now outlives the render, so rebuilding its options on every
    // refresh would be replacing a control the user may have open, for no
    // reason. Only a genuine change to the account's calendars redraws it.
    if (names.length === writable.length && names.every((n, i) => n === writable[i])) return;
    writable = names;
    mount(picker,
      el("option", { value: AUTO, text: "auto" }),
      ...names.map((name) => el("option", { value: name, text: name })),
    );
    // Restore the panel's choice, and fall back to auto if that calendar is
    // gone — deleting the calendar you had selected must not leave the box
    // pointed at something that cannot be written to.
    picker.value = names.includes(panel.target) ? panel.target : AUTO;
    panel.target = picker.value;
  };

  const draw = () => {
    if (!resolved) return void mount(preview);
    const override = chosen();
    const target = override ?? resolved.calendar;
    const date = parseISO(resolved.date);
    const day = `${DAY_NAMES[(date.getDay() + 6) % 7]} ${MONTHS[date.getMonth()]} ${date.getDate()}`;
    mount(preview,
      el("span.cal-pill", { text: target }),
      el("span.cal-pv-title", { text: resolved.title }),
      el("span.cal-pv-when", {
        text: `${day} · ${resolved.start ? `${resolved.start}–${resolved.end}` : "all day"}`,
      }),
      el("span.cal-pv-src", {
        // Under `auto` this says how the calendar was arrived at. Once you have
        // picked one it says so, because "learned" beside a calendar you chose
        // by hand would be claiming credit for your decision.
        text: override ? "chosen" : SOURCE_LABEL[resolved.source],
      }),
      resolved.warning ? el("span.cal-pv-warn", { text: resolved.warning }) : null,
    );
  };

  picker.addEventListener("change", () => {
    panel.target = picker.value;
    draw();
  });

  const resolve = async () => {
    const text = input.value.trim();
    if (!text) {
      resolved = null;
      draw();
      return;
    }
    const mine = ++sequence;
    try {
      const got = await api.postRaw("/api/calendar/preview", { text, today: api.today() });
      // A slower earlier keystroke must not overwrite a newer answer.
      if (mine !== sequence) return;
      resolved = got;
      draw();
    } catch (error) {
      if (mine !== sequence) return;
      resolved = null;
      mount(preview, el("span.cal-pv-warn", { text: error.message }));
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    // Long enough that typing a sentence is one round trip rather than thirty,
    // short enough that stopping to look shows an answer immediately.
    timer = setTimeout(resolve, 220);
  });

  input.addEventListener("keydown", async (event) => {
    if (event.key === "Tab" && picker.options.length > 1) {
      // Tab switches dashboard tabs everywhere else, so this has to stop the
      // app-level handler as well as the browser's own focus move. It steps the
      // picker rather than keeping a parallel notion of the target, so the
      // keyboard and the mouse cannot disagree — and it wraps through `auto`,
      // which is how you get back to the guess after overruling it.
      event.preventDefault();
      event.stopPropagation();
      const n = picker.options.length;
      picker.selectedIndex = (picker.selectedIndex + (event.shiftKey ? -1 : 1) + n) % n;
      panel.target = picker.value;
      draw();
      return;
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      input.value = "";
      resolved = null;
      picker.value = AUTO;
      draw();
      return;
    }
    if (event.key !== "Enter") return;

    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    // Enter can arrive before the debounce has fired, so a fast typist must
    // still write the event a slow one would have written.
    clearTimeout(timer);
    if (!resolved) await resolve();
    if (!resolved) return;

    const target = chosen();
    input.disabled = true;
    picker.disabled = true;
    mount(preview, el("span.faint", { text: "adding…" }));
    try {
      await api.postRaw("/api/calendar/add", {
        text,
        today: api.today(),
        // Omitted under `auto`: the server then uses its own prediction and
        // records no correction, which is right — nothing was corrected.
        ...(target ? { calendar: target } : {}),
      });
      input.value = "";
      resolved = null;
      input.disabled = false;
      picker.disabled = false;
      // The picker keeps its selection across an add. Filing three things onto
      // Homework in a row should not mean choosing it three times.
      //
      // This render redraws the grid around the box rather than replacing it,
      // so the week does not blink out and back while the new event is fetched.
      await panel.render(node);
      input.focus();
    } catch (error) {
      input.disabled = false;
      picker.disabled = false;
      mount(preview, el("span.cal-pv-warn", { text: `failed: ${error.message}` }));
    }
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
