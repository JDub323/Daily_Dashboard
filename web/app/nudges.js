// The things the day is still waiting on, said out loud.
//
// Everything here was already knowable from the screen: the statusline carries
// the ring, the chips define the day, the due list is a panel on home. What
// none of that does is *arrive*. A dashboard left open on a second monitor is
// read when you look at it, and the failure this exists for is the one where
// you did not look at it — the check-in that was going to be done in a minute
// at nine and was still not done at two, the required half of the day noticed
// at ten past midnight, the meal nobody wrote down.
//
// --- the rules of a nudge ----------------------------------------------------
//
// A notification you learn to dismiss is worse than none, because dismissing it
// is the habit you build instead of the one it was for. So:
//
//   **Nothing before it is late.** Every rule below has a clock on it, and the
//   clock is the point: "the morning check-in is not done" is not news at
//   08:30, it is news at 11:00. A nudge that fires while the thing is still
//   ordinarily undone is a nudge about nothing.
//
//   **Nothing twice in a row.** Each rule carries its own repeat interval, and
//   it is measured in hours rather than minutes. The check-in gets asked about
//   three or four times across a morning, not twelve.
//
//   **Nothing after it is done.** Obvious, and worth saying because it is the
//   whole reason these are computed from `chipTasks()` rather than scheduled:
//   the same list that decides what a perfect day is decides what is worth
//   interrupting for, so the two can never disagree about whether the maths is
//   finished.
//
//   **Every one of them is a door.** Clicking a nudge goes to the panel it is
//   about — through `focusPanel`, so it crosses sections, stops the logging
//   carousel on the right card, and lands the focus ring there. A notification
//   that tells you about a thing and then makes you go and find it has spent
//   your attention and given nothing back for it.
//
// --- why not the toast -------------------------------------------------------
//
// `ui.toast` is for the thing you just did: it is centred, it is transient, it
// replaces whatever was there before it, and it is not clickable. All four are
// right for "could not reach Garmin" and wrong for this. These stack, they
// wait, and they are the only clickable chrome in the app that is not part of
// a panel.

import * as api from "./api.js";
import { MEALS_A_DAY, chipTasks } from "./chips.js";
import { el } from "./dom.js";
import { focusPanel } from "./panel.js";
import { splashOpen } from "./splash.js";
import { store } from "./store.js";

/** How often the rules are evaluated. Not how often they fire — every rule
 *  has its own repeat interval, and most of them are hours. */
const CHECK_MS = 120_000;

/** Up to this many on screen at once. Past three it is a wall rather than a
 *  reminder, and the oldest is the one that has already been ignored. */
const MAX_ON_SCREEN = 3;

// --- when a rule may speak again --------------------------------------------
//
// Kept in localStorage, keyed by the day, so a reload does not start the
// morning over. That is the whole reason it is not a module variable: this is
// a tab that gets refreshed, and being nagged again about the check-in because
// you pressed F5 is precisely the thing that teaches you to ignore these.
//
// Every read and write is wrapped, because localStorage throws rather than
// returning null in a browser set to block site data, and a dashboard that
// fails to boot over a reminder would be a poor trade.

const KEY = "nudges";

function memory() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    // Keyed by date and dropped wholesale when it turns: yesterday's timings
    // say nothing about today, and this way nothing has to prune.
    return raw.date === api.today() ? raw : { date: api.today(), at: {} };
  } catch {
    return { date: api.today(), at: {} };
  }
}

function remember(id) {
  try {
    const state = memory();
    state.at[id] = Date.now();
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // A browser that will not store this still gets the nudge; it just gets it
    // again after a reload. Degrading to "slightly too keen" is the right way
    // for this to fail.
  }
}

const lastShown = (id) => memory().at[id] ?? 0;

// --- the rules ---------------------------------------------------------------

/**
 * `after` is the hour it becomes worth saying, `every` the hours before it may
 * be said again, `panel` where clicking it goes, and `check(ctx)` the line —
 * or null when there is nothing to report.
 *
 * The hours are local and belong to the browser, like every other clock in
 * this app: [Invariant 1] is why the server is never asked what time it is.
 */
