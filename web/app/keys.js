// Keyboard shortcuts, so the morning ritual is not eight mouse trips.
//
// One listener on the document rather than per-element handlers: every binding
// here is global by nature, and the chips are rebuilt on every store change so
// anything attached to them would have to be reattached each time.
//
// Three gates, and all three matter more than the bindings do. Shortcuts are
// off while a modal is open (you are doing the task, and `1` belongs in the
// box you are typing in), off inside any editable element, and off whenever a
// modifier is held so that Cmd-1 still switches browser tabs. A shortcut layer
// that eats the browser's own keys is worse than no shortcut layer.

import {
  chipTasks,
  moveChipCursor,
  openChip,
  openCursorChip,
  openNextUnfinished,
} from "./chips.js";
import { $, el } from "./dom.js";
import { openHistoryModal } from "./modals/history.js";
import { openModal, toast } from "./ui.js";

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTyping(target) {
  return Boolean(target) && (EDITABLE.has(target.tagName) || target.isContentEditable);
}

const modalOpen = () => !$("#modal-overlay").hidden;

/**
 * The bindings, as data — so the help sheet is generated from the same list
 * that dispatches. A legend maintained separately from the handler is a legend
 * that is wrong by the second change.
 */
const BINDINGS = [
  {
    keys: ["1", "…", "9"],
    hint: "open that task",
    match: (e) => /^[1-9]$/.test(e.key),
    run: (e) => openChip(Number(e.key) - 1),
  },
  {
    keys: ["n"],
    hint: "open the next unfinished task",
    match: (e) => e.key === "n",
    run: () => {
      if (!openNextUnfinished()) toast("everything is done — nice day ✦", "info");
    },
  },
  {
    keys: ["j", "k"],
    hint: "move between tasks",
    match: (e) => ["j", "k", "ArrowRight", "ArrowLeft"].includes(e.key),
    run: (e) => moveChipCursor(e.key === "j" || e.key === "ArrowRight" ? 1 : -1),
  },
  {
    keys: ["enter"],
    hint: "open the highlighted task",
    match: (e) => e.key === "Enter",
    run: () => openCursorChip(),
  },
  {
    keys: ["h"],
    hint: "history — heatmap, elo by area, day logs",
    match: (e) => e.key === "h",
    run: () => openHistoryModal(),
  },
  {
    keys: ["?"],
    hint: "this list",
    // Both spellings. A US layout reports `?` for shift-/, but the shifted
    // character is layout-dependent and some environments report the unshifted
    // key with `shiftKey` set instead — including, as it turns out, headless
    // automation. Matching one of them makes the binding work on a keyboard
    // and not on another, which is the worst of the three outcomes.
    match: (e) => e.key === "?" || (e.key === "/" && e.shiftKey),
    run: () => openHelp(),
  },
];

function openHelp() {
  openModal("keyboard", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "active on the dashboard itself — not while a modal is open or a field is focused.",
      }),
      el("div.keys", null,
        ...BINDINGS.flatMap((b) => [
          el("div.keycombo", null, ...b.keys.map((k) => el("kbd", { text: k }))),
          el("div.dim", { text: b.hint }),
        ]),
        el("div.keycombo", null, el("kbd", { text: "esc" })),
        el("div.dim", { text: "close" }),
      ),
      el("div.faint.small.gap", {
        text: `${chipTasks().length} tasks today · the digit for each is shown on its chip`,
      }),
    );
  });
}

export function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;

    if (modalOpen()) {
      // Esc is handled in ui.js, which owns the overlay. Everything else is
      // the modal's own business while it is up.
      return;
    }
    if (isTyping(e.target)) return;

    const binding = BINDINGS.find((b) => b.match(e));
    if (!binding) return;
    e.preventDefault();
    binding.run(e);
  });
}
