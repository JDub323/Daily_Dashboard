// A panel borrowing the whole keyboard.
//
// The two logging panels are forms whose every field has three to ten legal
// values, and filling one in was: `i`, tab, tab, tab, or else the mouse. The
// keys that would obviously do it — the digits — are the tab switches, and they
// have to stay the tab switches, because a shortcut that means one thing on one
// card and another thing on the next is not a shortcut, it is a hazard.
//
// So a panel can *take* the keyboard for as long as it is filling something in,
// exactly the way the link-hint layer does in nav.js: while a capture is up it
// sees every key before the bindings table does, and the digits mean what that
// panel says they mean. Esc gives them back, and so does anything that moves
// you somewhere else — Alt-hjkl, a tab switch.
//
// The rule that keeps this honest is that a capture is always *visible*: the
// statusline's mode segment says which panel has the keyboard and what it is
// waiting for ("check-in ▸ fatigue"), because a mode you cannot see is a mode
// you press Escape to leave at random.

import { exitHints, inHints, showMode } from "./nav.js";
import { onTabChange } from "./tabs.js";

/** `{ owner, onKey }`, or null when the bindings table owns the keys. */
let active = null;

export const capturing = () => active !== null;

/** The panel id holding the keyboard, or null. */
export const captureOwner = () => active?.owner ?? null;

/**
 * Take the keyboard for `owner`.
 *
 * `onKey(event)` returns true for a key it consumed. Anything it declines
 * falls through to the normal bindings, so Tab still switches section and `?`
 * still opens the cheatsheet from inside a capture — the panel only has to
 * name the keys it actually wants.
 *
 * Re-calling with the same owner replaces the handler rather than nesting.
 * That is the ordinary case, not an edge one: these panels re-render under
 * their own capture (an answer posts, the store notifies, the body is rebuilt)
 * and the new handler closes over the new nodes.
 */
export function beginCapture({ owner, label, onKey, onEnd = null }) {
  // Two layers over the same keys is one too many: the dispatcher offers to
  // the capture first, so painted hints would sit there unreachable.
  if (inHints()) exitHints();
  // Deliberately *not* firing the outgoing `onEnd` here. Replacing is the
  // ordinary case — the same panel re-taking its own keyboard after a redraw —
  // and a panel that cleared its own cursor on every render would never be
  // able to hold one.
  active = { owner, onKey, onEnd };
  showMode(label);
}

/** Update the statusline without disturbing the handler — a capture that has
 *  moved to its next field. */
export function captureLabel(label) {
  if (active) showMode(label);
}

/**
 * Give the keyboard back.
 *
 * `owner` guards against a stale panel ending a capture that has since been
 * taken by another one; omit it to end whatever is running.
 */
export function endCapture(owner = null) {
  if (!active || (owner && active.owner !== owner)) return false;
  const { onEnd } = active;
  // Cleared before the callback, so a panel that redraws from inside its own
  // `onEnd` does not find a capture still standing and re-take it.
  active = null;
  showMode();
  onEnd?.();
  return true;
}

/** Offer a key to the active capture. True if it was consumed. */
export function takeKey(event) {
  return Boolean(active?.onKey(event));
}

// Switching section means you are somewhere else now, and a capture left
// running would eat the digits on a page that has never heard of it.
onTabChange(() => endCapture());
