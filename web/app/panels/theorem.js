// Theorem recall: write what it says, and where you needed it.
//
// This used to be a reveal button and a one-bit self-check, sharing a card
// with the box for adding new theorems — two unrelated jobs in one panel, and
// the reveal put the answer on screen before the judgement was made, which
// makes it reading rather than recall. Adding a theorem is its own panel now
// (see deck.js), and the statement does not arrive from the server until the
// recall has been graded.
//
// Two boxes, because remembering what Cauchy-Schwarz says and remembering the
// problem you reached for it on are different acts. Each is worth half, so
// getting one is a draw rather than a loss: +3 / 0 / -3 on the area rating.
//
// [Invariant] The draw is a GET that *writes* — it stamps the card as seen and
// puts it on cooldown — so it does not happen on render. Rendering is not an
// event: this panel is redrawn whenever anything at all lands in the store, so
// a draw in `render` would burn cards by the handful. The draw is a boot-time
// gather in `daily.js` now, which fires once a day and only on a day you
// opened the page — the property this panel had as a modal, kept.

import * as api from "../api.js";
import { opener } from "../daily.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { typeset } from "../ui.js";

/** Loose on purpose — see the placeholder text. Matches the server's floor. */
const MIN_ANSWER = 3;

/**
 * What you have typed but not submitted, kept across re-renders.
 *
 * The panel is redrawn by any store change — adding a card in the deck panel
 * below, a live feed landing, a rollover check — and each redraw builds fresh
 * textareas. Without this, half an answer disappears because something
 * unrelated finished loading, which is the sort of thing you only notice by
 * losing a paragraph to it.
 *
 * Keyed by card, so tomorrow's draw starts empty rather than inheriting the
 * answer to a theorem you are no longer being asked about.
 */
let draft = { cardId: null, explanation: "", application: "" };

function drafted(cardId) {
  if (draft.cardId !== cardId) draft = { cardId, explanation: "", application: "" };
  return draft;
}

function draw(body) {
  const theorems = store.state.theorems;

  if (!theorems.today) {
    mount(body, undrawn(theorems));
    return;
  }
  if (theorems.written) {
    mount(body, graded(theorems));
    return;
  }
  mount(body, question(body, theorems));
}

/** Before the draw: nothing has been claimed for today yet. */
function undrawn(theorems) {
  if (!theorems.deck_size) {
    return el("div.faint", { text: "deck is empty · add a theorem in the panel below" });
  }
  return el("div", null,
    el("div.faint", { text: `${theorems.deck_size} cards in rotation` }),
    opener("theorems"),
  );
}

/** Drawn and unanswered: the name, and two boxes. */
function question(body, theorems) {
  const card = theorems.today;
  const entry = theorems.deck.find((e) => e.card.id === card.id);
  const seen = entry ? ` · seen ${entry.seen}× · recalled ${entry.recalled}×` : "";

  const held = drafted(card.id);
  const explanation = el("textarea.recall-box", {
    rows: 3,
    value: held.explanation,
    oninput: (e) => {
      held.explanation = e.target.value;
    },
    placeholder:
      "what does it say? Plain English is fine — \"the dot product's magnitude is at most "
      + "the product of the magnitudes\" is Cauchy–Schwarz.",
  });
  const application = el("textarea.recall-box", {
    rows: 3,
    value: held.application,
    oninput: (e) => {
      held.application = e.target.value;
    },
    placeholder:
      "a problem you needed it for. No need to name it — \"the polynomial that agreed with "
      + "a rational function everywhere on [0,1]\" is enough.",
  });
  const message = el("span.faint");

  const submit = el("button.primary", {
    text: "submit",
    onclick: async () => {
      const answer = {
        explanation: explanation.value.trim(),
        application: application.value.trim(),
      };
      if (answer.explanation.length < MIN_ANSWER || answer.application.length < MIN_ANSWER) {
        // Said rather than silently refused: half the rating rides on the
        // second box, and leaving it empty is a thing you might do on purpose.
        message.textContent = "both boxes — say so in the second if you cannot place it";
        return;
      }
      submit.disabled = true;
      message.textContent = "grading…";
      try {
        put("theorems", await api.post("/api/theorem/recall", answer));
        draft = { cardId: null, explanation: "", application: "" };
        draw(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  return el("div", null,
    el("div.big.gold", { text: card.name }),
    el("div.faint", { text: `${card.topic}${seen}` }),
    el("div.faint.gap", { text: "1 · the theorem, in your own words" }),
    explanation,
    el("div.faint.gap", { text: "2 · somewhere you needed it" }),
    application,
    el("div.row", null, submit, message),
  );
}

/** Graded: the verdict, what it cost or earned, and finally the statement. */
function graded(theorems) {
  const card = theorems.today;
  const written = theorems.written;
  const statement = el("div.statement", { text: card.statement });

  const half = (ok, label, yours) =>
    el("div.recall-half", null,
      el("div.row.spread", null,
        el("span", { text: label }),
        el(`span.${ok ? "ok" : "bad"}`, { text: ok ? "✓" : "✗" }),
      ),
      el("div.dim.small", { text: yours }),
    );

  const delta = theorems.delta;
  const sign = delta > 0 ? "+" : "";

  const node = el("div", null,
    el("div.row.spread", null,
      el("div.big.gold", { text: card.name }),
      // Zero is drawn as its own thing rather than "+0" — a draw is a result,
      // not a small win.
      el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
        text: delta === 0 ? "no change" : `${sign}${delta}`,
        title: `${card.topic} rating`,
      }),
    ),
    el("div.faint", { text: card.topic }),
    half(written.explained, "the theorem", written.explanation),
    half(written.applied, "where you used it", written.application),
    written.note ? el("div.dim.gap", { text: written.note }) : null,
    el("div.faint.divider", { text: "what it actually says" }),
    statement,
  );
  typeset(statement);
  return node;
}

export default {
  id: "theorem",
  title: "theorem recall",
  tab: "math",
  slot: "grid",
  span: 2,

  async render(node) {
    draw(node);
  },
};
