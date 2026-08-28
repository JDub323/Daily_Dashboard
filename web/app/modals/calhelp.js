// What the box does with a line, in the order it does it.
//
// The grammar is small and almost entirely guessable, which is exactly why it
// needed writing down somewhere: a parser you can guess is one you never find
// the edges of, and the edges here are load-bearing. Two of them in
// particular — that a todo is decided by which calendar it lands on and by
// nothing else, and that a todo with no day typed in it is *refused* — are
// rules you would otherwise learn by having a piece of homework quietly not
// appear on the list.
//
// A modal rather than a line under the box, for the reason the cheatsheet is
// one: this is read twice, on the first day and on the day something behaves
// unexpectedly, and a permanent paragraph of instructions above a text box is
// a paragraph you stop seeing in a week and never stop paying for in height.
//
// The alias table is deliberately *not* duplicated here. It has its own `?`
// beside the box because it answers a different question ("what did I call
// that course") at a different moment — while looking at an empty box, mid-
// sentence — and one that changes once a semester rather than never.

import * as api from "../api.js";
import { el } from "../dom.js";
import { openModal } from "../ui.js";

/** `[what you type, what it does]`, in the order a line is read. */
const GRAMMAR = [
  ["dinner with Anna friday 6pm", "a day and a time — an event an hour long"],
  ["move to ND 8am-10am wednesday", "an explicit range sets the length"],
  ["real analysis test on wed from 9:25-10:30", "24h or am/pm, `-` or `to`"],
  ["bengal bouts saturday", "no time found — an all-day event"],
  ["pset 4 due tomorrow", "today · tomorrow · a weekday · 3/14 · march 14"],
  ["every tuesday: reading", "twelve separate events, one per week"],
  ["every other tue: seminar", "the same, fortnightly"],
];

const RULES = [
  [
    "one line each",
    "the box takes as many lines as you paste. ctrl+enter writes all of them, "
    + "and the preview under it is the argv that is about to run — not a guess "
    + "at what google will make of it.",
  ],
  [
    "the calendar decides todo or event",
    "there is no sigil and no mode. a line written to Homework or Tests becomes "
    + "a tracked item with a checkbox on the due list; a line written anywhere "
    + "else is an ordinary event and never appears there.",
  ],
  [
    "auto is not the model",
    "`auto` is the subject's own calendar, then the tests rule, then Homework — "
    + "because nineteen dated lines in twenty are work. the trained classifier "
    + "is `learned`, one tab away in the picker. picking one by hand is also "
    + "the training signal: the same phrasing stops being wrong.",
  ],
  [
    "a todo must name its day",
    "an event with no day lands today, which is one drag to fix. a due date "
    + "nobody typed is a lie about when work is owed, so those lines are "
    + "refused with the reason printed under the box.",
  ],
  [
    "aliases are whole words",
    "`os` expands in “new os homework” and not in “buy banjos”. the `aliases` "
    + "button beside the box lists them.",
  ],
  [
    "ticking deletes the event",
    "finishing an item removes it from google as well. when that call fails the "
    + "item stays finished and says the event is still standing — an "
    + "unreachable google is not a reason to refuse to mark homework done.",
  ],
];

export function openCalendarHelp() {
  openModal("the box", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "one grammar, one place to type — the week above and the due list "
          + "below are both filled from it.",
      }),
      el("div.calhelp-grammar", null,
        ...GRAMMAR.flatMap(([line, means]) => [
          el("code.calhelp-line", { text: line }),
          el("span.dim", { text: means }),
        ]),
      ),
      el("div.calhelp-rules", null,
        ...RULES.map(([title, text]) =>
          el("div.calhelp-rule", null,
            el("div.value", { text: title }),
            el("div.dim.small", { text }),
          ),
        ),
      ),
      // Last, and only when there is one: the horizon is the single number in
      // here that is configuration rather than grammar, and reading it off the
      // server beats printing a 12 that someone has since changed.
      el("div.faint.small.gap", { id: "calhelp-recur" }),
    );

    api
      .get("/api/todo", { date: api.today() })
      .then(({ recur_weeks }) => {
        const slot = body.querySelector("#calhelp-recur");
        if (slot && recur_weeks) {
          slot.textContent =
            `an unbounded “every tuesday” runs ${recur_weeks} weeks · [todo].recur_weeks`;
        }
      })
      .catch(() => {});
  });
}
