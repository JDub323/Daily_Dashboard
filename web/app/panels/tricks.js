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
// [Invariant] Nothing here spends money without being asked. The set arrives
// free three ways — from the overnight file, or by carrying the prompt to the
// Claude app and pasting the result back — and the API button is the last
// resort, labelled with what it costs.

import * as api from "../api.js";
import { promptBlock } from "../clipboard.js";
import { el, mount } from "../dom.js";
import { openTrickArchive } from "../modals/tricks.js";
import { clearTrickDraft, openTrickSet } from "../modals/trickset.js";
import { put, store } from "../store.js";

/** What has been pasted but not submitted, kept across re-renders. */
let pasted = { date: null, text: "" };

function paste() {
  const date = api.today();
  if (pasted.date !== date) pasted = { date, text: "" };
  return pasted;
}

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

// --- getting today's set ----------------------------------------------------

/**
 * The free path, and the default one.
 *
 * The prompt is assembled server-side and shipped with the panel, so the copy
 * button is instant and what you copy is what you were looking at — the same
 * bargain the debate's prompt makes. The generation then happens in the Claude
 * app on the subscription, and the dashboard's only job is to read what comes
 * back.
 */
function pasteBox(body, panel, extra) {
  const held = paste();
  const box = el("textarea.recall-box", {
    rows: 2,
    value: held.text,
    // The instruction lives in the placeholder rather than on a line of its
    // own. This state is on screen for ten seconds a day and every sentence in
    // it is one you have already read, so the card is kept to the two presses
    // it is actually for.
    placeholder: "…then paste Claude's reply here",
    oninput: (e) => { held.text = e.target.value; },
  });
  const message = el("span.faint");
  const save = el("button.primary", {
    text: "use this set",
    onclick: async () => {
      if (!box.value.trim()) {
        message.textContent = "paste the reply first";
        return;
      }
      save.disabled = true;
      message.textContent = "reading it…";
      try {
        put("tricks", await api.post("/api/trick/paste", { text: box.value }));
        pasted = { date: null, text: "" };
        clearTrickDraft();
        render(body);
        // Straight into the sheet: you have just fetched the set, and the next
        // thing you want is to be looking at it.
        openTrickSet();
      } catch (error) {
        save.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div", null,
    promptBlock(panel.prompt, {
      summary: "read the prompt",
      label: "copy the prompt →",
    }).node,
    box,
    el("div.row", null, save, extra, message),
  );
}

/** Before the set exists. */
function undrawn(body, panel) {
  if (!panel.enabled) {
    return el("div.faint", { text: "not configured — see [tricks] in config.toml" });
  }

  // The overnight task already did it: one press, no window-hopping, no cost.
  // Offered above the copy-and-paste route rather than beside it, because when
  // it is available it is strictly the better of the two.
  const fromBatch = el("button.primary", {
    text: "open tonight's set",
    onclick: () => run(body, fromBatch, "opening…"),
  });

  // The paid path, quiet and beside the free one rather than under a heading of
  // its own. It is the fallback, and a button that costs something should look
  // like one you have to decide to press — but it should not need a paragraph.
  const paid = el("button", {
    text: "or generate · ~$0.03",
    title: "one API generation. The prompt above does the same job for nothing.",
    onclick: () => run(body, paid, "writing today's set…"),
  });

  return el("div", null,
    el("div.faint", { text: `from this week's topic: ${store.config.topic}` }),
    panel.batched
      ? el("div", null,
          el("div.faint.small", { text: "the 3am task left a set — free" }),
          el("div.row", null, fromBatch),
        )
      : pasteBox(body, panel, paid),
  );
}

/** `POST /api/trick/new` — the batch or, failing that, a paid generation. */
async function run(body, button, busy) {
  const was = button.textContent;
  button.disabled = true;
  button.textContent = busy;
  try {
    put("tricks", await api.post("/api/trick/new"));
    clearTrickDraft();
    render(body);
    openTrickSet();
  } catch (error) {
    button.disabled = false;
    button.textContent = was;
    body.append(el("div.err", { text: error.message }));
  }
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
function summaryCard(body, panel) {
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
    : el("div.faint", { text: `${panel.problems.length} problems, and one challenge` });

  return el("div", null,
    el("div.row.spread", null,
      el("span.big.gold", { text: panel.trick.name }),
      el("span.tag", { text: panel.trick.kind }),
    ),
    el("div.faint", { text: panel.trick.topic }),
    panel.callback_of
      ? el("div.faint.small", { text: `one problem calls back to ${panel.callback_of}` })
      : null,
    progress,
    el("div.row", null, openButton),
  );
}

function render(body) {
  const panel = store.state.tricks;
  mount(body,
    header(panel),
    panel.trick ? summaryCard(body, panel) : undrawn(body, panel),
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
