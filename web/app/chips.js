// The daily checklist. Gold = done.
//
// Chips lost two jobs in the tab rewrite and kept the important one. They are
// no longer the keyboard surface — nav.js moves a focus ring over real panels
// now, and the digits belong to the tabs — and they no longer carry a cursor.
// What they still are is the only place the whole day is visible at once,
// which is why they head the home tab: eight tabs is eight things you could be
// forgetting, and this is the row that says which.
//
// Clicking one switches to its tab and lands the ring on its panel, so the
// checklist doubles as the map.
//
// They also own the completion write, which is why this renders on every store
// change rather than only when the home tab is showing. Every pane is mounted
// at boot — hidden, not absent — so the count is correct from whichever tab
// you happen to be standing on when you finish something.

import * as api from "./api.js";
import { el, mount } from "./dom.js";
import { focusPanel, markDone } from "./panel.js";
import { completionWrite, store } from "./store.js";

function tasks() {
  const s = store.state;
  // Filtered at the end: an unconfigured debate has no chip at all, rather
  // than a chip that can never go gold. A task nobody can finish would make a
  // perfect day unreachable, and the streak is counted off exactly that.
  return [
    { id: "math", label: "math problem", done: s.math.status !== "open", panel: "math" },
    { id: "chess", label: "chess puzzle", done: s.chess.status !== "open", panel: "chess" },
    { id: "word", label: "word of day", done: Boolean(s.word?.grade), panel: "word" },
    { id: "wordle", label: "wordle", done: Boolean(s.games.wordle_today), panel: "games" },
    { id: "pokedoku", label: "pokedoku", done: Boolean(s.games.pokedoku_today), panel: "games" },
    {
      // Done means answered, not merely drawn. The boot payload never draws,
      // so an unopened day shows `today: null` — which is correctly not done.
      id: "theorem",
      label: "theorem recall",
      done: s.theorems.recalled !== null,
      panel: "theorem",
    },
    {
      // What "done" means changes with the day, and it has to: a chip that is
      // unreachable on some days would make `perfect_days` (done = total)
      // unreachable with it, and the streak it feeds is the reason the
      // denominator has to stay honest. On a reading day the debt is the
      // reading — facts logged against the week's issue. On a debate day it is
      // the ballot.
      id: "debate",
      label: s.debate.reading_day ? "issue reading" : "debate",
      done: s.debate.reading_day
        ? s.debate.facts_today.length >= s.debate.min_facts
        : s.debate.session?.status === "scored",
      panel: "debate",
    },
    {
      id: "paper",
      label: "paper triage",
      done: Boolean(s.papers.surfaced) && s.papers.surfaced.status !== "surfaced",
      panel: "reading",
    },
    { id: "log", label: "day log", done: Boolean(s.daylog), panel: "log" },
  ].filter((task) => task.id !== "debate" || s.debate.enabled);
}

/** Today's tasks, in chip order. What the cheatsheet counts. */
export function chipTasks() {
  return tasks();
}

/**
 * Jump to the next thing you have not done. The ritual key, `n`.
 *
 * False when there is nothing left, so the caller can say so rather than
 * silently doing nothing — which is indistinguishable from a broken binding.
 */
export function openNextUnfinished() {
  const next = tasks().find((task) => !task.done);
  if (!next) return false;
  focusPanel(next.panel);
  return true;
}

/** The panel wrapper. Registered first on the home tab, so it heads the page. */
export const todayPanel = {
  id: "today",
  title: "today",
  tab: "home",
  slot: "hero",
  hero: true,
  render(node) {
    target = node;
    return renderChips();
  },
};

let target = null;

export async function renderChips() {
  const list = tasks();
  const done = list.filter((task) => task.done).length;

  if (target) {
    mount(target,
      el("div.chips", null,
        ...list.map((task) =>
          el(`div.chip${task.done ? ".done" : ""}`, {
            text: task.label,
            role: "button",
            tabIndex: 0,
            onclick: () => focusPanel(task.panel),
          }),
        ),
      ),
    );
  }

  mount(document.querySelector("#st-completion"),
    el("span.completion-ring", { text: `${done}/${list.length}` }),
    done === list.length ? " perfect ✦" : " done",
  );

  for (const id of ["math", "chess", "debate"]) {
    const task = list.find((entry) => entry.id === id);
    if (task) markDone(id, task.done);
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
