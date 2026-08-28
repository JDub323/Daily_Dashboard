// The day's task list — no longer drawn, still counted.
//
// Chips lost two jobs in the tab rewrite and have now lost the third. They were
// never the keyboard surface (nav.js moves a ring over real panels, and the
// digits belong to the tabs), they never carried a cursor, and the row of them
// that headed the home tab is gone too: it was a second copy of the count the
// statusline carries on every tab, sitting above a heatmap that answers the
// same question better and over a longer window.
//
// What the list still is, is the definition of a day. `tasks()` is what `n`
// walks, what the cheatsheet counts, and what the completion write is computed
// from — which is why `renderChips` runs on every store change rather than only
// when some particular tab is showing. Every pane is mounted at boot — hidden,
// not absent — so the count is correct from whichever tab you happen to be
// standing on when you finish something.
//
// --- the two layers ---------------------------------------------------------
//
// The list used to be eleven things you owed, and the ring was `done/11`. That
// made every task equally load-bearing, which meant none of them were: a day
// where the math got done and the Pokedoku did not scored the same as its
// opposite. Worse, it made a perfect day rare enough to stop meaning anything,
// and the streak counted off it spent most of its life at zero.
//
// So the day is layered now:
//
//   required   the math tab minus the deck — the problem, the trick, the
//              theorem — plus the morning check-in and three logged meals.
//              Five things. This is the ring, this is what `perfect ✦` means,
//              and this is the only thing the streak is counted off. Small on
//              purpose: a floor you can hold every day is worth more than a
//              ceiling you reach twice a month.
//
//   optional   everything else, worth `points`. Still tracked, still turns its
//              panel gold, still walked by `n` once the core is clear — what it
//              no longer does is stand between you and a perfect day. The
//              points are the reason to do them anyway, and they are written to
//              `day_completion.bonus`, so a heavy day is still legible a month
//              later rather than evaporating at midnight.
//
// Why those five. The three math cells are the only ones on the list that
// compound — the problem moves an elo, and the trick and the theorem are the
// deck that makes the next problem cheaper — so doing them on the days you do
// not feel like it is the whole point of the app. The check-in is here for the
// opposite reason: it is thirty seconds of typing whose value is *entirely* in
// being unbroken, because everything derived from it is a windowed mean, and a
// series answered four mornings a week produces baselines that drift for
// reasons that have nothing to do with training. The meals are here on a
// third argument again, spelled out at the chip: they are the one required
// thing that is a *report* rather than a task, and a report you can always
// file is the safest thing there is to require.
//
// The weights are a judgement call and meant to be edited. The rule they follow
// is minutes-and-attention, not importance: the ballot is an evening, the chess
// puzzle and the word are a real sitting each, and a Wordle is a Wordle.

import * as api from "./api.js";
import { markTabs } from "./carousel.js";
import { el, mount } from "./dom.js";
import { focusPanel, markDone } from "./panel.js";
import { weightToday } from "./panels/checkin.js";
import { completionWrite, store } from "./store.js";

/**
 * How many meals a day the log expects.
 *
 * Three, and it is a claim about the eater rather than a target: he eats
 * three times a day, so a day with two rows in it is a day with a row
 * missing. Named here rather than left as a literal because both the chip
 * and the nudge that chases it have to mean the same number.
 */
export const MEALS_A_DAY = 3;


/**
 * What the training plan puts on the day.
 *
 * Two kinds of thing, and the split is the plan's own apostrophe:
 *
 *   `training`  the blocks the plan asks for outright. Required — it joins the
 *               ring, and a day the plan prescribes something is a `done/5`.
 *               Safe to require precisely *because* the plan is what decides:
 *               a rest day prescribes nothing and the task is simply not
 *               offered, which is the objection that kept session logging off
 *               this list until there was a plan to consult.
 *
 *   a group     a standing commitment worth points — practice. Optional by
 *               construction: it has a `points` field, and "required exactly
 *               when it is worth no points" is still the one definition.
 *
 * Done means *every* prescribed block logged, not any. Otherwise a twelve-minute
 * neck workout would close a Monday that also wanted a lift and half an hour of
 * cardio.
 */
