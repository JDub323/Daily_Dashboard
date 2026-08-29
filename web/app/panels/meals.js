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

import { MEAL_STEPS } from "../logs.js";
import { quickLog } from "../quicklog.js";

/** The questions live in `logs.js`, because the phone at `/m` asks the same
 *  ones. Re-exported under the name the tests and the other panels use. */
export const STEPS = MEAL_STEPS;

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
