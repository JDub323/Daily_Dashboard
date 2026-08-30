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
// cost sit with it.
//
// The two deeper reads that used to sit below it are both gone: the day-log
// archive is a modal off the log cell (`modals/daylogs.js`), and the per-area
// elo chart went with every other rating chart in the app. `/api/history` still
// serves both halves; only the day logs are read now.

import * as api from "../api.js";
import { el } from "../dom.js";
import { rerender } from "../panel.js";
import { store } from "../store.js";

// --- the completion heatmap -------------------------------------------------
//
// Built entirely from `day_completion`, which the boot payload already carries
// in full — so it draws on the first frame and never shows a spinner.

const DAY_MS = 864e5;
const MIN_WEEKS = 12;
const MAX_WEEKS = 53;
// The ceiling a square is allowed to grow to, and the gutter between two of
// them — `--hm-col-max` and `--hm-gap` in style.css, which is where the floor
// lives too. Only the ceiling is needed here: it is what decides how many
// columns it takes to reach the far edge of the card.
const COL_MAX = 20;
const COL_GAP = 3;
// The column count the grid on screen was drawn with, and the panel bodies
// already being watched for a width change — both for `observeWidth` at the
// bottom of the file.
let drawn = 0;
const observed = new WeakSet();

/** Local midnight for an ISO date, via noon — see the piano panel's day index. */
const at = (iso) => new Date(`${iso}T12:00:00`);

/**
 * Five buckets: nothing, then four shades of how much of the day got done.
 *
 * The required half only. Points deliberately do not lift a square: the grid
 * answers "did you keep this up", and a day that skipped the math problem and
 * banked six points elsewhere is not a day you kept it up. The points are in
 * the tooltip, and their run is the line under the streak.
 */
function level(row) {
  if (!row || !row.done) return 0;
  const pct = row.done / row.total;
  if (pct >= 1) return 4;
  if (pct >= 0.7) return 3;
  if (pct >= 0.4) return 2;
  return 1;
}

/** `2026-08-18 — 4/4 done · +6 pts`. */
function cellTitle(iso, row) {
  if (!row) return `${iso} — nothing recorded`;
  const core = `${iso} — ${row.done}/${row.total} done`;
  return row.bonus ? `${core} · +${row.bonus} pts` : core;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * How many week-columns to draw.
 *
 * Sized to the data, then widened to the card. The data half is the original
 * rule: a fresh install with nine days of history should not render a
 * 53-column grid that is 96% empty — that reads as "you have done almost
 * nothing" when what it means is "you started last week". The card half is
 * what stops twelve weeks of squares from sitting in the bottom-left third of
 * a hero: the grid takes as many columns as fit at `COL_MAX`, so it reaches
 * the far edge at a readable square size instead of stretching a dozen of them
 * to 40px each. Either way it grows to a full year and stops, and past that
 * `.hm-scroll` is what handles the overflow.
 *
 * `width` is the panel body's content box, or 0 before it has one — a render
 * on a hidden tab measures nothing, and falls back to the data-sized grid
 * until the tab is looked at.
 */
function weekCount(rows, today, width) {
  const fits = Math.floor((width + COL_GAP) / (COL_MAX + COL_GAP));
  let data = MIN_WEEKS;
  if (rows.length) {
    const earliest = rows.reduce((a, r) => (r.date < a ? r.date : a), rows[0].date);
    const days = Math.round((today - at(earliest)) / DAY_MS);
    data = Math.ceil(days / 7) + 1;
  }
  return Math.max(MIN_WEEKS, Math.min(MAX_WEEKS, Math.max(data, fits)));
}

function heatmap(width) {
  const rows = store.state.completion ?? [];
  const byDate = new Map(rows.map((r) => [r.date, r]));

  const today = at(api.today());
  const weeks = weekCount(rows, today, width);

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
      cells.push(el(`div.hm-cell.hm-${level(row)}`, { title: cellTitle(iso, row) }));
    }
  }

  return { cells, labels, weeks, rows };
}

/**
 * `+14 pts this week · 302 all time` — the optional layer's own scoreboard.
 *
 * A week rather than a day, because that is the window the points are actually
 * for. The statusline already says what today is worth and resets at midnight;
 * what it cannot tell you is whether the extras are still happening at all,
 * which is a question about a run of days.
 *
 * `?? 0` throughout: rows written before the split have no `bonus`, and a
 * NaN here would take the whole hero down over an arithmetic detail.
 */
function pointsLine(rows, today) {
  const total = rows.reduce((sum, row) => sum + (row.bonus ?? 0), 0);
  if (!total) return null;
  const since = new Date(today);
  since.setDate(since.getDate() - 6);
  const week = rows
    .filter((row) => at(row.date) >= since)
    .reduce((sum, row) => sum + (row.bonus ?? 0), 0);
  return el("div.faint.small", {
    text: `+${week} pts this week · ${total} all time`,
    title: "points come from the optional half of the day — the games, the "
      + "word, the ballot, the paper. The ring above counts the required half.",
  });
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

// --- the panel --------------------------------------------------------------

export const activity = {
  id: "activity",
  title: "✦ the year so far",
  tab: "home",
  slot: "hero",
  hero: true,
  // Half the hero, sharing its row with what is owed. Three of six rather than
  // four because the heatmap is the widest thing in here and it is only ~470px
  // at a full year — past that `.hm-scroll` takes over, which is what it is
  // for. See the note beside `calendar, todo, activity` in main.js.
  span: 3,

  async render(node) {
    const { cells, labels, weeks, rows } = heatmap(node.clientWidth);
    drawn = weeks;
    observeWidth(node);
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
          pointsLine(rows, at(api.today())),
        ),
        spendSlot,
      ),
      el("div.hm-scroll", null,
        el("div.hm-wrap", null,
          // The same track sizing the grid below uses, so a month label still
          // starts over the week it names once the columns stretch.
          el("div.hm-months", { style: `grid-template-columns: repeat(${weeks}, minmax(var(--hm-col), 1fr))` },
            ...labels),
          el("div.heatmap", null, ...cells),
        ),
      ),
      el("div.row.gap", null,
        el("span.faint.small", { text: "less" }),
        ...[0, 1, 2, 3, 4].map((n) => el(`div.hm-cell.hm-${n}`)),
        el("span.faint.small", { text: "more · full square = everything required" }),
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

/**
 * Redraw the grid when the card it is in changes width.
 *
 * How many columns fit is a fact about the layout now, and the first render
 * does not know it: the panel is built behind the splash, where the shell is
 * `display: none` and every width is 0 — so without this the grid you see
 * after pressing enter is the fallback one, sized to the data. A window resize
 * is the same problem later on, and one observer answers both.
 *
 * Guarded on the count rather than on the width, so the redraw happens only
 * when the answer actually changed — a rerender writes into the box being
 * observed, and an observer that fired on its own output would not stop.
 */
function observeWidth(node) {
  if (observed.has(node)) return;
  observed.add(node);
  new ResizeObserver(() => {
    const width = node.clientWidth;
    if (!width) return;
    if (weekCount(store.state.completion ?? [], at(api.today()), width) !== drawn) {
      rerender(activity.id);
    }
  }).observe(node);
}
