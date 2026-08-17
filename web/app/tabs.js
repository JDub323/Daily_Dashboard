// The tab shell.
//
// The dashboard used to be ten panels in three fixed columns, all on screen at
// once. That made every panel equally important, which meant none of them
// were: the math problem you actually came to do sat in the same 300px card as
// a stock ticker. Splitting by subject lets each section have a *hero* — one
// thing that is obviously the point of the tab — with the rest supporting it.
//
// Panes are all built up front and shown by toggling `hidden`, never rebuilt
// on switch. Panels hold live timers, streaming generations and half-typed
// inputs; tearing their DOM down on every tab change would drop all three.
// Six hidden panes cost nothing and switching is then instant.
//
// `dailies` is the exception to the hero rule, on purpose. Games, newsletters
// and hobbies were three tabs of one or two cards each, and each had to
// nominate a hero it did not really have — the chess puzzle got the same slot
// on its tab that the math problem gets on its own. They are one page now, and
// on that page nothing is the point: it is the day's small obligations, all
// the same size, and you work down it.

import { $, el } from "./dom.js";

/**
 * The tabs, in order. The index is the digit that jumps to it, so reordering
 * this list reorders the shortcuts with it — there is no second table of
 * key bindings to keep in sync.
 *
 * `blurb` is the one line under the hero on an otherwise-empty tab; `bg` names
 * the backdrop in web/assets/bg/.
 */
export const TABS = [
  { id: "home", label: "home", blurb: "everything, counted" },
  { id: "math", label: "math", blurb: "problem of the day, and the theorem deck" },
  { id: "politics", label: "politics", blurb: "the week's issue, the prep, and the argument" },
  { id: "dailies", label: "dailies", blurb: "the word, the bee, the games, the puzzle, the paper" },
  { id: "markets", label: "markets", blurb: "quotes and the money pages" },
  { id: "health", label: "health", blurb: "weight and sleep" },
];

const byId = new Map(TABS.map((tab) => [tab.id, tab]));

let active = TABS[0].id;
const listeners = new Set();

/** Called with the new tab id after every switch. */
export function onTabChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const activeTab = () => active;

/** The node a panel with this tab/slot mounts into. */
export function slotNode(tabId, slot) {
  return $(`#pane-${tabId} > .${slot === "hero" ? "hero" : "grid"}`);
}

export function activate(id, { focus = false } = {}) {
  if (!byId.has(id) || id === active) {
    if (focus) $(`#tab-${id}`)?.focus();
    return;
  }
  active = id;

  for (const tab of TABS) {
    const selected = tab.id === active;
    $(`#pane-${tab.id}`).hidden = !selected;
    const button = $(`#tab-${tab.id}`);
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    // Roving tabindex: only the selected tab is in the browser's own tab
    // order, which is the documented tablist pattern — and it matters more
    // than usual here because Tab itself is bound to switching tabs.
    button.tabIndex = selected ? 0 : -1;
  }

  document.body.dataset.tab = active;
  $("#st-tab").textContent = byId.get(active).label;
  // The pane scrolls the window, so a tab switched into halfway down the
  // previous one would open mid-panel.
  window.scrollTo({ top: 0, behavior: "instant" });

  for (const fn of listeners) fn(active);
  if (focus) $(`#tab-${active}`).focus();
}

/** Move `delta` tabs, wrapping. What Tab and Shift-Tab do. */
export function cycle(delta) {
  const i = TABS.findIndex((tab) => tab.id === active);
  activate(TABS[(i + delta + TABS.length) % TABS.length].id);
}

/** Jump to the nth tab, 1-based — what the digit keys do. */
export function activateNth(n) {
  const tab = TABS[n - 1];
  if (tab) activate(tab.id);
}

export function initTabs() {
  const bar = $("#tabbar");
  const panes = $("#panes");

  TABS.forEach((tab, i) => {
    bar.append(
      el("button.tab", {
        id: `tab-${tab.id}`,
        text: tab.label,
        role: "tab",
        "aria-selected": "false",
        "aria-controls": `pane-${tab.id}`,
        tabIndex: -1,
        // The digit is printed on the tab rather than hidden in the
        // cheatsheet: a shortcut you have to look up is one you never learn.
        dataset: { key: String(i + 1) },
        onclick: () => activate(tab.id),
      }),
    );

    panes.append(
      el("section.pane", {
        id: `pane-${tab.id}`,
        role: "tabpanel",
        "aria-labelledby": `tab-${tab.id}`,
        hidden: true,
      },
        el("div.hero"),
        el("div.grid"),
      ),
    );
  });

  // `active` is already TABS[0], so activate() would return early without
  // ever painting the initial selection. Force the first switch.
  active = null;
  activate(TABS[0].id);
}
