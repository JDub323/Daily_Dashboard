// The chess panel: a lichess link and three self-report buttons.
//
// The inline board is gone — 1,991 lines of vendored chess.js, a ~130-line
// mount function, the classic-script/ES-module split in index.html, and the
// board CSS with it. The replacement already existed as the PGN-parse-failure
// fallback; it is now the whole feature.
//
// `mistakes` and `hints` left the schema with the board. The puzzle's rating
// did not: it is still the elo opponent.

import * as api from "../api.js";
import { opener } from "../daily.js";
import { el, link, mount } from "../dom.js";
import { put, store } from "../store.js";
import { lastDelta, summaryRow } from "./math.js";

const OUTCOMES = [
  { outcome: "clean", label: "solved clean", cls: "ok" },
  { outcome: "messy", label: "solved w/ mistakes", cls: "" },
  { outcome: "failed", label: "failed", cls: "bad" },
];

export default {
  id: "chess",
  title: "♞ chess puzzle",
  tab: "hobbies",
  slot: "hero",
  hero: true,
  collapsible: true,

  /** Rating and what today's puzzle did to it. See the math panel's. */
  summary() {
    const chess = store.state.chess;
    if (!chess.puzzle_id || chess.status === "open") return `${chess.summary.rating}`;
    const last = chess.summary.history[chess.summary.history.length - 1];
    const delta = last?.delta == null ? "" : ` ${last.delta >= 0 ? "+" : ""}${last.delta}`;
    return `${chess.summary.rating}${delta} · ${chess.puzzle_id}`;
  },

  async render(node) {
    const chess = store.state.chess;
    const head = summaryRow(chess.summary);

    // Fetched at boot by `daily.js`. The gather is guarded on `puzzle_id`
    // being absent, and has to be: `open_chess_attempt` upserts on the date,
    // so a second fetch would replace a puzzle you are part way through with
    // a different one and keep the rating it was opened against.
    if (!chess.puzzle_id) {
      mount(node, head, opener("chess"));
      return;
    }

    const puzzleLine = el("div.faint", null,
      "puzzle ", link(chess.puzzle_url, chess.puzzle_id),
      " ", el("span.tag", { text: `rated ${chess.puzzle_rating}` }),
      chess.themes.length ? ` · ${chess.themes.slice(0, 4).join(" · ")}` : null,
    );

    if (chess.status !== "open") {
      mount(node, head,
        el("div.ok", null, "done for today ✓ ", lastDelta(chess.summary)),
        puzzleLine,
      );
      return;
    }

    const buttons = OUTCOMES.map(({ outcome, label, cls }) =>
      el(`button${cls ? `.${cls}` : ""}`, {
        text: label,
        onclick: async (e) => {
          [...e.target.parentNode.children].forEach((b) => (b.disabled = true));
          put("chess", await api.post("/api/chess/finish", { outcome }));
          await this.render(node);
        },
      }),
    );

    mount(node, head, puzzleLine,
      el("div.row", null, link(chess.puzzle_url, "solve it on lichess ↗", { class: "big" })),
      el("div.faint", { text: "then report how it went:" }),
      el("div.row", null, ...buttons),
    );
  },
};
