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
//   about — through `focusPanel`, so it crosses sections and lands the focus
//   ring there. A notification that tells you about a thing and then makes you
//   go and find it has spent your attention and given nothing back for it.
//
//   Three of these doors now open onto a panel that takes no input, because
//   the check-in and the meal log moved to the phone. The reminder is still
//   worth having — it is the *noticing* that was the point, and the laptop is
//   where you are when you fail to notice — and the door still lands on the
//   card that will stop saying you are owed once the phone has been used.
//
// --- why not the toast -------------------------------------------------------
//
// `ui.toast` is for the thing you just did: it is centred, it is transient, it
// replaces whatever was there before it, and it is not clickable. All four are
// right for "could not reach Garmin" and wrong for this. These stack, they
// wait, and they are the only clickable chrome in the app that is not part of
// a panel.

import * as api from "./api.js";
import { chipTasks } from "./chips.js";
import { el } from "./dom.js";
import { squareMeals } from "./logs.js";
import { focusPanel } from "./panel.js";
import { splashOpen } from "./splash.js";
import { paused, store } from "./store.js";

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
    // The health panel, which is where the *answer* shows up — the check-in
    // itself is answered on the phone now. Every door on this shelf still
    // opens, and this one opens onto the thing that is waiting rather than
    // onto a form.
    panel: "health",
    // Late enough that a slow morning is not a failure, early enough that the
    // weight is still a fasted one and the sleep is still last night's.
    after: 10,
    every: 2,
    check: ({ tasks }) => (isDone(tasks, "checkin") ? null : "the morning check-in is not done"),
  },
  {
    // The feel pings, at the desk. The laptop pushed one to the phone at a
    // minute it drew this morning; if you are here rather than there, this is
    // the same question, and it settles the moment a feel lands — logged on the
    // phone and brought over by the pulse. No hour of its own: whether a ping
    // is out is the server's to say (`FeelPing.state`), and it only ever says
    // so inside a window.
    id: "feel-ping",
    panel: "health",
    after: 0,
    every: 1,
    check: () => {
      const out = (store.state.health?.feel?.pings ?? []).find((ping) => ping.state === "sent");
      return out ? `how do you feel? — the ${out.name} ping is waiting on the phone` : null;
    },
  },
  // The three meals, each named and each on its own clock.
  //
  // One rule counting "meals so far against meals expected by now" was the
  // first version of this and it was worse in the way that matters: it said
  // "1 meal not logged" at two in the afternoon, which is a number you have to
  // stop and translate into *which* meal, and the translation is the whole
  // content of the reminder. "lunch is not logged" needs no translation.
  //
  // The hours are his: 8am, 2pm, 9pm. They are the times *by which* each meal
  // has normally happened rather than the times he eats, because a reminder
  // that arrives while you are holding the fork is a reminder about nothing.
  //
  // Each fires once and the nth meal of the day clears the nth reminder. That
  // is deliberately not "was a meal logged near this hour" — a late breakfast
  // and an early lunch would then both satisfy lunch and neither satisfy
  // breakfast, and the log has no field saying which meal a row *is*.
  ...[
    ["breakfast", 8],
    ["lunch", 14],
    ["dinner", 21],
  ].map(([meal, at], i) => ({
    id: `meal-${meal}`,
    panel: "health",
    after: at,
    // Once each. Three named reminders that all repeat would be nine a day by
    // bedtime, and the third one is not more persuasive than the first.
    every: 24,
    // Snacks dropped, the same way the chip drops them: a nudge that stops
    // asking about dinner because an apple was logged at four is a nudge that
    // has stopped doing its job.
    check: () =>
      squareMeals(store.state.health.meals_today).length > i ? null : `${meal} is not logged`,
  })),
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
    // The due list is a second round trip. `needs` is what makes it fetched
    // only when it could matter, and — since a fetch that fails hands back
    // nothing rather than an empty day — what makes these two rules sit the
    // whole pass out rather than read "no items" as "nothing is due".
    needs: "todo",
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
    needs: "todo",
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

