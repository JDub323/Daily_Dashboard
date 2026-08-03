// The panel harness: mount, render, and turn a thrown error into a retry.

import { $, el, mount } from "./dom.js";

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
    const head = el("h2", { text: def.title });

    // A collapsible panel's heading is also its toggle. No separate chevron
    // button: the heading is already the full width of the card and is the
    // thing you would reach for.
    if (def.collapsible) {
      head.classList.add("collapse-toggle");
      head.append(el("span.panel-summary"));
      head.addEventListener("click", () => {
        manual.add(def.id);
        setCollapsed(def.id, !card.classList.contains("collapsed"));
      });
    }

    const body = el("div.body");
    card.append(head, body);
    $(`#col-${def.col}`).append(card);
    def.node = body;
    def.card = card;
    draw(def);
  }
}

// --- collapsing a finished panel -------------------------------------------
//
// The stated design is hierarchy rather than a flat grid, and until now that
// applied only across space: the centre column looked identical at 0/8 and at
// 8/8. A hero panel whose task is done collapses to its heading plus a
// one-line summary and sorts to the bottom of its column, so what is still
// open rises without anything having to move in the DOM.

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

/** Flash a panel's border and scroll to it — what the task chips do. */
export function focusPanel(id) {
  const def = registry.get(id);
  if (!def?.card) return;
  // Asking for a panel is asking to see it. Scrolling to a one-line summary
  // and flashing its border would be the shortcut appearing not to work, so a
  // collapsed panel opens — and counts as manual, since you asked for it.
  if (def.card.classList.contains("collapsed")) {
    manual.add(id);
    setCollapsed(id, false);
  }
  def.card.scrollIntoView({ behavior: "smooth", block: "center" });
  def.card.classList.add("flash");
  setTimeout(() => def.card.classList.remove("flash"), 1200);
}

export function markDone(id, done) {
  const def = registry.get(id);
  if (!def?.card) return;
  def.card.classList.toggle("done-gold", done);
  if (def.collapsible && !manual.has(id)) setCollapsed(id, done);
}
