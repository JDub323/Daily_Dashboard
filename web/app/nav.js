// The focus ring: what h/j/k/l actually move.
//
// Two levels, on vim's own distinction between moving between windows and
// moving inside one. A flat "next focusable thing in the document" cursor was
// the obvious first design and it is unusable here — the reading panel alone
// has a dozen links and four buttons, so getting from it to the panel below
// would be twenty keystrokes.
//
//   panel mode (default)  h/j/k/l move between panels, geometrically.
//                         Enter descends into the focused panel.
//   item mode             j/k walk the links and controls inside that panel.
//                         Enter activates one. Esc comes back up.
//
// Movement between panels is spatial rather than by DOM order, because the
// panes are CSS grids whose column count changes with the viewport. Ordering
// by document position would mean `l` moved you down a column on a narrow
// window, which is the kind of thing that teaches you not to trust the keys.

import { $, $$ } from "./dom.js";
import { activeTab } from "./tabs.js";

const ITEMS =
  "a[href], button:not(:disabled), input:not([disabled]), textarea, select, summary, .collapse-toggle";

let mode = "panels";
let itemIndex = -1;
/** The focused panel's element id, not the node: panels re-render their
 *  bodies constantly and a held node would outlive the DOM it described. */
let panelId = null;

const pane = () => $(`#pane-${activeTab()}`);

/** Every visible panel card in the active tab, in document order. */
function panels() {
  return $$(".panel", pane()).filter((card) => card.offsetParent !== null);
}

function panelNode() {
  return panelId ? $(`#${panelId}`) : null;
}

/** Focusable controls inside the focused panel, skipping hidden ones. */
function items() {
  const card = panelNode();
  if (!card) return [];
  return $$(ITEMS, card).filter((node) => node.offsetParent !== null);
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
  for (const card of $$(".panel")) card.classList.remove("nav-panel");
  for (const node of $$(".nav-item")) node.classList.remove("nav-item");

  const card = panelNode();
  if (!card) return;
  card.classList.toggle("nav-panel", true);

  if (mode !== "items") return;
  const list = items();
  if (itemIndex >= list.length) itemIndex = list.length - 1;
  list[itemIndex]?.classList.add("nav-item");
  $("#st-mode").textContent = "items";
}

function setMode(next) {
  mode = next;
  $("#st-mode").textContent = next;
  repaint();
}

function show(node) {
  node?.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
 */
function nearest(from, { axis, sign }) {
  const rects = panels().map((card) => [card, card.getBoundingClientRect()]);
  const here = from.getBoundingClientRect();
  const cx = (r) => r.left + r.width / 2;
  const cy = (r) => r.top + r.height / 2;
  const along = axis === "x" ? cx : cy;
  const across = axis === "x" ? cy : cx;
  const threshold = 8;

  let best = null;
  let bestScore = Infinity;
  for (const [card, rect] of rects) {
    if (card === from) continue;
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
  if (!next) return;
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
  show(card);
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
 * Act on the focused item.
 *
 * A text box is focused rather than clicked, which hands the keyboard over to
 * it — every binding in keys.js is gated off while an editable element has
 * focus, so typing into it just works and Esc gives the keys back.
 */
function activateItem() {
  const node = items()[itemIndex];
  if (!node) return;
  if (node.matches("input, textarea, select")) {
    node.focus();
    if (node.select && node.type !== "number") node.select();
    return;
  }
  node.click();
  // Clicking usually re-renders the panel underneath us; the index survives,
  // and repaint puts the ring back on whatever now occupies that slot.
  setTimeout(repaint, 0);
}

// --- the public verbs -------------------------------------------------------

export const inItems = () => mode === "items";

export function move(key) {
  if (mode === "items") {
    // h/l fall back to linear order inside a panel: "the previous control" is
    // what you mean there, and a row of buttons is as often vertical as not.
    moveItem(key === "j" || key === "l" ? 1 : -1);
    return;
  }
  movePanel(key);
}

export function enter() {
  if (mode === "items") activateItem();
  else enterItems();
}

/** Esc: leave item mode, or drop the ring entirely if already out of it. */
export function escape() {
  if (mode === "items") {
    itemIndex = -1;
    setMode("panels");
    return true;
  }
  if (panelId) {
    panelId = null;
    repaint();
    return true;
  }
  return false;
}