// --- what this pass should do ------------------------------------------------

/**
 * The rules, read against the day, for a shelf currently showing `showing`.
 *
 * Pure, and separated from the shelf below for the reason every clock in here
 * is a rule and not a `setTimeout`: the interesting cases are the ones a
 * browser will not produce on demand — three in the afternoon with lunch logged
 * a minute ago and the card about it still on screen.
 *
 * Three verdicts:
 *
 *   `raise` — nothing on screen for this rule, and it has something to say.
 *   `update` — a card is up and the sentence has moved on. "3 required left"
 *     becoming "2 required left" is the same reminder, not a new one, so it
 *     must not renumber the shelf or restart the repeat clock.
 *   `settle` — a card is up and the thing it is about is done.
 *
 * That third one is the whole point of this function existing. "Nothing after
 * it is done" was already written at the head of this file and was only ever
 * enforced at the moment of raising: a card that went up at two o'clock stayed
 * up saying lunch was not logged for as long as it took to dismiss it, however
 * long ago lunch had actually been logged. It is a worse failure now than it
 * was, because the logging moved to the phone — the laptop is no longer the
 * thing that finds out, so the card was very often already wrong when it
 * appeared and had no way of ever noticing.
 *
 * The clock is what decides whether a thing is worth *raising*. It has no
 * business deciding whether a sentence already on screen is still true, so a
 * rule with a card up is evaluated whatever the hour and whatever its repeat
 * interval has left to run.
 */
export function verdicts(ctx, showing = []) {
  const out = [];
  for (const rule of RULES) {
    const up = showing.includes(rule.id);
    // Paused, only the todo list may still speak — it is the half of the
    // dashboard that is still on. Anything else already up goes the way a
    // finished one does, since the day it was about has stopped asking.
    if (ctx.paused && rule.panel !== "todo") {
      if (up) out.push({ id: rule.id, do: "settle" });
      continue;
    }
    // A rule whose input did not arrive says nothing this pass, and — the half
    // that is new and easy to get wrong — retracts nothing either. An empty due
    // list and a due list that could not be fetched are the same value, and
    // striking out "2 overdue" because the server was busy would be the
    // reminder lying in the one direction it must not.
    if (rule.needs && ctx[rule.needs] == null) continue;
    if (!up && (ctx.hour < rule.after || !due(rule))) continue;
    const text = rule.check(ctx);
    if (text) out.push({ id: rule.id, do: up ? "update" : "raise", text, panel: rule.target?.(ctx) ?? rule.panel });
    else if (up) out.push({ id: rule.id, do: "settle" });
  }
  return out;
}

/** Whether this pass needs the due list — which is a second round trip, so it
 *  is only made when a todo rule could either fire or be retracted. */
export const wantsTodo = (ctx, showing = []) =>
  RULES.some((rule) => rule.needs === "todo"
    && (showing.includes(rule.id) || (ctx.hour >= rule.after && due(rule))));

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
  setTimeout(() => {
    card.remove();
    // The numbers are positions, so taking one out renumbers the rest. Doing
    // it with the removal rather than before it means `f 2` never addresses a
    // card that is mid-fade.
    renumber();
  }, 200);
}

/**
 * Number the cards, top to bottom, and print the number on each.
 *
 * The digit is what `f 1` addresses, so it has to be *on* the card — a
 * shortcut whose target you have to count out is one you will not use while
 * the thing it is about is still worth doing. Renumbered on every change,
 * because the numbers are positions and not names: the top card is always 1.
 */
function renumber() {
  const cards = [...shelf().children].reverse();
  cards.forEach((card, i) => {
    card.querySelector(".nudge-n").textContent = String(i + 1);
  });
}

/** Take the nth card's door, 1-based from the top. True if there was one. */
export function openNudge(n) {
  const card = [...shelf().children].reverse()[n - 1];
  if (!card) return false;
  card.querySelector(".nudge-body").click();
  return true;
}

