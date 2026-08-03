// Elo history. The philosophy — high variance early, stabilising over time —
// is the server's now; this only plots what it returns.

import { chartBlock } from "../charts.js";
import { el } from "../dom.js";
import { openHistoryModal } from "../modals/history.js";
import { store } from "../store.js";

const SEED = { math: 1500, chess: 1400 };
const WINDOW = 40;

/**
 * Reconstructed events carry a null rating: their trajectory was fabricated
 * by the old backfill and is flagged, not rewritten. Dropping them here means
 * the chart plots only ratings that were actually measured.
 */
function series(track) {
  const measured = store.state[track].summary.history
    .map((event) => event.rating_after)
    .filter((rating) => rating != null);
  return [SEED[track], ...measured].slice(-WINDOW);
}

export default {
  id: "elo",
  title: "elo history",
  col: "right",

  async render(node) {
    node.replaceChildren(
      chartBlock("♞ chess", store.state.chess.summary.rating, series("chess"), "var(--gold)"),
      chartBlock("∑ math", store.state.math.summary.rating, series("math"), "var(--accent)"),
      // The way through to everything backward-looking. It lives here because
      // this is the only panel that already looks backwards, and these two
      // pooled lines are the shallow end of what the tables can answer.
      el("div.row.gap", null,
        el("button", { text: "history →", onclick: openHistoryModal }),
        el("span.faint.small", { text: "or press h" }),
      ),
    );
  },
};
