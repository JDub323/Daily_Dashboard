// What you drank, in drinks.
//
// The other half of the hydration reading. `bathroom` says what your body did
// with the water and this says how much of it there was — and neither is worth
// much without the other. A week of dark urine with nothing in here is a week
// you did not write down; a week of dark urine on three litres a day is a salt
// problem rather than a water one, and only one of those is fixed by drinking
// more.
//
// Five kinds, and the cut between them is what each one does to you rather
// than what it is called:
//
//   * **water** and **electrolyte** differ on salt, which is the thing that
//     decides whether the water stays in you. On a cut that is not a detail.
//   * **electrolyte** and **gatorade** differ on sugar and on nothing else —
//     which is why a Gatorade Zero is `electrolyte` here whatever the bottle
//     says. Two hundred calories is either a mid-session necessity or a soft
//     drink, and the log has no way to tell them apart if they share a row.
//   * **coffee** counts as fluid. The diuretic effect is real and smaller than
//     the water it arrives in; it is its own kind because the caffeine is
//     worth counting, not because the water in it is suspect.
//   * **alcohol** counts as none, and is the one kind whose unit is not twelve
//     ounces. See below.
//
// --- the unit ---------------------------------------------------------------
//
// One drink is twelve ounces of it. A can is 1, a 16.9oz bottle is 1.4, a mug
// is about 0.8, and the number is printed on the side of most of them.
//
// So this is the one quick log with a typed answer rather than a row of
// buttons, and the exception is argued for at the top of `quicklog.js`. The
// short version: `size` and `volume` are buckets because nobody knows how big
// their lunch was, and a bucket is an honest way to say so. Here the answer is
// *written on the bottle*, and rounding it to small/medium/large would be
// throwing away accuracy that cost nothing to have.
//
// Alcohol changes the unit, because twelve ounces of spirits is not a quantity
// anybody means. There a drink is a standard drink — a shot, a beer and a
// glass of wine are each 1. That rule, and the sugar rule above, are the two
// things about this panel you cannot guess by looking at it, and they live
// behind the `?` rather than on the card: the whole claim of a quick log is
// that it costs nothing, and a card that opens with a paragraph of
// definitions has already broken it.

import { DRINK_STEPS } from "../logs.js";
import { quickLog } from "../quicklog.js";
import { openDrinkHelp } from "../modals/drinkhelp.js";

/** The questions live in `logs.js`, because the phone at `/m` asks the same
 *  ones. Re-exported under the name the tests and the other panels use. */
export const STEPS = DRINK_STEPS;

/** `1`, `1.5`, `0.5` — never `1.00`, and never `0.30000000000000004`. */
const say = (amount) => String(Math.round(amount * 100) / 100);

export default quickLog({
  id: "drinks",
  title: "drinks",
  endpoint: "/api/drink",
  deleteEndpoint: "/api/drink/delete",
  empty: "nothing logged today",
  help: openDrinkHelp,

  steps: STEPS,
  entries: (state) => state.health.drinks_today,
  describe: (entry) => `${entry.kind} · ${say(entry.amount)}`,
});
