// Elo history. The philosophy — high variance early, stabilising over time —
// is the server's; this only plots what it returns.
//
// One panel per track now, each on the tab of the thing it rates, rather than
// one card holding both. Two lines side by side invited a comparison that does
// not exist: the math and chess ratings are different scales against different
// opponents, and the only reason they were ever adjacent is that they were the
// two numbers the app happened to keep.
//
// The way through to the full retrospective is gone from here because the
// retrospective is the home tab now — press 1.

import { chartBlock } from "../charts.js";
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

function eloPanel({ track, tab, title, label, color }) {
  return {
    id: `elo-${track}`,
    title,
    tab,
    slot: "grid",

    async render(node) {
      node.replaceChildren(
        chartBlock(label, store.state[track].summary.rating, series(track), color),
      );
    },
  };
}

export const mathElo = eloPanel({
  track: "math",
  tab: "math",
  title: "rating history",
  label: "∑ math",
  color: "var(--accent)",
});

export const chessElo = eloPanel({
  track: "chess",
  tab: "hobbies",
  title: "rating history",
  label: "♞ chess",
  color: "var(--gold)",
});
