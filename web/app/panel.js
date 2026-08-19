// The panel harness: mount, render, and turn a thrown error into a retry.

import { el, mount } from "./dom.js";
import { focusPanelNode, focusedPanel, repaint } from "./nav.js";
import { activate, slotNode } from "./tabs.js";

const registry = new Map();

/**
 * Live panels reschedule themselves after every draw.
 *
 * A panel marked `live` may return, from `render`, how long to wait before the
 * next one — the feed panels compute that from the server's own cache TTL.
 * Returning nothing falls back to `DEFAULT_REFRESH_MS`, and a throw retries on
 * `RETRY_MS` instead, so a feed that was down when the page loaded recovers on
 * its own rather than waiting for someone to press the retry button.
 */
const timers = new Map();
const DEFAULT_REFRESH_MS = 300_000;
const RETRY_MS = 60_000;

function schedule(def, ms) {
  if (!def.live) return;
  clearTimeout(timers.get(def.id));
  timers.set(
    def.id,
    setTimeout(() => {
      // A hidden tab does not refetch. Nobody is reading it, and `main.js`
      // redraws every live panel when it comes back into view, so polling in
      // the background would buy a fresher number for a screen that is about
      // to be redrawn anyway.
      if (document.hidden) schedule(def, ms);
      else draw(def);
    }, ms),
  );
}

export function register(...definitions) {
  for (const def of definitions) registry.set(def.id, def);
}

export function get(id) {
  return registry.get(id);
}

/** Re-render one panel by id. Safe to call for a panel that isn't mounted. */
export function rerender(id) {
  const def = registry.get(id);
  if (def?.node) draw(def);
}

/** Re-render the named panels, or every registered one. */
export function rerenderAll(ids = [...registry.keys()]) {
  for (const id of ids) rerender(id);
}

async function draw(def) {
  try {
    const after = await def.render(def.node);
    schedule(def, Number.isFinite(after) ? after : DEFAULT_REFRESH_MS);
  } catch (error) {
    mount(def.node,
      el("div.err", { text: error.message }),
      el("div.row", null, el("button", { text: "retry", onclick: () => draw(def) })),
    );
    schedule(def, RETRY_MS);
  }
  // A render replaces the panel's whole body, which silently throws away the
  // item highlight. The ring is put back on whatever now occupies that slot.
  repaint();
}

/** Redraw every live panel — what the tab does when it comes back into view. */
export function refreshLive() {
  for (const def of registry.values()) {
    if (def.live && def.node) draw(def);
  }
}

export function mountAll() {
  for (const def of registry.values()) {
    const card = el(`section.panel${def.hero ? ".hero" : ""}`, { id: `panel-${def.id}` });

    // How many of the grid's six columns this card is worth. Set as a custom
    // property rather than `grid-column` directly so the narrow-screen media
    // queries can override it — an inline `grid-column` would beat them and
    // there would be no way back but `!important`.
    //
    // Hero panels ignore it: the hero is a flex column, and what a hero is
    // worth is the whole width by definition.
    if (!def.hero) card.style.setProperty("--span", def.span ?? 2);

    // A panel may decline a heading, and then there is no <h2> at all rather
    // than an empty one holding its line-height open. The week grid is the case
    // that wanted it: its own header already says which week is on screen, so a
    // 21px "week" above that was a second title for the same thing.
    //
    // A collapsible panel cannot decline, because its heading *is* its toggle —
    // there is no separate chevron, the heading is already the full width of
    // the card and is the thing you would reach for.
    const head = def.title || def.collapsible ? el("h2", { text: def.title ?? "" }) : null;

    if (def.collapsible && head) {
      head.classList.add("collapse-toggle");
      head.append(el("span.panel-summary"));
      head.addEventListener("click", () => {
        manual.add(def.id);
        setCollapsed(def.id, !card.classList.contains("collapsed"));
      });
    }

    const body = el("div.body");
    card.append(...(head ? [head] : []), body);
    slotNode(def.tab, def.slot).append(card);
    def.node = body;
    def.card = card;
    draw(def);
  }
}

// --- collapsing a finished panel -------------------------------------------
//
// A hero panel whose task is done folds to its heading plus a one-line
// summary. The tab it heads is then a short page saying what happened rather
// than a full-height card restating a problem you already solved.

const manual = new Set();
/** Panels the user has toggled by hand. Auto-collapse leaves these alone —
 *  expanding a finished panel to reread the answer should not be undone by
 *  the next store change half a second later. */

export function setCollapsed(id, collapsed) {
  const def = registry.get(id);
  if (!def?.card) return;
  def.card.classList.toggle("collapsed", collapsed);
  const slot = def.card.querySelector(".panel-summary");
  if (slot) slot.textContent = collapsed ? (def.summary?.() ?? "") : "";
}

/** Forget the manual overrides — what a day rollover does. */
export function resetCollapse() {
  manual.clear();
}

/**
 * Show a panel: switch to its tab, put the focus ring on it, and flash it.
 *
 * This is what a task chip does, and what `n` does. Under tabs it has to move
 * the *section* as well — the old version scrolled to a card that, half the
 * time, is now on a page you are not looking at.
 */
export function focusPanel(id) {
  const def = registry.get(id);
  if (!def?.card) return;
  activate(def.tab);
  // Asking for a panel is asking to see it. Scrolling to a one-line summary
  // and flashing its border would be the shortcut appearing not to work, so a
  // collapsed panel opens — and counts as manual, since you asked for it.
  if (def.card.classList.contains("collapsed")) {
    manual.add(id);
    setCollapsed(id, false);
  }
  // nav.js knows nothing about the registry — it addresses panels by element
  // id — so this is the one place the two meet.
  focusPanelNode(def.card.id);
  def.card.scrollIntoView({ behavior: "smooth", block: "center" });
  def.card.classList.add("flash");
  setTimeout(() => def.card.classList.remove("flash"), 1200);
}

/**
 * Let the focused panel own Enter.
 *
 * A panel may export `onEnter()`, returning true if it took the key. That is
 * how the two logging panels start their number ladder: on a card of
 * twenty-five tiny buttons, `nav.enter` can only descend into item mode and
 * walk them one at a time, which is the ceremony the ladder exists to remove.
 *
 * Here rather than in nav.js because nav addresses panels by element id and
 * has never known the registry existed — this file is where the two meet.
 */
export function panelEnter() {
  const cardId = focusedPanel();
  if (!cardId) return false;
  for (const def of registry.values()) {
    if (def.card?.id === cardId) return Boolean(def.onEnter?.());
  }
  return false;
}

export function markDone(id, done) {
  const def = registry.get(id);
  if (!def?.card) return;
  def.card.classList.toggle("done-gold", done);
  if (def.collapsible && !manual.has(id)) setCollapsed(id, done);
}
