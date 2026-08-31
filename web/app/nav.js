// The focus ring: what h/j/k/l actually move.
//
// Two levels, on vim's own distinction between moving between windows and
// moving inside one. A flat "next focusable thing in the document" cursor was
// the obvious first design and it is unusable here — the reading panel alone
// has a dozen links and four buttons, so getting from it to the panel below
// would be twenty keystrokes.
//
//   panel mode (default)  h/j/k/l move between panels, geometrically.
//                         Enter presses the panel's only button, or descends.
//                         i drops into its first text box; f paints link hints.
//   item mode             j/k walk the links and controls inside that panel.
//                         Enter activates one.
//   insert mode           a text box has the keyboard. Tab walks the boxes of
//                         that panel and nothing else.
//
// Esc from any of them returns to panel mode in one press, and Alt-hjkl moves
// between panels from any of them too — the two escapes from a mode you did
// not mean to be in. The ring is never dropped: losing your place is not a
// state worth having a key for.
//
// Movement between panels is spatial rather than by DOM order, because the
// panes are CSS grids whose column count changes with the viewport. Ordering
// by document position would mean `l` moved you down a column on a narrow
// window, which is the kind of thing that teaches you not to trust the keys.

import { $, $$ } from "./dom.js";
import { activeTab } from "./tabs.js";

const ITEMS =
  "a[href], button:not(:disabled), input:not([disabled]), textarea, select, summary, .collapse-toggle";
/** What `i` and Tab walk. Checkboxes and radios are controls, not text boxes. */
const FIELDS =
  "input:not([disabled]):not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=button]), textarea, select";

/** panels | items | insert | hints */
let mode = "panels";
let itemIndex = -1;
/** The focused panel's element id, not the node: panels re-render their
 *  bodies constantly and a held node would outlive the DOM it described. */
let panelId = null;

const pane = () => $(`#pane-${activeTab()}`);

/**
 * On screen, and therefore worth landing on.
 *
 * `offsetParent` alone was enough while everything off screen was off screen
 * by way of `display: none` — a hidden pane, a collapsed card. The logging
 * carousel stacks its five slides in one grid cell and hides four of them
 * with `visibility`, which keeps their layout (that is the point: the shell
 * stops resizing as it turns) and therefore keeps their `offsetParent`. Left
 * at the old test, `h`/`j`/`k`/`l` stopped on cards nobody could see
 * and `f` painted hints on their links.
 *
 * Checked on the node's own ancestors rather than the node alone, because
 * `visibility` inherits and it is the *track* that is hiding the slide, not
 * the slide hiding itself.
 */
const visible = (node) =>
  node.offsetParent !== null && getComputedStyle(node).visibility !== "hidden";

/** Every visible panel card in the active tab, in document order. */
function panels() {
  return $$(".panel", pane()).filter(visible);
}

function panelNode() {
  return panelId ? $(`#${panelId}`) : null;
}

/** Focusable controls inside the focused panel, skipping hidden ones. */
function items() {
  const card = panelNode();
  if (!card) return [];
  return $$(ITEMS, card).filter(visible);
}

function fields(card) {
  return card ? $$(FIELDS, card).filter(visible) : [];
}

// --- painting ---------------------------------------------------------------

/**
 * Reapply both highlights.
 *
 * Exported because a panel re-render replaces its whole body, which throws
 * away the item highlight without telling anyone. The panel harness calls this
 * after every draw.
 */
export function repaint() {
  // A draw that replaced the hinted links took the hints with it — the list
  // you were choosing from no longer exists, so the layer comes down rather
  // than pointing at nodes that are no longer on screen.
  if (hinted.length && hinted.some((node) => !node.isConnected)) exitHints();

  for (const card of $$(".panel")) card.classList.remove("nav-panel");
  for (const node of $$(".nav-item")) node.classList.remove("nav-item");

  const card = panelNode();
  if (!card) return;
  card.classList.toggle("nav-panel", true);

  if (mode !== "items") return;
  const list = items();
  if (itemIndex >= list.length) itemIndex = list.length - 1;
  list[itemIndex]?.classList.add("nav-item");
  paintMode();
}

/**
 * What the mode segment says, when something has borrowed the keyboard.
 *
 * Held here rather than written straight to the DOM by the borrower, because
 * the borrower is not the only thing that paints it. Blurring a text box to
 * start the check-in ladder queues a deferred `setMode("panels")` — see the
 * focusout listener — which would land a moment later and quietly overwrite
 * the label with "panels" while the ladder was still up.
 */
let borrowed = null;

function paintMode() {
  $("#st-mode").textContent = borrowed ?? mode;
}

function setMode(next) {
  mode = next;
  paintMode();
  repaint();
}

