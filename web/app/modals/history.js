// The retrospective view: everything the app already knew and never showed.
//
// The dashboard is strictly today-shaped. The only backward-looking things in
// it were a 40-point pooled elo sparkline and three streak counters — so the
// number that actually decides what you get served each morning, the per-area
// math rating, appeared only as a current figure with no trend, and the day
// log was write-only: `repo.daylog(today)` was its single read path anywhere.
//
// A modal rather than a panel, on the theorem archive's reasoning: the columns
// are for things you glance at daily, and a surface you consult when you want
// to know how a month went would hold that space permanently. No chip either —
// the chip row is today's checklist, and looking back is not a task you finish.
//
// Three modes behind tabs rather than three stacked sections. Each answers a
// different question and only one of them is ever the reason you opened this.

import * as api from "../api.js";
import { lineChart } from "../charts.js";
import { el, mount } from "../dom.js";
import { store } from "../store.js";
import { openModal } from "../ui.js";

let cache = null;
let mode = "activity";

export function openHistoryModal() {
  openModal("history", async (body) => {
    // The heatmap needs no fetch — `completion` ships whole in the boot
    // payload — so it draws immediately and the fetch only gates the other
    // two tabs. Opening on "activity" therefore never shows a spinner.
    draw(body);
    if (cache) return;
    try {
      cache = await api.get("/api/history");
    } catch (error) {
      cache = { areas: [], daylogs: [], error: error.message };
    }
    draw(body);
  });
}

const MODES = {
  activity: { label: "activity", render: (body) => activity(body) },
  areas: { label: "elo by area", render: (body) => areas(body) },
  logs: { label: "day logs", render: (body) => logs(body) },
};

function draw(body) {
  mount(body,
    el("div.row.gap", null,
      ...Object.entries(MODES).map(([key, { label }]) =>
        el(`button${key === mode ? ".primary" : ""}`, {
          text: label,
          onclick: () => {
            mode = key;
            draw(body);
          },
        }),
      ),
    ),
    MODES[mode].render(body),
  );
}

/** Shared by the two tabs that wait on the fetch. */
function pending() {
  if (!cache) return el("div.faint", { text: "loading…" });
  if (cache.error) return el("div.err", { text: cache.error });
  return null;
}

// --- the completion heatmap ------------------------------------------------
//
// The panel that makes a streak feel like something rather than a number. It
// is built entirely from `day_completion`, which the boot payload already
// carries in full.

const DAY_MS = 864e5;
const MIN_WEEKS = 12;
const MAX_WEEKS = 53;

/** Local midnight for an ISO date, via noon — see the piano panel's day index. */
const at = (iso) => new Date(`${iso}T12:00:00`);

