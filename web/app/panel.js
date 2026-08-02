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
    const card = el(`section.panel${def.hero ? ".hero" : ""}`, { id: `panel-${def.id}` },
      el("h2", { text: def.title }),
    );
    const body = el("div.body");
    card.append(body);
    $(`#col-${def.col}`).append(card);
    def.node = body;
    def.card = card;
    draw(def);
  }
}

/** Flash a panel's border and scroll to it — what the task chips do. */
export function focusPanel(id) {
  const def = registry.get(id);
  if (!def?.card) return;
  def.card.scrollIntoView({ behavior: "smooth", block: "center" });
  def.card.classList.add("flash");
  setTimeout(() => def.card.classList.remove("flash"), 1200);
}

export function markDone(id, done) {
  registry.get(id)?.card?.classList.toggle("done-gold", done);
}
