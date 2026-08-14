// Keyboard bindings, so the mouse is optional rather than assumed.
//
// One listener on the document: every binding here is global by nature, and
// the panels are rebuilt on every store change, so anything attached to them
// would have to be reattached each time.
//
// Three gates matter more than the bindings do.
//
//   - Off while a modal is open. You are doing the task, and `j` belongs in
//     the sentence you are typing.
//   - Off inside any editable element — with Esc and Tab as the deliberate
//     exceptions, since they are the way out of one and the way across to the
//     next one.
//   - Off whenever Ctrl/Cmd is held, so Cmd-1 still switches *browser* tabs. A
//     shortcut layer that eats the browser's own keys is worse than no
//     shortcut layer.
//
// Alt is the exception to the third gate, and only for h/j/k/l: moving between
// panels is the one thing that has to work from inside a mode you did not mean
// to enter, including with a text box holding the keyboard. Nothing in a
// browser is bound to Alt-hjkl, so the cost is a menu shortcut nobody presses.
//
// Shift is not in that list either, because Shift-Tab is a binding.

import { chipTasks, openNextUnfinished, required } from "./chips.js";
import { $, el } from "./dom.js";
import * as nav from "./nav.js";
import { TABS, activateNth, cycle } from "./tabs.js";
import { openModal, toast } from "./ui.js";

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTyping(target) {
  return Boolean(target) && (EDITABLE.has(target.tagName) || target.isContentEditable);
}

const modalOpen = () => !$("#modal-overlay").hidden;

/** `gg` is the only two-key sequence, so the pending state is one flag. */
let pendingG = false;
let gTimer = null;

function armG() {
  pendingG = true;
  clearTimeout(gTimer);
  gTimer = setTimeout(() => {
    pendingG = false;
  }, 700);
}

/**
 * The bindings, as data — the cheatsheet is generated from the same list that
 * dispatches. A legend maintained separately from the handler is a legend that
 * is wrong by the second change.
 */
const BINDINGS = [
  {
    keys: ["tab", "shift-tab"],
    hint: "next / previous section",
    match: (e) => e.key === "Tab",
    run: (e) => cycle(e.shiftKey ? -1 : 1),
  },
  {
    keys: ["1", "…", String(TABS.length)],
    hint: "jump straight to a section",
    match: (e) => /^[1-9]$/.test(e.key) && Number(e.key) <= TABS.length,
    run: (e) => activateNth(Number(e.key)),
  },
  {
    keys: ["h", "j", "k", "l"],
    hint: "move the focus ring — between panels, or between links inside one",
    match: (e) => ["h", "j", "k", "l"].includes(e.key),
    run: (e) => nav.move(e.key),
  },
  {
    keys: ["alt", "+", "hjkl"],
    hint: "move between panels from any mode — even with a text box focused",
    // Handled before every gate below; listed here so the sheet is the map.
    match: () => false,
    run: () => {},
  },
  {
    keys: ["enter"],
    hint: "press the panel's only button, or step inside it · shift-enter always steps inside",
    match: (e) => e.key === "Enter",
    run: (e) => nav.enter({ descend: e.shiftKey }),
  },
  {
    keys: ["i"],
    hint: "type in this panel's first text box · tab walks the rest of them",
    match: (e) => e.key === "i",
    run: () => nav.insert(),
  },
  {
    keys: ["f"],
    hint: "letter every link in this panel, then press its letter to open it",
    match: (e) => e.key === "f",
    run: () => nav.hint(),
  },
  {
    keys: ["gg", "G"],
    hint: "first / last panel of the section",
    match: (e) => e.key === "g" || e.key === "G",
    run: (e) => {
      if (e.key === "G") {
        nav.edge("last");
        return;
      }
      if (pendingG) {
        pendingG = false;
        nav.edge("first");
        return;
      }
      armG();
    },
  },
  {
    keys: ["n"],
    hint: "open the next thing you have not done today",
    match: (e) => e.key === "n",
    run: () => {
      if (!openNextUnfinished()) toast("everything is done — nice day ✦", "info");
    },
  },
  {
    keys: ["?"],
    hint: "this list",
    // Both spellings. A US layout reports `?` for shift-/, but the shifted
    // character is layout-dependent and some environments report the unshifted
    // key with `shiftKey` set instead — including headless automation.
    // Matching one of them works on a keyboard and not on another, which is
    // the worst of the three outcomes.
    match: (e) => e.key === "?" || (e.key === "/" && e.shiftKey),
    run: () => openHelp(),
  },
  {
    keys: ["esc"],
    hint: "back to the highlighted panel, in one press · also closes a modal",
    match: (e) => e.key === "Escape",
    run: () => nav.escape(),
  },
];

/**
 * Alt-hjkl, by physical key.
 *
 * `e.key` is the wrong side of the keyboard layout for this one: on macOS Alt
 * is the compose modifier, so Alt-j arrives as "∆" and Alt-h as "˙". `e.code`
 * is the key you actually pressed.
 */
const ALT_MOVE = { KeyH: "h", KeyJ: "j", KeyK: "k", KeyL: "l" };

function openHelp() {
  // The required ones, matching the statusline. Two surfaces disagreeing about
  // how much of the day is left is worse than either number alone.
  const tasks = required(chipTasks());
  const done = tasks.filter((task) => task.done).length;
  openModal("keyboard", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "active on the dashboard itself — not while a modal is open. "
          + "in a text box only esc, tab and alt-hjkl still answer.",
      }),
      el("div.keys", null,
        ...BINDINGS.flatMap((binding) => [
          el("div.keycombo", null, ...binding.keys.map((key) => el("kbd", { text: key }))),
          el("div.dim", { text: binding.hint }),
        ]),
      ),
      el("div.faint.small.gap", {
        text: `${done}/${tasks.length} done today · each section's digit is printed on its tab`,
      }),
    );
  });
}

export function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey) return;

    // Alt-hjkl, ahead of every gate. This is the key that has to work when you
    // are somewhere you did not mean to be — three fields deep in a form, or
    // in item mode on the wrong card.
    if (e.altKey) {
      const key = ALT_MOVE[e.code];
      if (!key || modalOpen()) return;
      // The blur is what makes it a *move* and not a move-while-still-typing:
      // nav returns to panel mode on the card it lands on.
      if (isTyping(e.target)) e.target.blur();
      e.preventDefault();
      nav.move(key, { toPanels: true });
      return;
    }

    // Esc next, and ahead of both remaining gates: it is the way out of the
    // text box, and ui.js owns it while a modal is up.
    if (e.key === "Escape") {
      if (modalOpen()) return;
      nav.escape();
      e.preventDefault();
      return;
    }

    if (modalOpen()) return;

    // Inside a text box the keyboard belongs to the box, with one exception:
    // Tab walks that panel's other boxes. See nav.cycleField.
    if (isTyping(e.target)) {
      if (e.key === "Tab" && nav.cycleField(e.shiftKey ? -1 : 1)) e.preventDefault();
      return;
    }

    // The hint layer eats the next key whatever it is — that is what buys it
    // the whole alphabet while `g`, `h` and `n` keep their normal meanings.
    if (nav.inHints()) {
      e.preventDefault();
      if (/^[a-z]$/i.test(e.key)) nav.takeHint(e.key.toLowerCase());
      else nav.exitHints();
      return;
    }

    const binding = BINDINGS.find((b) => b.match(e));
    if (!binding) return;
    // Tab is the one that would otherwise do something: without this the
    // browser walks its own focus order underneath the tab switch.
    e.preventDefault();
    binding.run(e);
    if (e.key !== "g") pendingG = false;
  });
}
