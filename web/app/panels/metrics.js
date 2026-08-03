// Trophies & streaks, and what the LLM is costing.
//
// Every streak is computed server-side from the browser's local date, so the
// numbers here are read, not derived.
//
// The spend strip is the first thing in the app that reads metrics.jsonl.
// Every paid call has been appending to that file since the first generation
// and nothing has ever opened it, which made the only recurring cost the app
// has the one number it would not show you.

import * as api from "../api.js";
import { el } from "../dom.js";
import { store } from "../store.js";

const WORDLE_CELL = { "🟩": "g", "🟨": "y", "⬛": "", "⬜": "" };
const POKEDOKU_CELL = (ch) => ("✅🟩".includes(ch) ? "g" : ch === "❌" || ch === "🟥" ? "r" : "w");

function grid(rows, className, cellClass) {
  if (!rows?.length) return null;
  return el(`span.${className}`, null,
    ...rows.flatMap((line) => [...line].map((ch) => el(`div.cell ${cellClass(ch) || ""}`.trim()))),
  );
}

const stat = (label, value) =>
  el("div.row.spread", null, el("span", { text: label }), el("span.gold.big", { text: value }));

// --- spend -----------------------------------------------------------------

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
 * not the other. Their difference is the batch's whole job, stated as a
 * number, and nothing in the UI has ever said it.
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
  const rows = spend.windows.map((w) =>
    el("div.row.spread", null,
      el("span.faint", { text: w.label }),
      el("span", { text: usd(w.usd), title: `${w.calls} call${w.calls === 1 ? "" : "s"}` }),
    ),
  );
  // The breakdown is drawn for the widest window only. On a quiet day the
  // other two are empty, and three breakdowns of the same handful of calls is
  // three ways of saying one thing.
  const widest = spend.windows[spend.windows.length - 1];

  return el("div.divider", null,
    el("div.row.spread", null,
      el("span.faint", { text: "llm spend (est.)" }),
      spend.tracking_since
        ? el("span.faint.small", { text: `since ${spend.tracking_since.slice(5)}` })
        : null,
    ),
    ...rows,
    breakdown(widest),
    batchLine(spend),
    spend.unreadable_lines
      ? el("div.faint.small", {
          text: `${spend.unreadable_lines} log line(s) unreadable — total may be low`,
        })
      : null,
  );
}

export default {
  id: "metrics",
  title: "trophies & streaks",
  col: "right",

  async render(node) {
    const games = store.state.games;
    const wordle = games.wordle_today;
    const pokedoku = games.pokedoku_today;
    // Filled in after the trophies are already on screen. The spend read hits
    // the filesystem, and a panel whose streaks wait on a log file would be a
    // worse panel than one whose cost line arrives a beat late.
    const spendSlot = el("div");

    node.replaceChildren(
      stat("wordle streak", games.wordle_streak),
      stat("pokedoku streak", games.pokedoku_streak),
      stat("100% days streak", store.state.perfect_day_streak),
      el("div.row.spread", null,
        el("span.faint", { text: "wordle avg" }),
        el("span.faint", { text: `${games.wordle_average ?? "—"} / 6` }),
      ),
      el("div.divider", null,
        el("div.faint", { text: "today's results" }),
        wordle
          ? el("div", null,
              grid(wordle.grid, "wgrid", (ch) => WORDLE_CELL[ch] ?? ""),
              el("span.dim", { text: `wordle ${wordle.won ? `${wordle.guesses}/6` : "X/6"}` }),
            )
          : el("div.faint", { text: "wordle — not yet" }),
        pokedoku
          ? el("div", null,
              grid(pokedoku.grid, "pgrid", POKEDOKU_CELL),
              el("span.dim", {
                text: `pokedoku ${pokedoku.correct}/9${pokedoku.score ? ` · ${pokedoku.score}` : ""}`,
              }),
            )
          : el("div.faint", { text: "pokedoku — not yet" }),
      ),
      spendSlot,
    );

    try {
      spendSlot.replaceChildren(spendBlock(await api.get("/api/spend", { date: api.today() })));
    } catch {
      // Swallowed rather than thrown: the panel harness turns a throw into a
      // retry button over the whole panel, and losing the streaks because a
      // cost estimate could not be read is the wrong trade.
      spendSlot.replaceChildren(
        el("div.divider", null, el("div.faint", { text: "llm spend — unavailable" })),
      );
    }
  },
};