const RULES = [
  {
    id: "checkin",
    panel: "checkin",
    // Late enough that a slow morning is not a failure, early enough that the
    // weight is still a fasted one and the sleep is still last night's.
    after: 10,
    every: 2,
    check: ({ tasks }) => (isDone(tasks, "checkin") ? null : "the morning check-in is not done"),
  },
  {
    id: "meals",
    panel: "meals",
    // Not one window but three, so "you are behind" can be said at lunchtime
    // rather than only at the end of the day when nothing can be done about
    // it. Expected-by-now against logged-so-far.
    after: 11,
    every: 3,
    check: ({ hour }) => {
      const logged = store.state.health.meals_today.length;
      const expected = hour >= 21 ? 3 : hour >= 15 ? 2 : 1;
      if (logged >= Math.min(expected, MEALS_A_DAY)) return null;
      const missing = Math.min(expected, MEALS_A_DAY) - logged;
      return `${missing} meal${missing === 1 ? "" : "s"} not logged — ${logged}/${MEALS_A_DAY} today`;
    },
  },
  {
    id: "required",
    // No fixed panel: it goes to whichever required thing is still open, which
    // is the one you would have had to go and find.
    panel: null,
    // The end of the day, and not before. These are the cells that compound,
    // so being reminded of them at four in the afternoon is being interrupted
    // about work you had every intention of doing after dinner.
    after: 20,
    every: 1.5,
    check: ({ tasks }) => {
      const open = tasks.filter((t) => !t.points && !t.done);
      if (!open.length) return null;
      return `${open.length} required left today — ${open.map((t) => t.label).join(", ")}`;
    },
    // The first unfinished required task, so the click lands somewhere useful
    // rather than on the first of five.
    target: ({ tasks }) => tasks.find((t) => !t.points && !t.done)?.panel ?? null,
  },
  {
    id: "due-today",
    panel: "todo",
    // Once the day has started, and only once: what is due today is a thing to
    // be told at the beginning of a day, not reminded of through it.
    after: 8,
    every: 24,
    check: ({ todo }) => {
      const due = todo.filter((item) => !item.done && item.due_date === api.today());
      if (!due.length) return null;
      return `due today: ${due.map((item) => item.title).join(", ")}`;
    },
  },
  {
    id: "overdue",
    panel: "todo",
    after: 8,
    // Twice a day. Overdue work does not become more urgent by being mentioned
    // more often, and it is the one thing here that cannot be finished by
    // pressing a key.
    every: 12,
    check: ({ todo }) => {
      const late = todo.filter((item) => !item.done && item.due_date < api.today());
      if (!late.length) return null;
      return `${late.length} overdue: ${late.map((item) => item.title).join(", ")}`;
    },
  },
];

const isDone = (tasks, id) => Boolean(tasks.find((t) => t.id === id)?.done);

// --- the stack ---------------------------------------------------------------

let stack = null;

function shelf() {
  stack ??= (() => {
    const node = el("div.nudges", { id: "nudges", role: "status", "aria-live": "polite" });
    document.body.append(node);
    return node;
  })();
  return stack;
}

function dismiss(card) {
  card.classList.add("out");
  // Removed after the transition rather than on it, so a stack of three does
  // not jump when the middle one goes.
  setTimeout(() => card.remove(), 200);
}

function raise({ id, text, panel }) {
  const node = shelf();
  // One card per rule at a time. A second "3 required left" under the first is
  // not more information, it is the same sentence twice.
  node.querySelector(`[data-nudge="${id}"]`)?.remove();
  while (node.children.length >= MAX_ON_SCREEN) node.firstElementChild.remove();

  const card = el("div.nudge", { dataset: { nudge: id } },
    el("button.nudge-body", {
      text,
      title: panel ? "go there" : "",
      onclick: () => {
        dismiss(card);
        if (panel) focusPanel(panel);
      },
    }),
    el("button.nudge-x", {
      text: "✕",
      title: "dismiss",
      "aria-label": "dismiss",
      onclick: (e) => {
        e.stopPropagation();
        dismiss(card);
      },
    }),
  );
  node.append(card);
  remember(id);
}

// --- the loop ----------------------------------------------------------------

async function todoItems() {
  try {
    const { items } = await api.get("/api/todo", { date: api.today() });
    return items;
  } catch {
    // A due list that could not be fetched is a reason to say nothing, not a
    // reason to say something wrong.
    return [];
  }
}

let checking = false;

/**
 * Evaluate every rule once.
 *
 * Exported so the day rollover can run it directly: a new day resets the
 * memory above by changing its date key, and the first check of it should not
 * wait two minutes.
 */
export async function checkNudges() {
  // Nothing over the title screen. It is a cover, and a notification sliding
  // out from under it is the one thing that would break it.
  if (checking || splashOpen() || !store.state) return;
  checking = true;
  try {
    const now = new Date();
    const hour = now.getHours() + now.getMinutes() / 60;
    const tasks = chipTasks();
    // Only fetched if a rule that needs it might actually fire, because this
    // runs every two minutes all day and most of those checks have nothing to
    // say.
    const wantsTodo = RULES.some(
      (rule) => rule.panel === "todo" && hour >= rule.after && due(rule),
    );
    const todo = wantsTodo ? await todoItems() : [];
    const ctx = { now, hour, tasks, todo };

    for (const rule of RULES) {
      if (hour < rule.after || !due(rule)) continue;
      const text = rule.check(ctx);
      if (!text) continue;
      raise({ id: rule.id, text, panel: rule.target?.(ctx) ?? rule.panel });
    }
  } finally {
    checking = false;
  }
}

const due = (rule) => Date.now() - lastShown(rule.id) >= rule.every * 3600_000;

export function initNudges() {
  // The first pass is deliberately not immediate. Boot has just finished, the
  // title screen is usually still up, and the dailies are still arriving — a
  // nudge about an unfinished thing that is in the middle of being fetched is
  // a nudge that is wrong.
  setTimeout(checkNudges, 20_000);
  setInterval(checkNudges, CHECK_MS);
  // Waking a laptop at four in the afternoon should not wait two minutes to
  // notice that most of the day happened without it.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkNudges();
  });
}

/** Take everything down — what a day rollover does before re-checking. */
export function clearNudges() {
  if (stack) stack.replaceChildren();
}
