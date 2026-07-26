import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { parseShare } from "../parsers.js";
import { put, store } from "../store.js";
import { openModal } from "../ui.js";

const ENDPOINT = { wordle: "/api/games/wordle", pokedoku: "/api/games/pokedoku" };

export function openGamesModal(focus = "wordle") {
  openModal(focus, (body) => draw(body, focus));
}

function draw(body, focus) {
  const games = store.state.games;
  const message = el("span.faint");
  const paste = el("textarea", {
    rows: 8,
    placeholder: "Wordle 1,496 4/6\n⬛🟨⬛⬛⬛\n…",
  });

  const logged = [
    games.wordle_today
      ? el("div.dim", {
          text: `wordle #${games.wordle_today.puzzle_no ?? "?"} ${
            games.wordle_today.won ? `${games.wordle_today.guesses}/6` : "X/6"
          } ✓`,
        })
      : null,
    games.pokedoku_today
      ? el("div.dim", { text: `pokedoku ${games.pokedoku_today.correct}/9 ✓` })
      : null,
  ].filter(Boolean);

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
        put("games", await api.post(ENDPOINT[parsed.game], parsed.body));
        draw(body, focus);
      } catch (error) {
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  mount(body,
    el("div.row", null,
      link("https://www.nytimes.com/games/wordle", "play wordle ↗"),
      link("https://pokedoku.com", "play pokedoku ↗"),
    ),
    ...logged,
    el("div.faint", { text: "paste your share text (auto-detects which game):" }),
    paste,
    el("div.row", null, log, message),
  );
  paste.focus();
}
