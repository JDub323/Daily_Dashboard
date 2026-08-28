// What went in: when, how much, and how processed.
//
// Three fields, and each one earns its keystroke against a different failure.
//
// **Size, not calories.** A calorie number typed from a plate is a guess with
// a lab coat on: the literature on self-reported intake has people out by a
// quarter, consistently, in the direction that flatters them. Three buckets is
// the same guess with its error bars showing, and it is honest about being an
// estimate right up until `health.MEAL_KCAL` turns it back into numbers — in
// one place, written down, where the assumption can be argued with rather than
// smuggled into a total.
//
// **Quality is how processed it was**, 1 = a burger, 5 = chicken and rice and
// something green. One axis, not macros and not a diet score, because it is
// answered standing in a kitchen with one hand, and because it is the axis
// that actually moves: a camp lost on food is usually lost on quality weeks
// before the scale says anything.
//
// **The hour** is the one that makes the log worth keeping past February. Two
// meals a day both eaten after 8pm is a different week from the same food at
// noon, and the difference shows up in the next morning's readiness rather
// than anywhere on this panel.
//
// The pairing this feeds is the honest one: estimated intake against the
// watch's measured expenditure, printed next to what the scale actually did.
// When the estimate and the scale disagree it is the estimate that is wrong —
// see `health.implied_lb_per_week`, which exists to make that comparison
// impossible to avoid noticing.

import { quickLog } from "../quicklog.js";

/**
 * `[key, value, label]`. Sizes go by first letter and there is no collision to
 * resolve — `s`, `m`, `l` mean the same thing here that they mean on the
 * bathroom panel's volume row, which is the point of using them twice.
 */
const SIZE = [
  ["s", "small", "small"],
  ["m", "medium", "medium"],
  ["l", "large", "large"],
];

/** 1–5, and the ends are printed beside the row. A scale whose direction you
 *  have to remember is one you will eventually answer backwards — the same
 *  argument the check-in makes at greater length. */
const QUALITY = [
  ["1", 1, "1"],
  ["2", 2, "2"],
  ["3", 3, "3"],
  ["4", 4, "4"],
  ["5", 5, "5"],
];

/** Two questions, no branch — the whole difference from the bathroom log. */
export const STEPS = [
  { name: "size", hint: "roughly, in calories", options: SIZE },
  { name: "quality", hint: "1 processed · 5 whole food", options: QUALITY },
];

export default quickLog({
  id: "meals",
  title: "meals",
  // The hour is asked first here and nowhere else. Meals are the log most
  // often written down after the fact -- you notice at three that lunch never
  // got entered -- and this is the one of the two with a global key, so the
  // shift keys are also what opens it: `m` `m` is "a meal, two hours ago".
  hourFirst: true,
  endpoint: "/api/meal",
  deleteEndpoint: "/api/meal/delete",
  empty: "nothing logged today",

  steps: STEPS,
  entries: (state) => state.health.meals_today,
  describe: (entry) => `${entry.size} · quality ${entry.quality}`,
});
