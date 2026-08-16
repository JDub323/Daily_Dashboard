// The home tab: everything the app already knew and never showed.
//
// This was a modal hanging off the elo panel — three tabs of retrospective
// crammed into a 680px box you had to know to press `h` for. The reasoning at
// the time was that a surface you consult monthly should not hold column space
// daily, and that was right about columns and wrong about the surface: the
// answer was never a smaller box, it was its own page.
//
// So: the completion heatmap is the hero, in the slot Edge gives the search
// bar — the first thing on the first tab, because "did I keep this up" is the
// question the whole dashboard exists to answer. The streak and the running
// cost sit with it. The two deeper reads — per-area elo, the day-log archive —
// are panels below it rather than tabs inside it, since nothing is competing
// for the space any more.

import * as api from "../api.js";
import { lineChart } from "../charts.js";
import { el } from "../dom.js";
import { store } from "../store.js";

// --- the shared /api/history read -------------------------------------------
//
// Two panels want it and neither should wait on the other's request.

let cache = null;
let inflight = null;

function history() {
  if (cache) return Promise.resolve(cache);
  inflight ??= api
    .get("/api/history")
    .then((data) => (cache = data))
    .catch((error) => (cache = { areas: [], daylogs: [], error: error.message }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Dropped when the day turns, so a stale month is not still on screen. */
export function invalidateHistory() {
  cache = null;
}

/** Shared by the two panels that wait on the fetch. */
function failed(data) {
  return data.error ? el("div.err", { text: data.error }) : null;
}

// --- the completion heatmap -------------------------------------------------
//
// Built entirely from `day_completion`, which the boot payload already carries
// in full — so it draws on the first frame and never shows a spinner.

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

function heatmap() {
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
      const iso = api.localISO(day);
      const row = byDate.get(iso);
      cells.push(el(`div.hm-cell.hm-${level(row)}`, {
        title: row ? `${iso} — ${row.done}/${row.total} done` : `${iso} — nothing recorded`,
      }));
    }
  }

  return { cells, labels, weeks, rows };
}

// --- spend ------------------------------------------------------------------
//
// The first thing in the app that reads metrics.jsonl. Every paid call has
// been appending to that file since the first generation and nothing had ever
// opened it, which made the only recurring cost the app has the one number it
// would not show you.

/**
 * `$0.36`, or `<$0.01` for a real but sub-cent amount.
 *
 * Rounding a third of a cent to "$0.00" would say nothing was spent, which is
 * a different claim from "not very much was". The distinction matters most on
 * exactly the calls this is meant to make visible — the cheap ones that add up.
 */
function usd(amount) {
  if (!amount) return "$0.00";
  return amount < 0.01 ? "<$0.01" : `$${amount.toFixed(2)}`;
}

const KINDS_SHOWN = 4;

/** `math $0.33 · word $0.01 · +2 more` — where the money actually went. */
function breakdown(window) {
  if (!window.by_kind.length) return null;
  const shown = window.by_kind.slice(0, KINDS_SHOWN);
  const rest = window.by_kind.length - shown.length;
  const parts = shown.map((b) => `${b.kind} ${usd(b.usd)}`);
  if (rest) parts.push(`+${rest} more`);
  return el("div.faint.small", { text: parts.join(" · ") });
}

/**
 * How much of the month's problems cost nothing, and when you last paid.
 *
 * The two counts come from different places on purpose — problems served from
 * `math_attempt`, problems paid for from the metrics log — because a problem
 * that came from the overnight batch or the curated pool appears in one and
 * not the other. Their difference is the batch's whole job, stated as a number.
 */
function batchLine(spend) {
  if (!spend.problems_served) return null;
  const free = spend.problems_served - spend.problems_paid;
  const when = spend.last_live_generation
    ? `last paid ${spend.last_live_generation.slice(5)}`
    : "none paid for";
  return el(`div.small.${free === spend.problems_served ? "ok" : "faint"}`, {
    text: `${free}/${spend.problems_served} problems free · ${when}`,
    title:
      `over the last ${spend.window_days} days: ${spend.problems_served} problems drawn, `
      + `${spend.problems_paid} of them paid for a live generation. The rest came from the `
      + `overnight batch or the curated pool.`,
  });
}

function spendBlock(spend) {
  // The breakdown is drawn for the widest window only. On a quiet day the
  // other two are empty, and three breakdowns of the same handful of calls is
  // three ways of saying one thing.
  const widest = spend.windows[spend.windows.length - 1];

  return el("div.stat-card", null,
    el("div.row.spread", null,
      el("span.faint", { text: "llm spend (est.)" }),
      spend.tracking_since
        ? el("span.faint.small", { text: `since ${spend.tracking_since.slice(5)}` })
        : null,
    ),
    ...spend.windows.map((w) =>
      el("div.row.spread", null,
        el("span.faint", { text: w.label }),
        el("span", { text: usd(w.usd), title: `${w.calls} call${w.calls === 1 ? "" : "s"}` }),
      ),
    ),
    breakdown(widest),
    batchLine(spend),
    spend.unreadable_lines
      ? el("div.faint.small", {
          text: `${spend.unreadable_lines} log line(s) unreadable — total may be low`,
        })
      : null,
  );
}

// --- the panels -------------------------------------------------------------

export const activity = {
  id: "activity",
  title: "✦ the year so far",
  tab: "home",
  slot: "hero",
  hero: true,

  async render(node) {
    const { cells, labels, weeks, rows } = heatmap();
    const perfect = rows.filter((r) => r.done >= r.total).length;
    // Filled in after the grid is already on screen. The spend read hits the
    // filesystem, and a heatmap that waits on a log file would be a worse
    // heatmap than one whose cost line arrives a beat late.
    const spendSlot = el("div.stat-card", null, el("div.faint", { text: "llm spend — reading…" }));

    node.replaceChildren(
      el("div.stat-row", null,
        el("div.stat-card", null,
          el("div.faint", { text: "100% days in a row" }),
          el("div.rating", { text: String(store.state.perfect_day_streak) }),
          el("div.faint.small", {
            text: `${rows.length} day${rows.length === 1 ? "" : "s"} recorded · ${perfect} perfect`,
          }),
        ),
        spendSlot,
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

    try {
      spendSlot.replaceChildren(spendBlock(await api.get("/api/spend", { date: api.today() })));
    } catch {
      // Swallowed rather than thrown: the harness turns a throw into a retry
      // button over the whole panel, and losing the heatmap because a cost
      // estimate could not be read is the wrong trade.
      spendSlot.replaceChildren(el("div.faint", { text: "llm spend — unavailable" }));
    }
  },
};

export const areas = {
  id: "areas",
  title: "elo by area",
  tab: "home",
  slot: "grid",
  // Two of a kind with everything else on the tab. A wider card was tried and
  // measured: it shortens this one, but only by pushing a third row onto the
  // tab, and the home grid is 460px taller that way. Six cards, two rows of
  // three, is the shortest home tab there is.
  span: 2,

  async render(node) {
    node.replaceChildren(el("div.faint", { text: "loading…" }));
    const data = await history();

    if (failed(data)) {
      node.replaceChildren(failed(data));
      return;
    }
    if (!data.areas.length) {
      node.replaceChildren(el("div.faint", {
        text: "no area has a measured result yet — the breakdown on the math tab is"
          + " your overall rating standing in until one does",
      }));
      return;
    }

    node.replaceChildren(
      el("div.faint.small", {
        text: "the rating each week's topic is actually pitched against. Only measured"
          + " results are plotted — reconstructed rows carry no rating and are not drawn.",
      }),
      ...data.areas.map((series) => {
        const values = series.points.map(([, rating]) => rating);
        const first = series.points[0][0];
        return el("div.chart-block", null,
          el("div.chart-title", null,
            el("span", { text: series.area }),
            el("span.value", { text: values[values.length - 1] }),
          ),
          lineChart(values, { color: "var(--accent)" }),
          el("div.faint.small", {
            text: `${series.events} result${series.events === 1 ? "" : "s"} since ${first}`,
          }),
        );
      }),
    );
  },
};

export const archive = {
  id: "archive",
  title: "day logs",
  tab: "home",
  slot: "grid",
  // Still the tallest card on the tab, and it is what sets the second row's
  // height. Full width does shorten it — but see `areas` above: it costs a
  // whole extra row to do it, which is a worse trade than the slack it saves.
  span: 2,

  async render(node) {
    node.replaceChildren(el("div.faint", { text: "loading…" }));
    const data = await history();

    if (failed(data)) {
      node.replaceChildren(failed(data));
      return;
    }
    if (!data.daylogs.length) {
      node.replaceChildren(el("div.faint", { text: "no day logs yet" }));
      return;
    }

    node.replaceChildren(
      el("div.faint.small", { text: `${data.daylogs.length} entries, newest first` }),
      el("div.archive", null,
        ...data.daylogs.map((entry) =>
          el("div.archive-item", null,
            el("div.value.small", { text: entry.date }),
            el("div.daylog-text", { text: entry.text }),
          ),
        ),
      ),
    );
  },
};
