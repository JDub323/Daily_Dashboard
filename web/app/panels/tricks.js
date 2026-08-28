// The trick of the day: one identity from the week's topic, and the set of
// problems that puts it in your hands.
//
// The theorem deck asks you to recall a statement. This asks you to *use* one,
// which is a different act and the reason the two panels sit next to each other
// rather than being one panel: a theorem you can state and have never applied is
// exactly what this is for.
//
// The cell is deliberately thin. Nine problems, each of them a displayed
// formula, is a document — it went in a grid card once and did not fit — so the
// working surface is a full-width sheet behind a click (modals/trickset.js) and
// what stays here is the line that says where you are.
//
// [Invariant] The answers are not in the payload until the set is marked. The
// marking is a string comparison the server performs, so shipping the answers
// with the problems would put the answer key in the page.
//
// [Invariant] Nothing here spends money at all. The set is written overnight by
// the scheduled task and read off disk; there is no generation behind this panel
// and no button that could start one. The one paid call the feature still makes
// is the challenge grader, in the sheet, when a challenge is actually answered.

import { opener } from "../daily.js";
import { el, mount } from "../dom.js";
import { openTrickArchive } from "../modals/tricks.js";
import { openTrickSet } from "../modals/trickset.js";
import { store } from "../store.js";

function header(panel) {
  const detail = [
    panel.events
      ? `${panel.events} result${panel.events === 1 ? "" : "s"}`
      : "no results yet — your math rating",
  ];
  if (panel.total) detail.push(`${panel.total} trick${panel.total === 1 ? "" : "s"} learned`);
  return el("div.row", null,
    el("span.rating", { text: panel.rating }),
    el("span.faint", { text: detail.join(" · ") }),
  );
}

function links() {
  return el("div.row.divider", null,
    el("button", { text: "past tricks →", onclick: openTrickArchive }),
  );
}

// --- before the set exists --------------------------------------------------

/**
 * Two empty states, and which one you get says whose turn it is.
 *
 * With a file on disk this is a busy line: `daily.js` asked for the set at boot
 * and it is a file read away. Without one there is nothing to press — the panel
 * used to offer the prompt to carry into the Claude app and a paid generation
 * beside it, and both are gone, so what is left to say is that the night did not
 * run and which command puts that right. The math panel's bargain: a failure you
 * can see and a fix you can name beats a fallback that quietly costs something.
 */
function undrawn(panel) {
  if (!panel.enabled) {
    return el("div.faint", { text: "not configured — see [tricks] in config.toml" });
  }
  if (!panel.batched) {
    return el("div", null,
      el("div.faint", { text: "no set for today — the overnight task did not leave one" }),
      el("div.faint.small", { text: "./batch.sh --trick-context, then --validate-trick" }),
    );
  }
  // The gather is in flight, or it failed and this is the retry. Either way the
  // control comes from `daily.js`, so pressing it cannot come to mean something
  // different from what the boot already tried.
  return el("div", null,
    el("div.faint", { text: `from this week's topic: ${store.config.topic}` }),
    opener("tricks"),
  );
}

// --- once it exists ---------------------------------------------------------

/**
 * The whole cell, once there is a set: what today's trick is called, and where
 * you got to.
 *
 * No maths at all, and that is the point rather than an economy. The statement
 * was here for one draft and put the card at 423px against its neighbours' 122
 * — a displayed product wrapped over four lines in a 311px column, which is the
 * same complaint the problems caused, at a quarter of the size. A grid card is
 * a place for a sentence; the sheet is where the formulas go.
 */
function summaryCard(panel) {
  const done = panel.status !== "open";

  const openButton = el("button.primary", {
    text: done ? "read the set →" : "work the set →",
    onclick: openTrickSet,
  });

  const progress = done
    ? el("div.row", null,
        el("span.big.gold", { text: `${panel.correct}/${panel.problems.length}` }),
        el(`span.${panel.delta > 0 ? "delta-up" : panel.delta < 0 ? "delta-down" : "faint"}`, {
          text: panel.delta == null ? "" : panel.delta === 0 ? "no change" : `${panel.delta > 0 ? "+" : ""}${panel.delta}`,
        }),
        el("span.faint", {
          text: panel.challenge_ok === null
            ? "· challenge unanswered"
            : `· challenge ${panel.challenge_ok ? "solved" : "missed"}`,
        }),
      )
    : null;

  return el("div", null,
    el("div.row.spread", null,
      el("span.big.value", { text: panel.trick.name }),
      el("span.tag", { text: panel.trick.kind }),
    ),
    progress,
    el("div.row", null, openButton),
  );
}

function render(body) {
  const panel = store.state.tricks;
  mount(body,
    header(panel),
    panel.trick ? summaryCard(panel) : undrawn(panel),
    links(),
  );
}

export default {
  id: "tricks",
  title: "◆ trick of the day",
  tab: "math",
  slot: "grid",
  // Wide, because everything in it is a formula. This is the card whose
  // contents were being blamed on the panel: a displayed product wrapped over
  // four lines in a 311px column, which is what sent the maths to the sheet in
  // the first place.
  span: 6,
  collapsible: true,

  /** The one line the panel keeps when it is folded. */
  summary() {
    const panel = store.state.tricks;
    if (!panel.trick) return `${panel.rating}`;
    if (panel.status === "open") return `${panel.rating} · ${panel.trick.name}`;
    const delta = panel.delta == null ? "" : ` ${panel.delta >= 0 ? "+" : ""}${panel.delta}`;
    return `${panel.rating}${delta} · ${panel.correct}/${panel.problems.length} · ${panel.trick.name}`;
  },

  async render(node) {
    render(node);
  },
};
