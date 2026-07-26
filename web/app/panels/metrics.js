// Trophies & streaks. Every streak is computed server-side from the browser's
// local date, so the numbers here are read, not derived.

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

export default {
  id: "metrics",
  title: "trophies & streaks",
  col: "right",

  async render(node) {
    const games = store.state.games;
    const wordle = games.wordle_today;
    const pokedoku = games.pokedoku_today;

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
    );
  },
};
