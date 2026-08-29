// The two things about the drinks log you cannot guess by looking at it.
//
// Which is exactly why they are in here and not on the card. There are only
// two — the unit, and the fact that alcohol changes it — and a quick log whose
// panel opens with a paragraph explaining itself is no longer a quick log. The
// same argument the calendar's sheet makes at greater length: this is read
// twice, on the first day and on the day something surprises you, and a
// permanent block of definitions is paid for in height every other day of the
// year.
//
// Short on purpose. Anything that grows past a screen here is a sign the panel
// has learned a rule it should not have.

import { el } from "../dom.js";
import { openModal } from "../ui.js";

/** `[label, what it means]`, in the order the row offers them. */
const KINDS = [
  ["water", "plain."],
  ["electrolyte", "salt, no sugar. a zero-sugar sports drink is this one."],
  ["gatorade", "salt and sugar both. the sugar is the whole difference from the row above."],
  ["coffee", "counts as fluid — the caffeine is why it is kept separate, not the water."],
  ["alcohol", "counts as none of your fluid, and changes what a drink means."],
];

export function openDrinkHelp() {
  openModal("drinks", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "one drink is 12 oz of it — a can. a 16.9oz bottle is 1.4, a mug about 0.8. "
          + "the box takes 1.5, .5 and 1 1/2 alike.",
      }),
      el("div.calhelp-grammar", null,
        ...KINDS.flatMap(([kind, means]) => [
          el("code.calhelp-line", { text: kind }),
          el("span.dim", { text: means }),
        ]),
      ),
      el("div.calhelp-rules", null,
        el("div.calhelp-rule", null,
          el("div.value", { text: "alcohol is counted in standard drinks" }),
          el("div.dim.small", {
            text: "a shot, a beer and a glass of wine are each 1 — the point is the "
              + "alcohol, not the volume, and twelve ounces of spirits is not a "
              + "quantity anybody means.",
          }),
        ),
      ),
    );
  });
}
