// The trophy record: streaks and averages.
//
// Today's two grids used to be here as well, and are not any more — the games
// hero above draws them next to the box you paste into, which is where you
// look when you have just logged one. What is left is the part that is about
// every day rather than this one.
//
// The spend strip that briefly lived here moved to the home tab. It was only
// ever next to the streaks because the streaks were the one panel with room.

import { el } from "../dom.js";
import { store } from "../store.js";

const stat = (label, value) =>
  el("div.row.spread", null, el("span", { text: label }), el("span.gold.big", { text: value }));

export default {
  id: "metrics",
  title: "streaks",
  tab: "games",
  slot: "grid",

  async render(node) {
    const games = store.state.games;

    node.replaceChildren(
      stat("wordle streak", games.wordle_streak),
      stat("pokedoku streak", games.pokedoku_streak),
      stat("100% days streak", store.state.perfect_day_streak),
      el("div.row.spread", null,
        el("span.faint", { text: "wordle avg" }),
        el("span.faint", { text: `${games.wordle_average ?? "—"} / 6` }),
      ),
    );
  },
};
