// The daily checklist. Gold = done.
//
// Chips are not a panel — they live in the header, and they re-render off the
// store's change notification rather than being poked by whichever panel
// happened to change something.

import * as api from "./api.js";
import { $, el, mount } from "./dom.js";
import { openDaylogModal } from "./modals/daylog.js";
import { openGamesModal } from "./modals/games.js";
import { openTheoremModal } from "./modals/theorems.js";
import { openWordModal } from "./modals/word.js";
import { focusPanel, markDone } from "./panel.js";
import { store } from "./store.js";

function tasks() {
  const s = store.state;
  const due = s.theorems.due.length;
  return [
    { id: "math", label: "math problem", done: s.math.status !== "open", open: () => focusPanel("math") },
    { id: "chess", label: "chess puzzle", done: s.chess.status !== "open", open: () => focusPanel("chess") },
    { id: "word", label: "word of day", done: Boolean(s.word?.grade), open: openWordModal },
    { id: "wordle", label: "wordle", done: Boolean(s.games.wordle_today), open: () => openGamesModal("wordle") },
    { id: "pokedoku", label: "pokedoku", done: Boolean(s.games.pokedoku_today), open: () => openGamesModal("pokedoku") },
    {
      id: "theorem",
      label: due ? `theorems (${due} due)` : "theorems",
      done: s.theorems.deck_size > 0 && due === 0,
      open: openTheoremModal,
    },
    {
      id: "paper",
      label: "paper triage",
      done: Boolean(s.papers.surfaced) && s.papers.surfaced.status !== "surfaced",
      open: () => focusPanel("reading"),
    },
    { id: "log", label: "day log", done: Boolean(s.daylog), open: openDaylogModal },
  ];
}

export async function renderChips() {
  const list = tasks();
  const done = list.filter((t) => t.done).length;

  mount($("#tasks"),
    ...list.map((task) =>
      el(`div.chip${task.done ? ".done" : ""}`, { text: task.label, onclick: task.open }),
    ),
  );

  mount($("#hdr-completion"),
    el("span.completion-ring", { text: `${done}/${list.length}` }),
    done === list.length ? " — perfect day ✦" : " done today",
  );

  for (const id of ["math", "chess"]) {
    markDone(id, list.find((t) => t.id === id).done);
  }

  // The old client fired a save on every page load: it recomputed `pct` as a
  // float and compared it against a stored float, which differed often enough
  // to write on boot. Compare the integers, and only write on a real change.
  const stored = store.state.completion.find((c) => c.date === api.today());
  if (stored && stored.done === done && stored.total === list.length) return;

  const row = await api.post("/api/completion", { done, total: list.length });
  store.state.completion = [...store.state.completion.filter((c) => c.date !== row.date), row];
}
