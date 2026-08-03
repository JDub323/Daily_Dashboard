// The daily checklist. Gold = done.
//
// Chips are not a panel — they live in the header, and they re-render off the
// store's change notification rather than being poked by whichever panel
// happened to change something.
//
// They are also the whole keyboard surface. Eight chips is eight mouse trips
// through something you do every morning, so the list below is exported as the
// thing `keys.js` drives: a cursor that moves over it, digits that jump into
// it, and "the next one you have not done" as a first-class destination.

import * as api from "./api.js";
import { $, el, mount } from "./dom.js";
import { openDaylogModal } from "./modals/daylog.js";
import { openGamesModal } from "./modals/games.js";
import { openTheoremModal } from "./modals/theorems.js";
import { openWordModal } from "./modals/word.js";
import { focusPanel, markDone } from "./panel.js";
import { completionWrite, store } from "./store.js";

function tasks() {
  const s = store.state;
  return [
    { id: "math", label: "math problem", done: s.math.status !== "open", open: () => focusPanel("math") },
    { id: "chess", label: "chess puzzle", done: s.chess.status !== "open", open: () => focusPanel("chess") },
    { id: "word", label: "word of day", done: Boolean(s.word?.grade), open: openWordModal },
    { id: "wordle", label: "wordle", done: Boolean(s.games.wordle_today), open: () => openGamesModal("wordle") },
    { id: "pokedoku", label: "pokedoku", done: Boolean(s.games.pokedoku_today), open: () => openGamesModal("pokedoku") },
    {
      // Done means answered, not merely drawn. The boot payload never draws,
      // so an unopened day shows `today: null` — which is correctly not done.
      id: "theorem",
      label: "theorem recall",
      done: s.theorems.recalled !== null,
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

// --- the keyboard cursor ---------------------------------------------------
//
// Held here rather than in `keys.js` because the chips are redrawn on every
// store change, so the highlight has to be reapplied by whatever does the
// drawing. -1 is "no cursor yet", which is the state on load: the first `j`
// should land on the first chip rather than on the second.

let cursor = -1;
let nodes = [];

/** Today's tasks, in chip order. The list `keys.js` indexes into. */
export function chipTasks() {
  return tasks();
}

function paint() {
  nodes.forEach((node, i) => node.classList.toggle("cursor", i === cursor));
  nodes[cursor]?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

/** Move the cursor by `delta`, wrapping. Does not open anything. */
export function moveChipCursor(delta) {
  const list = tasks();
  if (!list.length) return;
  cursor = cursor < 0 ? (delta > 0 ? 0 : list.length - 1) : (cursor + delta + list.length) % list.length;
  paint();
}

/** Open chip `i` and leave the cursor on it. */
export function openChip(i) {
  const list = tasks();
  if (i < 0 || i >= list.length) return;
  cursor = i;
  paint();
  list[i].open();
}

/** Open the chip under the cursor, if there is one. */
export function openCursorChip() {
  if (cursor >= 0) openChip(cursor);
}

/**
 * Jump to the next thing you have not done, wrapping past the cursor.
 *
 * The ritual key. Searching from *after* the cursor rather than from the start
 * means pressing it repeatedly walks the remaining work instead of bouncing
 * off whichever task you happen to be avoiding.
 */
export function openNextUnfinished() {
  const list = tasks();
  for (let n = 1; n <= list.length; n++) {
    const i = (Math.max(cursor, -1) + n) % list.length;
    if (!list[i].done) {
      openChip(i);
      return true;
    }
  }
  return false;
}

export async function renderChips() {
  const list = tasks();
  const done = list.filter((t) => t.done).length;

  nodes = list.map((task, i) =>
    el(`div.chip${task.done ? ".done" : ""}`, {
      text: task.label,
      onclick: () => openChip(i),
      // The digit is the shortcut, shown on the chip so the binding is
      // discoverable without a legend to memorise first.
      dataset: { key: i + 1 <= 9 ? String(i + 1) : "" },
    }),
  );
  if (cursor >= list.length) cursor = list.length - 1;

  mount($("#tasks"), ...nodes);
  paint();

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
  //
  // `completionWrite` also decides which *day* the write belongs to, which is
  // not necessarily today — see its comment.
  const write = completionWrite(store.state, done, list.length);
  if (!write) return;

  const row = await api.post("/api/completion", write);
  store.state.completion = [...store.state.completion.filter((c) => c.date !== row.date), row];
}
