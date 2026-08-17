// Wordle and Pokedoku, logged from their share text.
//
// This was one modal that opened focused on whichever chip you clicked, and
// the focus existed because 680px only had room to lead with one of them. Both
// games are laid out side by side instead, so the ordering question goes away,
// and the single paste box already auto-detects which share you gave it.
//
// It headed a tab of its own for a while, which was more room than two games
// nobody else schedules deserve. It is half a row on `dailies` now, beside the
// streaks it feeds.

import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { parseShare } from "../parsers.js";
import { put, store } from "../store.js";

const GAMES = {
  wordle: {
    label: "wordle",
    play: ["https://www.nytimes.com/games/wordle", "play wordle ↗"],
    grid: "wgrid",
    summary: (r) => `wordle #${r.puzzle_no ?? "?"} ${r.won ? `${r.guesses}/6` : "X/6"}`,
  },
  pokedoku: {
    label: "pokedoku",
    play: ["https://pokedoku.com", "play pokedoku ↗"],
    grid: "pgrid",
    // Uniqueness is the interesting number and the one that was never stored,
    // so it is shown when there is one rather than folded away.
    summary: (r) => `pokedoku ${r.correct}/9${r.score != null ? ` · ${r.score} uniqueness` : ""}`,
  },
};

const PLACEHOLDER = "Wordle 1,496 4/6\n⬛🟨⬛⬛⬛\n⬛🟩🟨⬛⬛\n🟩🟩🟩🟩🟩\n\n…or a PokéDoku share. Either one.";

/**
 * Emoji tile -> the cell class that draws it.
 *
 * `.wgrid`/`.pgrid`/`.cell` have been in style.css since the rewrite with
 * nothing rendering them — they are the pre-rewrite trophy grids, and their
 * colours are already the ones the rest of the dashboard uses. Drawing the
 * grid from them rather than echoing the pasted emoji means it matches the
 * theme instead of matching whatever font the OS picks for emoji.
 */
const CELL = {
  "🟩": "g", "✅": "g", "✔": "g",
  "🟨": "y",
  "🟥": "r", "❌": "r", "❎": "r",
  "⬛": "w", "⬜": "w",
};

/** The share grid, as coloured cells. Null when there is nothing to draw. */
function gridBlock(rows, cls) {
  if (!rows?.length) return null;
  return el(`div.${cls}`, null,
    ...rows.flatMap((row) =>
      [...row.replace(/[︎️]/g, "")].map((tile) => el(`div.cell.${CELL[tile] ?? "w"}`)),
    ),
  );
}

/** One game's column: how to play it, and today's result if there is one. */
function gameBlock(game, result) {
  const spec = GAMES[game];
  return el("div.game-card", null,
    el("div.row.spread", null,
      el("span.value", { text: spec.label }),
      link(...spec.play),
    ),
    result
      ? el("div", null,
          el("div.dim", { text: `${spec.summary(result)} ✓` }),
          gridBlock(result.grid, spec.grid),
        )
      : el("div.faint", { text: "not logged yet" }),
  );
}

function draw(body) {
  const games = store.state.games;
  const message = el("span.faint");
  const paste = el("textarea", { rows: 6, placeholder: PLACEHOLDER });

  const log = el("button.primary", {
    text: "parse & log",
    onclick: async () => {
      // Parsing happens here, and the result is posted as structured data —
      // the server never sees the share text.
      const parsed = parseShare(paste.value);
      if (!parsed) {
        message.textContent = "couldn't parse that — paste the share text";
        return;
      }
      try {
        put("games", await api.post(`/api/games/${parsed.game}`, parsed.body));
        draw(body);
      } catch (error) {
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  mount(body,
    el("div.game-row", null,
      gameBlock("wordle", games.wordle_today),
      gameBlock("pokedoku", games.pokedoku_today),
    ),
    el("div.faint.divider", { text: "paste your share text (it works out which game):" }),
    paste,
    el("div.row", null, log, message),
  );
}

export default {
  id: "games",
  title: "⬛ daily games",
  tab: "dailies",
  slot: "grid",
  span: 3,

  async render(node) {
    draw(node);
  },
};