/**
 * Borrow the mode segment. `showMode()` with no argument gives it back.
 *
 * The mode itself is untouched either way: a capture is something happening
 * *inside* panel mode, and Esc out of one should leave the ring exactly where
 * it was.
 */
export function showMode(text = null) {
  borrowed = text;
  paintMode();
}

/** The focused panel's element id, or null. `panel.js` maps it to a
 *  definition — nav itself knows nothing about the registry. */
export const focusedPanel = () => panelId;

function show(node) {
  node?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/** Back to the top of the page — where the tab bar and the date live. */
function toTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// --- panel mode -------------------------------------------------------------

/** Put the cursor on a panel by id, without scrolling past it. */
export function focusPanelNode(id) {
  panelId = id;
  itemIndex = -1;
  setMode("panels");
  show(panelNode());
}

/** The first panel of the active tab — where the cursor lands on a switch. */
export function resetToTab() {
  const list = panels();
  panelId = list[0]?.id ?? null;
  itemIndex = -1;
  setMode("panels");
}

const DIRECTIONS = {
  h: { axis: "x", sign: -1 },
  l: { axis: "x", sign: 1 },
  k: { axis: "y", sign: -1 },
  j: { axis: "y", sign: 1 },
};

/**
 * The nearest panel in a direction.
 *
 * Scored as distance along the axis you asked for plus twice the drift across
 * it, so a card that is slightly down and far right loses to one directly
 * right. The half-cell threshold stops two cards whose centres differ by three
 * pixels — a grid row, in other words — from counting as "below" each other.
 *
 * Every key additionally demands that the candidate *share the band it is
 * moving out of*: h/l want a candidate that overlaps the source's rows, j/k one
 * that overlaps its columns. Without it on h/l, `l` from a full-width hero —
 * which has nothing beside it — scored the leftmost card of the row *below* as
 * "right", because down-and-far-right still beats nothing at all.
 *
 * The same hole was open on j/k for longer, and it is the one you actually hit,
 * because the two halves of the health tab sit side by side and are *not* the
 * same height: the plan on the left is as tall as the day it is describing, and
 * the logging card on the right is a fixed 505px. Compare only the centres and
 * the taller card's centre sits below the shorter one's — so `k` out of the
 * plan went sideways to the log, and `j` out of the log went sideways to the
 * plan, as though the two cells were stacked. Which of them counted as "below"
 * the other changed with how much training the day prescribed, so on a light
 * day the keys behaved and on a heavy one they did not.
 *
 * Both holes are one rule: a key that changes the band it was moving along is a
 * key you stop trusting, so it now simply does nothing there. h and l are still
 * how you cross between columns, and `k` off the top of a column still reaches
 * the header through `toTop`.
 */
function nearest(from, { axis, sign }) {
  const rects = panels().map((card) => [card, card.getBoundingClientRect()]);
  const here = from.getBoundingClientRect();
  const cx = (r) => r.left + r.width / 2;
  const cy = (r) => r.top + r.height / 2;
  const along = axis === "x" ? cx : cy;
  const across = axis === "x" ? cy : cx;
  // The band the move is leaving: rows for a sideways key, columns for a
  // vertical one. The same 8px of slack at both ends, so a one-pixel grid seam
  // is not an overlap and two cards flush against each other are.
  const sharesRows = (r) => r.bottom > here.top + 8 && r.top < here.bottom - 8;
  const sharesCols = (r) => r.right > here.left + 8 && r.left < here.right - 8;
  const shares = axis === "x" ? sharesRows : sharesCols;
  const threshold = 8;

  let best = null;
  let bestScore = Infinity;
  for (const [card, rect] of rects) {
    if (card === from) continue;
    if (!shares(rect)) continue;
    const delta = (along(rect) - along(here)) * sign;
    if (delta < threshold) continue;
    const score = delta + 2 * Math.abs(across(rect) - across(here));
    if (score < bestScore) {
      bestScore = score;
      best = card;
    }
  }
  return best;
}

function movePanel(key) {
  const list = panels();
  if (!list.length) return;
  const from = panelNode() ?? list[0];
  if (!panelNode()) {
    panelId = from.id;
    repaint();
    show(from);
    return;
  }
  const next = nearest(from, DIRECTIONS[key]);
  if (!next) {
    // `k` off the top panel is how you get back to the tab bar and the date.
    // Without this the header was unreachable without the mouse: the ring
    // simply stopped at the first card and the page stayed where it was.
    if (key === "k") toTop();
    return;
  }
  panelId = next.id;
  repaint();
  show(next);
}

export function edge(which) {
  const list = panels();
  const card = which === "first" ? list[0] : list[list.length - 1];
  if (!card) return;
  panelId = card.id;
  setMode("panels");
  if (which === "first") toTop();
  else show(card);
}

// --- item mode --------------------------------------------------------------

function enterItems() {
  if (!panelNode()) resetToTab();
  if (!items().length) return;
  itemIndex = 0;
  setMode("items");
  show(items()[0]);
}

function moveItem(delta) {
  const list = items();
  if (!list.length) return;
  itemIndex = (itemIndex + delta + list.length) % list.length;
  repaint();
  show(list[itemIndex]);
}

/**
 * Hand the keyboard to a text box.
 *
 * Every binding in keys.js is gated off while an editable element has focus,
 * so typing into it just works, Tab walks this panel's other boxes and Esc
 * gives the keys back.
 *
 * `replace` is the difference between the two ways in. Enter on a field means
 * "set this value" and selects what is there; `i` means insert and leaves the
 * caret at the end, because a half-typed spelling answer must survive it.
 */
function focusField(node, { replace = false } = {}) {
  node.focus();
  // Said here as well as in the focusin listener. That listener is what keeps
  // the ring honest when the *mouse* moves focus, but a window that does not
  // have the OS focus never fires it, and the statusline would then claim you
  // were in panel mode with a caret blinking in a box.
  if (mode !== "insert") setMode("insert");
  if (!node.select) return;
  if (replace && node.type !== "number") {
    node.select();
    return;
  }
  const end = node.value?.length ?? 0;
  try {
    node.setSelectionRange(end, end);
  } catch {
    // number and select inputs have no selection to place; focus is enough.
  }
}

/** Act on the focused item. */
function activateItem() {
  const node = items()[itemIndex];
  if (!node) return;
  if (node.matches(FIELDS)) {
    focusField(node, { replace: true });
    return;
  }
  node.click();
  // Clicking usually re-renders the panel underneath us; the index survives,
  // and repaint puts the ring back on whatever now occupies that slot.
  setTimeout(repaint, 0);
}

// --- link hints -------------------------------------------------------------
//
// `f` paints a letter on each link in the focused panel; that letter then
// opens it. Two keystrokes rather than one, and in exchange the letters cost
// nothing from the rest of the map — the hint layer swallows the next key, so
// `g`, `h` and `n` can be hints here and still be themselves everywhere else.
// One letter per link also means a headline list is not capped at the six
// letters that happened to be free.

// Five letters short of the alphabet: c, d, l, m and p belong to the logging
// carousel, which `f` also dispatches. Two meanings for one key inside one
// layer is not a shortcut, and painting `c` on a headline while `c` also
// jumped to the check-in is exactly that.
//
// This list and `carousel.TABS` have to agree, and nothing enforces it — a
// letter added there and left here paints a hint that the dispatcher never
// offers, so the link is simply unreachable and the panel looks fine. It is
// checked in `tests/js/keys.test.js` for that reason.
export const HINT_KEYS = "abefghijknoqrstuvwxyz";

let hinted = [];
/** The mode `f` was pressed from, so cancelling puts you back in it. */
let hintReturn = "panels";

function links() {
  const card = panelNode();
  return card ? $$("a[href]", card).filter(visible) : [];
}

export const inHints = () => mode === "hints";

/**
 * Paint the hints and take the next key.
 *
 * It used to decline on a panel with no links, which was right when links
 * were the only thing this layer did. It now also carries the five letters
 * that jump to a log — and those have to work from the maths tab, which has
 * no links on it at all — so an empty panel opens the layer with nothing
 * painted rather than swallowing the keypress.
 */
/**
 * @param nudges how many notifications are on screen, so the mode line can say
 *   which digits this layer will answer. Passed in rather than imported: nav
 *   is underneath everything and importing the notification stack from here
 *   would be a cycle through keys.js.
 */
export function hint(nudges = 0) {
  const list = links().slice(0, HINT_KEYS.length);
  clearHints();
  hintReturn = mode === "hints" ? hintReturn : mode;
  hinted = list;
  // A data attribute rather than an inserted badge node: the panels own their
  // own markup and re-render it constantly, and an attribute on an existing
  // link cannot end up orphaned in the middle of a sentence.
  hinted.forEach((node, i) => {
    node.dataset.navkey = HINT_KEYS[i];
  });
  mode = "hints";
  const painted = hinted.length ? `a–${HINT_KEYS[hinted.length - 1]} · ` : "";
  const numbered = nudges > 0 ? ` · 1–${nudges}` : "";
  $("#st-mode").textContent = `hints ${painted}c d l m p${numbered}`;
  return true;
}

function clearHints() {
  for (const node of hinted) delete node.dataset.navkey;
  hinted = [];
}

export function exitHints() {
  clearHints();
  setMode(hintReturn === "hints" ? "panels" : hintReturn);
}

/** Consume a key while the hints are up. True if it opened something. */
export function takeHint(key) {
  const i = HINT_KEYS.indexOf(key);
  const node = i < 0 ? null : hinted[i];
  exitHints();
  if (!node) return false;
  // click() rather than window.open: the anchor already carries target and
  // rel, and the popup blocker allows this because a keypress is a gesture.
  node.click();
  return true;
}

// --- the public verbs -------------------------------------------------------

export const inItems = () => mode === "items";

/**
 * @param opts.toPanels  force a between-panel move — what Alt-hjkl does from
 *                       inside item or insert mode.
 */
export function move(key, { toPanels = false } = {}) {
  if (mode === "items" && !toPanels) {
    // h/l fall back to linear order inside a panel: "the previous control" is
    // what you mean there, and a row of buttons is as often vertical as not.
    moveItem(key === "j" || key === "l" ? 1 : -1);
    return;
  }
  if (mode !== "panels") {
    itemIndex = -1;
    setMode("panels");
  }
  movePanel(key);
}

/**
 * Enter.
 *
 * On a panel holding exactly one button, that button is unambiguously what
 * Enter means — "parse & log", "check", "submit" — and making you press Enter,
 * then j to the only control, then Enter again was ceremony around a single
 * choice. Anything with two buttons still descends, and Shift-Enter descends
 * regardless, which is the way into the heading of a collapsible panel whose
 * body holds one button.
 */
export function enter({ descend = false } = {}) {
  if (mode === "items") {
    activateItem();
    return;
  }
  if (!panelNode()) resetToTab();
  const card = panelNode();
  if (!card) return;
  if (!descend) {
    const only = $$("button:not(:disabled)", card).filter(visible);
    if (only.length === 1) {
      only[0].click();
      setTimeout(repaint, 0);
      return;
    }
  }
  enterItems();
}

/** `i`: straight into the panel's first text box, from panel or item mode. */
export function insert() {
  if (!panelNode()) resetToTab();
  const box = fields(panelNode())[0];
  if (!box) return false;
  focusField(box);
  return true;
}

/**
 * Tab inside a text box: the next box of the same panel, wrapping.
 *
 * Trapped on purpose. Tab's document order walks out of the panel and into
 * every control below it — on a tabbed page, mostly ones that are not on
 * screen — and Tab at the top level is the section switch, which is the last
 * thing you want half way through filling in a form.
 */
export function cycleField(delta) {
  const active = document.activeElement;
  const card = active?.closest?.(".panel");
  if (!card) return false;
  const list = fields(card);
  const i = list.indexOf(active);
  if (i < 0) return false;
  // One box still swallows the key rather than letting Tab change section.
  if (list.length > 1) focusField(list[(i + delta + list.length) % list.length], { replace: true });
  return true;
}

/**
 * Esc: back to the panel ring, in one press, from wherever you are.
 *
 * It used to unwind one level at a time and then drop the ring entirely, which
 * meant Esc twice put you nowhere and the third h/j/k/l started over from the
 * first panel of the tab. Base state is a highlighted cell, and Esc is how you
 * get there.
 */
export function escape() {
  if (mode === "hints") {
    exitHints();
    return true;
  }
  const active = document.activeElement;
  if (active?.blur && active !== document.body) active.blur();
  if (mode !== "panels") {
    itemIndex = -1;
    setMode("panels");
    return true;
  }
  return false;
}

/**
 * Finished: give the keyboard back, without moving the ring.
 *
 * What `escape()` does, minus the part that is about *you* deciding to
 * leave — there are no hints to take down, because a panel that just posted
 * something was not in the hint layer. A log that ends with the caret still
 * in its note box costs an Esc before the next key means anything, and that
 * Esc is being pressed at a form that is already empty.
 *
 * The ring stays on the card deliberately. You have just written to it, it
 * is what you are looking at, and moving on is a decision with a key of its
 * own.
 */
export function leaveCell() {
  const active = document.activeElement;
  if (active?.blur && active !== document.body) active.blur();
  if (mode !== "panels") {
    itemIndex = -1;
    setMode("panels");
  }
}

/**
 * Keep the ring on whatever the browser's own focus is doing.
 *
 * Clicking into a box, and the spelling round autofocusing its first one, both
 * move the keyboard without going through any verb here. Without this the ring
 * still claimed the panel you were last on, so Esc and Alt-hjkl acted on a
 * card you were not typing into.
 */
export function initNav() {
  document.addEventListener("focusin", (e) => {
    const card = e.target.closest?.(".panel");
    if (!card) return;
    panelId = card.id;
    if (e.target.matches(FIELDS)) setMode("insert");
  });

  document.addEventListener("focusout", () => {
    // After the event, because focus is on <body> during it and moving between
    // two boxes of the same panel would otherwise flicker out of insert mode.
    setTimeout(() => {
      if (mode !== "insert") return;
      if (document.activeElement?.matches?.(FIELDS)) return;
      setMode("panels");
    }, 0);
  });
}
