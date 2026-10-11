// The day's task list — no longer drawn, no longer even defined here, still
// counted.
//
// Chips lost two jobs in the tab rewrite and have now lost the third. They were
// never the keyboard surface (nav.js moves a ring over real panels, and the
// digits belong to the tabs), they never carried a cursor, and the row of them
// that headed the home tab is gone too: it was a second copy of the count the
// statusline carries on every tab, sitting above a heatmap that answers the
// same question better and over a longer window.
//
// What is left is the arithmetic and the consequences — the ring, the gold
// borders, the `n` key and the completion write — over a list that now lives in
// `day.js`. It moved there when the phone grew a screen that asks the same
// question: two devices disagreeing about whether the day is finished is worse
// than either of them being wrong, because there is no way to tell which one to
// believe. The definition is one file; this is one of its two readers.
//
// `renderChips` still runs on every store change rather than only when some
// particular tab is showing. Every pane is mounted at boot — hidden, not absent
// — so the count is correct from whichever tab you happen to be standing on
// when you finish something.

import * as api from "./api.js";
import { dayTasks, optional, required, score } from "./day.js";
import { el, mount } from "./dom.js";
import { focusPanel, markDone } from "./panel.js";
import { completionWrite, paused, store } from "./store.js";

/** Today's tasks, in the order the day asks for them. What the cheatsheet
 *  counts, and what `n` walks. */
export function chipTasks() {
  return dayTasks(store.state);
}

/**
 * Jump to the next thing you have not done. The ritual key, `n`.
 *
 * Required first, and optional only once nothing is owed — so `n` walks the
 * ones you actually owe and only then starts offering points, rather than
 * parking you on Wordle while the math problem is still open.
 *
 * False when there is nothing left at all, so the caller can say so rather
 * than silently doing nothing — which is indistinguishable from a broken
 * binding.
 *
 * Three of these now land somewhere you cannot finish them. The check-in, the
 * meals and the day's training all point at the health panel, which reads and
 * takes nothing — they are answered on the phone. That is the honest version
 * rather than a regression: `n` has always meant "show me the next thing the
 * day is waiting on", and the panel it lands on is the one that will stop
 * saying it is waiting once you have. The alternative was dropping them off
 * the list, which would have quietly shrunk what a day is.
 */
export function openNextUnfinished() {
  const list = chipTasks();
  const next =
    required(list).find((task) => !task.done) ?? list.find((task) => !task.done);
  if (!next) return false;
  focusPanel(next.panel);
  return true;
}

export async function renderChips() {
  // Paused, the day asks for nothing, so there is nothing to count and no row
  // to write. Writing 0/5 would put a failed day on the heatmap for every day
  // the dashboard was switched off; writing nothing leaves those days looking
  // like the ones it was not opened, which is what they are.
  if (paused()) {
    mount(document.querySelector("#st-completion"), el("span.faint", { text: "paused" }));
    return;
  }
  const list = chipTasks();
  // The ring counts the required ones and nothing else. Rows already in
  // `day_completion` keep the total they were written with, so the heatmap and
  // the streak stay true about the days they describe: a day recorded as 8/11
  // is still shown as 8/11, it is simply not the shape today is measured in.
  const counted = required(list);
  const done = counted.filter((task) => task.done).length;
  const { earned, available } = score(list);

  mount(document.querySelector("#st-completion"),
    el("span.completion-ring", { text: `${done}/${counted.length}` }),
    done === counted.length ? " perfect ✦" : " done",
    // Said rather than hidden, and said at zero too once the core is clear: a
    // layer you only hear about once you are already scoring is one you forget
    // exists. `of N` is what stops the number being unreadable — +4 is a good
    // day or a poor one depending entirely on what was on offer.
    earned || done === counted.length
      ? el("span.faint", { text: ` · +${earned} of ${available} pts` })
      : null,
  );

  // The gold border, for every panel the day asks something of. This used to
  // name three of them, which is why the chess puzzle read as finished while
  // the bee beside it read as untouched whichever of the two you had done.
  //
  // Grouped by *panel* rather than by task, and gold only once every task on
  // it is done. The plan panel needed that rule already — a card still open is
  // a panel still owed — and the games panel needs it for the same reason: it
  // draws Wordle and Pokedoku side by side, so a border earned by one of them
  // is a border that lies about the other.
  const byPanel = new Map();
  for (const task of list) {
    byPanel.set(task.panel, (byPanel.get(task.panel) ?? true) && task.done);
  }
  for (const [panel, done] of byPanel) markDone(panel, done);

  // The old client fired a save on every page load: it recomputed `pct` as a
  // float and compared it against a stored float, which differed often enough
  // to write on boot. Compare the integers, and only write on a real change.
  //
  // `completionWrite` also decides which *day* the write belongs to, which is
  // not necessarily today — see its comment.
  const write = completionWrite(store.state, done, counted.length, earned);
  if (!write) return;

  const row = await api.post("/api/completion", write);
  store.state.completion = [...store.state.completion.filter((c) => c.date !== row.date), row];
}
