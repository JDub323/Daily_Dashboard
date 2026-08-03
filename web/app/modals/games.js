// Wordle and Pokedoku, logged from their share text.
//
// One modal for both games, opened focused on whichever chip you clicked. It
// used to be one modal that was *about* Wordle whichever chip you clicked: the
// title was the focus but nothing else was, so the placeholder showed a Wordle
// share, the Wordle line was rendered first, and a logged Pokedoku result was
// a bare "pokedoku 8/9 ✓" with no grid — because the grid never parsed, and
// because there was no code here that could have drawn one.

import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { parseShare } from "../parsers.js";
import { put, store } from "../store.js";
import { openModal } from "../ui.js";

const GAMES = {
  wordle: {
    label: "wordle",
    endpoint: "/api/games/wordle",
    play: ["https://www.nytimes.com/games/wordle", "play wordle ↗"],
    grid: "wgrid",
    placeholder: "Wordle 1,496 4/6\n⬛🟨⬛⬛⬛\n⬛🟩🟨⬛⬛\n🟩🟩🟩🟩🟩",
    summary: (r) => `wordle #${r.puzzle_no ?? "?"} ${r.won ? `${r.guesses}/6` : "X/6"}`,
  },
  pokedoku: {
    label: "pokedoku",
    endpoint: "/api/games/pokedoku",
    play: ["https://pokedoku.com", "play pokedoku ↗"],
    grid: "pgrid",
    placeholder: "PokéDoku\nScore: 8/9\nUniqueness: 137\n✅✅✅\n✅✅✅\n✅✅❌",
    // Uniqueness is the interesting number and the one that was never stored,
    // so it is shown when there is one rather than folded away.
    summary: (r) => `pokedoku ${r.correct}/9${r.score != null ? ` · ${r.score} uniqueness` : ""}`,
  },
};

export function openGamesModal(focus = "wordle") {
  const game = GAMES[focus] ? focus : "wordle";
  openModal(GAMES[game].label, (body) => draw(body, game));
}

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

/** One logged result: the headline, then the grid it came with. */
function logged(game, result) {
  if (!result) return null;
  return el("div.gap", null,
    el("div.dim", { text: `${GAMES[game].summary(result)} ✓` }),
    gridBlock(result.grid, GAMES[game].grid),
  );
}

function draw(body, focus) {
  const games = store.state.games;
  const other = focus === "wordle" ? "pokedoku" : "wordle";
  const today = { wordle: games.wordle_today, pokedoku: games.pokedoku_today };

  const message = el("span.faint");
  const paste = el("textarea", { rows: 8, placeholder: GAMES[focus].placeholder });

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
        put("games", await api.post(GAMES[parsed.game].endpoint, parsed.body));
        // Redrawn focused on whichever game was actually pasted, not on
        // whichever chip opened the modal. Pasting a Pokedoku share into a
        // modal opened from the Wordle chip is a normal thing to do, and the
        // result it shows you should be the one you just logged.
        draw(body, parsed.game);
      } catch (error) {
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  mount(body,
    el("div.row", null,
      link(...GAMES[focus].play),
      link(...GAMES[other].play),
    ),
    // The game in focus first, so what you came to do is what you see.
    logged(focus, today[focus]),
    logged(other, today[other]),
    el("div.faint", { text: "paste your share text (auto-detects which game):" }),
    paste,
    el("div.row", null, log, message),
  );
  paste.focus();
}
