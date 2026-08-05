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
//   - Off inside any editable element — with Esc as the deliberate exception,
//     since it is the way back out of one.
//   - Off whenever Ctrl/Alt/Cmd is held, so Cmd-1 still switches *browser*
//     tabs. A shortcut layer that eats the browser's own keys is worse than
//     no shortcut layer.
//
// Shift is not in that list, because Shift-Tab is a binding.

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
    keys: ["enter"],
    hint: "enter the focused panel, then open the focused link or press the button",
    match: (e) => e.key === "Enter",
    run: () => nav.enter(),
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
    hint: "leave a panel, then drop the ring · also closes a modal or a text box",
    match: (e) => e.key === "Escape",
    run: () => nav.escape(),
  },
];

function openHelp() {
  // The required ones, matching the statusline. Two surfaces disagreeing about
  // how much of the day is left is worse than either number alone.
  const tasks = required(chipTasks());
  const done = tasks.filter((task) => task.done).length;
  openModal("keyboard", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "active on the dashboard itself — not while a modal is open or a field is focused.",
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
    if (e.altKey || e.ctrlKey || e.metaKey) return;

    // Esc first, and ahead of both remaining gates: it is the way out of the
    // text box, and ui.js owns it while a modal is up.
    if (e.key === "Escape") {
      if (modalOpen()) return;
      if (isTyping(e.target)) {
        e.target.blur();
        e.preventDefault();
        return;
      }
      if (nav.escape()) e.preventDefault();
      return;
    }

    if (modalOpen() || isTyping(e.target)) return;

    const binding = BINDINGS.find((b) => b.match(e));
    if (!binding) return;
    // Tab is the one that would otherwise do something: without this the
    // browser walks its own focus order underneath the tab switch.
    e.preventDefault();
    binding.run(e);
    if (e.key !== "g") pendingG = false;
  });
}