/** How many are on screen — what the hint layer prints so the digits it will
 *  accept are the digits you can see. */
export const nudgeCount = () => (stack ? stack.children.length : 0);

function raise({ id, text, panel }) {
  const node = shelf();
  // One card per rule at a time. A second "3 required left" under the first is
  // not more information, it is the same sentence twice.
  node.querySelector(`[data-nudge="${id}"]`)?.remove();
  while (node.children.length >= MAX_ON_SCREEN) node.firstElementChild.remove();

  const card = el("div.nudge", { dataset: { nudge: id } },
    el("button.nudge-body", {
      title: panel ? "go there" : "",
      onclick: () => {
        dismiss(card);
        if (panel) focusPanel(panel);
      },
    },
      el("span.nudge-n"),
      el("span.nudge-t", { text }),
    ),
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
  renumber();
  remember(id);
}

/** The card for a rule, or null. */
const cardFor = (id) => shelf().querySelector(`[data-nudge="${id}"]`);

/** Which rules have a card up — the settled ones excluded, since those are on
 *  their way out and re-evaluating them would only settle them again. */
const showing = () =>
  [...shelf().children]
    .filter((card) => !card.classList.contains("settled"))
    .map((card) => card.dataset.nudge);

/**
 * The sentence moved on; the card did not.
 *
 * In place, and nothing else: no renumber, no `remember`, no re-append. A
 * reminder whose count has gone from three to two is the same reminder, and
 * anything that moves it up the shelf or restarts its clock would be treating
 * progress as a fresh interruption.
 */
function update(id, text) {
  const line = cardFor(id)?.querySelector(".nudge-t");
  if (line && line.textContent !== text) line.textContent = text;
}

/** How long a settled card stays up before it goes. */
const SETTLED_MS = 4000;

/**
 * The thing this card was about has been done. Say so, then go.
 *
 * Struck through with a tick rather than removed on the spot, and this is the
 * one place in the file where something is on screen that is not asking for
 * anything. It is worth the four seconds: a card that vanishes silently is
 * indistinguishable from one you dismissed by accident, and the whole reason
 * this exists is that the thing was very likely done somewhere else — in a
 * kitchen, on a phone — so seeing "lunch is not logged" struck out is the
 * confirmation that the two ends have actually met.
 *
 * The sentence is kept rather than replaced with a new one. It is the sentence
 * you already read, and a line through it is unambiguous in a way that a second
 * sentence in the same box is not.
 */
function settle(id) {
  const card = cardFor(id);
  if (!card || card.classList.contains("settled")) return;
  card.classList.add("settled");
  card.querySelector(".nudge-body")?.setAttribute("title", "done");
  setTimeout(() => dismiss(card), SETTLED_MS);
}

// --- the loop ----------------------------------------------------------------

async function todoItems() {
  try {
    const { items } = await api.get("/api/todo", { date: api.today() });
    return items;
  } catch {
    // A due list that could not be fetched is a reason to say nothing, not a
    // reason to say something wrong. `null` rather than `[]` because those two
    // are now different answers: an empty list retracts a card, and a failed
    // fetch must not — see `verdicts`.
    return null;
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
    const up = showing();
    // Only fetched if a rule that needs it might actually fire *or* has a card
    // that might now be wrong, because this runs every two minutes all day and
    // most of those checks have nothing to say.
    // `null`, not `[]`, when it was not asked for: "we did not ask" and "there
    // is nothing due" are different answers, and only one of them is allowed to
    // take a card down.
    const todo = wantsTodo({ hour }, up) ? await todoItems() : null;
    const ctx = { now, hour, tasks, todo, paused: paused() };

    for (const verdict of verdicts(ctx, up)) {
      if (verdict.do === "raise") raise(verdict);
      else if (verdict.do === "update") update(verdict.id, verdict.text);
      else settle(verdict.id);
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