function planTasks(s) {
  const plan = s.health.plan;
  if (!plan) return [];

  const logged = new Set(
    s.health.sessions_today.filter((x) => x.plan_key).map((x) => x.plan_key),
  );
  const out = [];

  // Extras are excluded: sparring is a thing a practice sometimes contained,
  // and a day that could not be finished without it would be unfinishable most
  // of the week.
  const owed = plan.today.filter((b) => !b.optional && !b.extra);
  if (owed.length) {
    out.push({
      id: "training",
      label: owed.length > 1 ? `training (${owed.length})` : "training",
      done: owed.every((b) => logged.has(b.key)),
      panel: "plan",
    });
  }

  for (const group of plan.groups) {
    if (!group.keys.length) continue;
    out.push({
      id: `plan:${group.name}`,
      label: group.name,
      done: group.keys.every((key) => logged.has(key)),
      panel: "plan",
      points: group.points,
      provisional: group.provisional,
    });
  }
  return out;
}

function tasks() {
  const s = store.state;
  // Filtered at the end: a day with nothing drawn has no chip at all, rather
  // than a chip that can never go gold. That was about the denominator — an
  // unfinishable required task put a perfect day out of reach — and it still
  // is for the required ones. For the rest it is about the points: a total
  // whose maximum you cannot reach is a score against a moving target.
  return [
    { id: "math", label: "math problem", done: s.math.status !== "open", panel: "math" },
    {
      // Done means the *marked set* is submitted. The challenge is optional
      // and deliberately not part of this: a chip that only went gold when you
      // had also done the hard one would make it not optional, and a perfect
      // day is counted off exactly these.
      id: "tricks",
      label: "math trick",
      done: s.tricks.status === "done",
      panel: "tricks",
    },
    {
      // Done means answered, not merely drawn. The boot payload never draws,
      // so an unopened day shows `today: null` — which is correctly not done.
      //
      // The recall, not the deck. Curating theorems is something you do when
      // you meet one, not something the day asks for, and the `theorem deck`
      // panel beside this one has never had a chip.
      id: "theorem",
      label: "theorem recall",
      done: s.theorems.recalled !== null,
      panel: "theorem",
    },
    {
      // Weight *and* the questionnaire — the two halves are useless apart. A
      // weight series with no readiness beside it cannot tell a cut from
      // under-recovery, and readiness with no weight cannot tell either from
      // making weight badly.
      //
      // Logging the session is deliberately NOT a chip — some days there is no
      // session, and a task you cannot finish on a rest day would put a perfect
      // day out of reach.
      id: "checkin",
      label: "morning check-in",
      done: weightToday(s) !== null && Boolean(s.health.readiness_today),
      panel: "checkin",
    },
    {
      // Three meals, and required rather than worth points.
      //
      // Every other candidate for the required layer had to argue that it was
      // something you would do anyway; this one is the reverse. Three meals a
      // day is not a habit being built, it is a fact about the day that is
      // going to be true whether or not anybody writes it down -- so the task
      // is not "eat three times", it is "say so", and a task you can always
      // finish is exactly what the required layer is for. Nothing here can put
      // a perfect day out of reach on a bad week the way a prescribed lift
      // could.
      //
      // It earns the place because of what the log is *for*. The panel's whole
      // argument is estimated intake against the watch's measured expenditure,
      // and the honest version of that comparison is impossible on a series
      // with holes in it: two meals logged on a four-meal day does not read as
      // a missing row, it reads as a light day, and `implied_lb_per_week` will
      // quietly believe it.
      id: "meals",
      label: `meals (${Math.min(s.health.meals_today.length, MEALS_A_DAY)}/${MEALS_A_DAY})`,
      done: s.health.meals_today.length >= MEALS_A_DAY,
      panel: "meals",
    },

    // The training plan's own tasks. Placed here rather than in the list below
    // because the required one belongs beside the other required ones — `n`
    // walks required first, and a lift you owe should come before a Wordle.
    ...planTasks(s),

    // --- optional: points, not obligations ---------------------------------
    {
      id: "chess",
      label: "chess puzzle",
      done: s.chess.status !== "open",
      panel: "chess",
      points: 2,
    },
    { id: "word", label: "word of day", done: Boolean(s.word?.grade), panel: "word", points: 2 },
    {
      // Beside the word of the day, because they are the same half of the day
      // — one grows the vocabulary, the other stops autocorrect quietly taking
      // the spelling of it away. Different words entirely: the bee draws from
      // `spelling.json` and never touches what the word panel generated.
      id: "spelling",
      label: "spelling bee",
      done: s.spelling.status === "done",
      panel: "spelling",
      points: 1,
    },
    {
      // Done means graded, not merely written. The write-up is the work, but
      // the grade is what checks it — and it is the one step that can tell you
      // a claim you were pleased with is false, which is the whole point of
      // the exercise.
      //
      // The heaviest thing on the list, and priced like it: an afternoon of
      // reading and a page of prose is the biggest single thing a day here
      // asks for.
      id: "investigate",
      label: "investigation",
      done: Boolean(s.investigate.day?.ballot),
      panel: "investigate",
      points: 3,
    },
    {
      // Same rule as the theorem chip: done means answered, not merely drawn.
      // Hidden when the deck is empty rather than permanently red.
      id: "patterns",
      label: "argument pattern",
      done: s.patterns.recalled !== null,
      panel: "patterns",
      points: 1,
    },
    {
      id: "paper",
      label: "paper triage",
      done: Boolean(s.papers.surfaced) && s.papers.surfaced.status !== "surfaced",
      panel: "reading",
      points: 2,
    },
    {
      // These two are the only things on the list somebody else schedules: the
      // puzzle exists whether or not it suits your day. That was the argument
      // for making them optional first, and it survives the split unchanged.
      id: "wordle",
      label: "wordle",
      done: Boolean(s.games.wordle_today),
      panel: "games",
      points: 1,
    },
    {
      id: "pokedoku",
      label: "pokedoku",
      done: Boolean(s.games.pokedoku_today),
      panel: "games",
      points: 1,
    },
    { id: "log", label: "day log", done: Boolean(s.daylog), panel: "log", points: 1 },
  ].filter(
    (task) =>
      // Nothing drawn means nothing owed: a day with an empty pool, or one
      // where the button has not been pressed, is not a day you have failed.
      (task.id !== "investigate" || Boolean(s.investigate.today))
      && (task.id !== "patterns" || s.patterns.deck_size > 0)
      && (task.id !== "spelling" || s.spelling.enabled)
      && (task.id !== "tricks" || s.tricks.enabled),
  );
}

