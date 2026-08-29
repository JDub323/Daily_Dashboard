// What came out, and what it says about the week.
//
// Two scales, both shorter than the published ones they come from, and the
// shortening is the design rather than a compromise on it. Bristol has seven
// stool types and the clinical urine charts run to eight shades; both are
// instruments for describing a symptom once, in a room, with the chart on the
// wall. This is answered four to eight times a day for six months from memory,
// and the seven-point version of it gets answered "4 or 5, call it 4" — which
// is not a finer measurement, it is a coarse one with a decimal place bolted
// on. Three answers and four answers are what can be given honestly in one
// keystroke, and one keystroke is the whole budget.
//
// What this is for, in a tab whose stated job is three questions — am I
// recovering, am I training enough, which way is my weight going:
//
//   * **urine colour is the hydration reading that is actually yours.** The
//     watch counts the water you remembered to log; this counts what your body
//     did with it. In a camp that ends with a weigh-in, walking around three
//     pounds down on fluid and calling it progress is the specific mistake
//     worth catching, and it is invisible on a scale.
//   * **stool consistency is the diet's receipt.** A week of liquid days
//     during a hard block is either the food or the stress, and both of those
//     are decisions you can make differently tomorrow.
//
// Neither is allowed to speak on a single reading; see `health.py`, where
// every one of these gates on a run of days *and* on evidence that the log was
// being kept at all. A day with nothing in it means you forgot, not that
// nothing happened, and a panel that cannot tell those apart is a panel that
// warns you about your Sundays.

import { BATHROOM_STEPS } from "../logs.js";
import { quickLog } from "../quicklog.js";

/**
 * The questions live in `logs.js`, because the phone at `/m` asks the same
 * ones. Re-exported under the name the tests and the other panels use — and
 * it is still exported so the branch can be tested without a DOM: a step whose
 * two options share a key is a button you can never reach from the keyboard,
 * and that is a bug you would find months later while standing in a bathroom.
 */
export const STEPS = BATHROOM_STEPS;

export default quickLog({
  id: "bathroom",
  title: "in and out",
  endpoint: "/api/bathroom",
  deleteEndpoint: "/api/bathroom/delete",
  empty: "nothing logged today",

  steps: STEPS,
  entries: (state) => state.health.bathroom_today,
  // The one field the kind carries, whichever it is — the row is `14:00
  // urine · darker · large`, and the model guarantees exactly one of the two
  // is present, so the `??` cannot silently print the wrong one.
  describe: (entry) => `${entry.kind} · ${entry.solidity ?? entry.color} · ${entry.volume}`,
});