/** Five buckets: nothing, then four shades of how much of the day got done. */
function level(row) {
  if (!row || !row.done) return 0;
  const pct = row.done / row.total;
  if (pct >= 1) return 4;
  if (pct >= 0.7) return 3;
  if (pct >= 0.4) return 2;
  return 1;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * How many week-columns to draw.
 *
 * Sized to the data rather than fixed at a year. A fresh install with nine
 * days of history should not render a 53-column grid that is 96% empty — that
 * reads as "you have done almost nothing" when what it means is "you started
 * last week". Grows to a full year and stops.
 */
function weekCount(rows, today) {
  if (!rows.length) return MIN_WEEKS;
  const earliest = rows.reduce((a, r) => (r.date < a ? r.date : a), rows[0].date);
  const days = Math.round((today - at(earliest)) / DAY_MS);
  return Math.max(MIN_WEEKS, Math.min(MAX_WEEKS, Math.ceil(days / 7) + 1));
}

function activity(body) {
  const rows = store.state.completion ?? [];
  const byDate = new Map(rows.map((r) => [r.date, r]));

  const today = at(api.today());
  const weeks = weekCount(rows, today);

  // The grid ends on the Saturday of the current week, so today's column is
  // the last one and the shape is stable through the week rather than
  // reflowing every morning.
  const end = new Date(today);
  end.setDate(end.getDate() + (6 - end.getDay()));
  const start = new Date(end);
  start.setDate(start.getDate() - (weeks * 7 - 1));

  const cells = [];
  const labels = [];
  let lastMonth = -1;

  for (let w = 0; w < weeks; w++) {
    // One label per column, on the first column that contains a new month.
    const columnStart = new Date(start);
    columnStart.setDate(columnStart.getDate() + w * 7);
    const month = columnStart.getMonth();
    const isNew = month !== lastMonth && columnStart.getDate() <= 7;
    labels.push(el("span.hm-month", { text: isNew ? MONTHS[month] : "" }));
    if (isNew) lastMonth = month;

    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(day.getDate() + w * 7 + d);
      if (day > today) {
        // Days that have not happened yet are held open, not drawn as misses.
        cells.push(el("div.hm-cell.hm-future"));
        continue;
      }
      const iso = day.toISOString().slice(0, 10);
      const row = byDate.get(iso);
      cells.push(el(`div.hm-cell.hm-${level(row)}`, {
        title: row ? `${iso} — ${row.done}/${row.total} done` : `${iso} — nothing recorded`,
      }));
    }
  }

  const done = rows.filter((r) => r.done >= r.total).length;

  return el("div", null,
    el("div.row.spread", null,
      el("span.faint", {
        text: `${rows.length} day${rows.length === 1 ? "" : "s"} recorded · ${done} perfect`,
      }),
      el("span.gold", { text: `${store.state.perfect_day_streak} day streak` }),
    ),
    el("div.hm-scroll", null,
      el("div.hm-wrap", null,
        el("div.hm-months", { style: `grid-template-columns: repeat(${weeks}, var(--hm-col))` },
          ...labels),
        el("div.heatmap", null, ...cells),
      ),
    ),
    el("div.row.gap", null,
      el("span.faint.small", { text: "less" }),
      ...[0, 1, 2, 3, 4].map((n) => el(`div.hm-cell.hm-${n}`)),
      el("span.faint.small", { text: "more · full square = every task done" }),
    ),
  );
}

// --- per-area elo ----------------------------------------------------------

function areas() {
  const waiting = pending();
  if (waiting) return waiting;

  if (!cache.areas.length) {
    return el("div.faint", {
      text: "no area has a measured result yet — the breakdown in the math panel is"
        + " your overall rating standing in until one does",
    });
  }

  return el("div", null,
    el("div.faint.small", {
      text: "the rating each week's topic is actually pitched against. Only measured"
        + " results are plotted — reconstructed rows carry no rating and are not drawn.",
    }),
    ...cache.areas.map((series) => {
      const values = series.points.map(([, rating]) => rating);
      const first = series.points[0][0];
      return el("div.chart-block", null,
        el("div.chart-title", null,
          el("span", { text: series.area }),
          el("span.gold", { text: values[values.length - 1] }),
        ),
        lineChart(values, { color: "var(--accent)" }),
        el("div.faint.small", {
          text: `${series.events} result${series.events === 1 ? "" : "s"} since ${first}`,
        }),
      );
    }),
  );
}

// --- the day-log archive ---------------------------------------------------

function logs() {
  const waiting = pending();
  if (waiting) return waiting;

  if (!cache.daylogs.length) {
    return el("div.faint", { text: "no day logs yet" });
  }

  return el("div", null,
    el("div.faint.small", { text: `${cache.daylogs.length} entries, newest first` }),
    el("div.archive", null,
      ...cache.daylogs.map((entry) =>
        el("div.archive-item", null,
          el("div.gold.small", { text: entry.date }),
          el("div.daylog-text", { text: entry.text }),
        ),
      ),
    ),
  );
}

/** Dropped when a write lands, so a reopened modal is not showing stale rows. */
export function invalidateHistory() {
  cache = null;
}