/** Today's tasks, in chip order. What the cheatsheet counts. */
export function chipTasks() {
  return tasks();
}

/**
 * The ones a perfect day is counted from — the ones with no `points`.
 *
 * A task is required exactly when it is worth no points, which keeps the
 * distinction in one place: there is no second `optional` flag that can fall
 * out of step with the weights.
 */
export const required = (list) => list.filter((task) => !task.points);

/** The other layer. */
export const optional = (list) => list.filter((task) => task.points);

/** Points banked out of the ones on offer today. */
export function score(list) {
  const layer = optional(list);
  return {
    earned: layer.reduce((sum, task) => sum + (task.done ? task.points : 0), 0),
    // A `provisional` task counts toward the total only on a day it was
    // actually done. That is the Saturday practice which will start running at
    // some point and has not yet: leaving it in the denominator would score
    // every Saturday between now and then against three points that cannot be
    // earned, and taking it out entirely would make a Saturday you did go worth
    // nothing. Never a penalty, always a reward.
    available: layer.reduce(
      (sum, task) => sum + (task.provisional && !task.done ? 0 : task.points),
      0,
    ),
  };
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
 */
export function openNextUnfinished() {
  const list = tasks();
  const next =
    required(list).find((task) => !task.done) ?? list.find((task) => !task.done);
  if (!next) return false;
  focusPanel(next.panel);
  return true;
}

export async function renderChips() {
  const list = tasks();
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

  for (const id of ["math", "chess", "investigate"]) {
    const task = list.find((entry) => entry.id === id);
    if (task) markDone(id, task.done);
  }

  // The plan panel holds several tasks, so it goes gold only when the day's
  // training is finished outright — a card still open is a panel still owed.
  const training = list.filter((task) => task.panel === "plan");
  if (training.length) markDone("plan", training.every((task) => task.done));

  // The logging carousel shows one of its four at a time, so the same fact has
  // to reach its tab bar or three quarters of it is unreadable without waiting
  // for the rotation. Which task speaks for which tab is carousel.js's table,
  // not this one's — here it is only the list.
  markTabs(list);

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
