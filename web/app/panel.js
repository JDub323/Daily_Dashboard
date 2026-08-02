// The panel harness: mount, render, and turn a thrown error into a retry.

import { $, el, mount } from "./dom.js";

const registry = new Map();

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
    await def.render(def.node);
  } catch (error) {
    mount(def.node,
      el("div.err", { text: error.message }),
      el("div.row", null, el("button", { text: "retry", onclick: () => draw(def) })),
    );
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
