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
    {
      // Beside the word of the day, because they are the same half of the day
      // — one grows the vocabulary, the other stops autocorrect quietly taking
      // the spelling of it away. Different words entirely: the bee draws from
      // `spelling.json` and never touches what the word panel generated.
      id: "spelling",
      label: "spelling bee",
      done: s.spelling.status === "done",
      panel: "spelling",
    },
    // `optional` — still tracked, still gold when done, but out of the
    // denominator. These two are the only things on the list that somebody
    // else schedules: the puzzle exists whether or not it suits your day, and
    // a ring that says 8/10 because the NYT published a Wordle is measuring
    // their calendar rather than yours. The streaks and the average in the
    // games panel are untouched — this changes what a perfect day *requires*,
    // not what gets recorded.
    {
      id: "wordle",
      label: "wordle",
      done: Boolean(s.games.wordle_today),
      panel: "games",
      optional: true,
    },
    {
      id: "pokedoku",
      label: "pokedoku",
      done: Boolean(s.games.pokedoku_today),
      panel: "games",
      optional: true,
    },
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
      // week's required articles plus the facts they produce; on a debate day
      // it is the ballot.
      //
      // `unread_required` is 0 when the overnight task left no list, so a
      // night it did not run degrades the reading day to the facts alone
      // rather than making it impossible.
      id: "debate",
      label: s.debate.reading_day ? "issue reading" : "debate",
      done: s.debate.reading_day
        ? s.debate.unread_required === 0 && s.debate.facts_today.length >= s.debate.min_facts
        : s.debate.session?.status === "scored",
      panel: "debate",
    },
    {
      // Same rule as the theorem chip: done means answered, not merely drawn.
      // Hidden when the deck is empty rather than permanently red — an
      // unfinishable task would put a perfect day out of reach.
      id: "patterns",
      label: "argument pattern",
      done: s.patterns.recalled !== null,
      panel: "patterns",
    },
    {
      id: "paper",
      label: "paper triage",
      done: Boolean(s.papers.surfaced) && s.papers.surfaced.status !== "surfaced",
      panel: "reading",
    },
    { id: "log", label: "day log", done: Boolean(s.daylog), panel: "log" },
  ].filter(
    (task) =>
      (task.id !== "debate" || s.debate.enabled)
      && (task.id !== "patterns" || s.patterns.deck_size > 0)
      && (task.id !== "spelling" || s.spelling.enabled),
  );
}

/** Today's tasks, in chip order. What the cheatsheet counts. */
export function chipTasks() {
  return tasks();
}

/** The ones a perfect day is counted from. Optional chips are shown and
 *  tracked; they are simply not owed. */
export const required = (list) => list.filter((task) => !task.optional);

/**
 * Jump to the next thing you have not done. The ritual key, `n`.
 *
 * Required first, and optional only once nothing is owed — so `n` walks the
 * day you have to do and then offers the games, rather than parking you on
 * Wordle while the math problem is still open.
 *
 * False when there is nothing left at all, so the caller can say so rather
 * than silently doing nothing — which is indistinguishable from a broken
 * binding.
 */
export function openNextUnfinished() {
  const list = tasks();
  const next =
    required(list).find((task) => !task.done) ?? list.find((task) => !task.done);
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
  // The count is over the required ones only. Optional chips still render and
  // still go gold — what they no longer do is stand between you and a perfect
  // day. Rows already in `day_completion` keep the total they were written
  // with, so the heatmap and the streak stay true about the days they describe.
  const counted = required(list);
  const done = counted.filter((task) => task.done).length;
  const extra = list.filter((task) => task.optional && task.done).length;

  if (target) {
    mount(target,
      el("div.chips", null,
        ...list.map((task) =>
          el(`div.chip${task.done ? ".done" : ""}${task.optional ? ".optional" : ""}`, {
            text: task.label,
            title: task.optional ? "optional — not counted toward the day" : "",
            role: "button",
            tabIndex: 0,
            onclick: () => focusPanel(task.panel),
          }),
        ),
      ),
    );
  }

  mount(document.querySelector("#st-completion"),
    el("span.completion-ring", { text: `${done}/${counted.length}` }),
    done === counted.length ? " perfect ✦" : " done",
    // Said rather than hidden: a finished optional chip should visibly count
    // for something, or it reads as a task the ring is ignoring.
    extra ? el("span.faint", { text: ` · +${extra}` }) : null,
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
  const write = completionWrite(store.state, done, counted.length);
  if (!write) return;

  const row = await api.post("/api/completion", write);
  store.state.completion = [...store.state.completion.filter((c) => c.date !== row.date), row];
}
