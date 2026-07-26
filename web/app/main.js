// Boot.
//
// One HTTP round trip fills the whole dashboard: GET /api/state?date=<local>
// returns a projection assembled server-side. The panels below read from that
// snapshot and never compute a rating, a score, or a streak themselves.

import * as api from "./api.js";
import { renderChips } from "./chips.js";
import { $ } from "./dom.js";
import { mountAll, register, rerender } from "./panel.js";
import calendar from "./panels/calendar.js";
import chess from "./panels/chess.js";
import elo from "./panels/elo.js";
import health from "./panels/health.js";
import markets from "./panels/markets.js";
import math from "./panels/math.js";
import metrics from "./panels/metrics.js";
import news from "./panels/news.js";
import piano from "./panels/piano.js";
import reading from "./panels/reading.js";
import { loadState, store, subscribe } from "./store.js";
import { initModal, onModalClose, toast } from "./ui.js";

/**
 * Which panels care about which slice of state.
 *
 * This is the whole cross-panel coupling. Panels used to call
 * `rerender("elo")` and `updateChips()` directly after a write, so finishing a
 * puzzle meant knowing which other panels existed.
 */
const WATCHES = {
  elo: ["math", "chess"],
  metrics: ["games"],
};

async function boot() {
  $("#hdr-date").textContent = new Date().toDateString();
  initModal();
  onModalClose(renderChips);

  try {
    store.config = await api.get("/api/config");
    $("#hdr-topic").textContent = `topic: ${store.config.topic}`;
  } catch {
    toast("could not load config — is the server running?");
  }

  await loadState();

  register(math, chess, piano, calendar, markets, news, reading, elo, metrics, health);
  mountAll();

  subscribe((keys) => {
    renderChips();
    for (const [id, watched] of Object.entries(WATCHES)) {
      if (keys.some((key) => watched.includes(key))) rerender(id);
    }
  });
  await renderChips();

  // Topic links are only needed once the math panel has a problem open, so
  // they load in the background rather than blocking first paint.
  api
    .get("/api/topic")
    .then((topic) => {
      store.topic = topic;
      rerender("math");
    })
    .catch(() => {});
}

boot().catch((error) => {
  toast(`boot failed: ${error.message}`);
});
