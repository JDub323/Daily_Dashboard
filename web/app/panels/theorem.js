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
 * A recall box that renders what you are typing.
 *
 * Plain English still grades exactly as well — the grader is told both are
 * acceptable — but "the dot product's magnitude is at most the product of the
 * magnitudes" and $|\langle x,y\rangle| \le \|x\|\,\|y\|$ are not the same
 * answer, and only the second one commits to which norm. Without a renderer
 * the second is a line of backslashes you cannot proofread, so the choice
 * between precision and legibility was being made by the input box.
 *
 * The preview sits under the textarea rather than replacing it: this is a
 * recall test, and something you have written must stay editable and visible
 * as source until you submit it. It appears only once there is a `$` in the
 * box, so nobody who writes prose ever sees an empty strip of chrome.
 *
 * Re-rendered on every keystroke, uncontroversially — KaTeX is synchronous and
 * local, these are two-line answers, and a debounce would buy nothing except a
 * preview that lags behind the cursor. `textContent` is reset first because
 * `renderMathInElement` replaces the maths it finds with rendered nodes, so
 * typesetting the output of the last pass would compound.
 */
function latexBox(rows, value, placeholder, onInput) {
  const preview = el("div.recall-preview", { hidden: true });

  const show = (text) => {
    if (!text.includes("$")) {
      preview.hidden = true;
      return;
    }
    preview.hidden = false;
    preview.textContent = text;
    typeset(preview);
  };

  const box = el("textarea.recall-box", {
    rows,
    value,
    placeholder,
    oninput: (e) => {
      onInput(e.target.value);
      show(e.target.value);
    },
  });

  // A draft restored across a re-render has to render immediately: the panel
  // is redrawn by any store change at all, and a preview that came back blank
  // under a box that still had text in it would read as having lost it.
  show(value);

  return { box, preview };
}

/** What both boxes say about notation. Once, because they say the same thing. */
const LATEX_HINT = "LaTeX renders — $x^2$ inline, $$…$$ on its own line";

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
  const explanation = latexBox(
    3,
    held.explanation,
    "what does it say? Plain English is fine — \"the dot product's magnitude is at most "
      + "the product of the magnitudes\" is Cauchy–Schwarz. LaTeX if you want to be exact.",
    (value) => {
      held.explanation = value;
    },
  );
  const application = latexBox(
    3,
    held.application,
    "a problem you needed it for. No need to name it — \"the polynomial that agreed with "
      + "a rational function everywhere on [0,1]\" is enough.",
    (value) => {
      held.application = value;
    },
  );
  const message = el("span.faint");

  const submit = el("button.primary", {
    text: "submit",
    onclick: async () => {
      // Sent as source, not as what the preview drew. The grader is told to
      // read `$..$` as the mathematics it denotes, and the stored answer is
      // what gets rendered back on the graded card — so the LaTeX has to
      // survive the round trip rather than being flattened on the way out.
      const answer = {
        explanation: explanation.box.value.trim(),
        application: application.box.value.trim(),
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
    el("div.big.value", { text: card.name }),
    el("div.faint", { text: `${card.topic}${seen}` }),
    el("div.row.spread.gap", null,
      el("span.faint", { text: "1 · the theorem, in your own words" }),
      el("span.faint.small", { text: LATEX_HINT }),
    ),
    explanation.box,
    explanation.preview,
    el("div.faint.gap", { text: "2 · somewhere you needed it" }),
    application.box,
    application.preview,
    el("div.row", null, submit, message),
  );
}

/** Graded: the verdict, what it cost or earned, and finally the statement. */
function graded(theorems) {
  const card = theorems.today;
  const written = theorems.written;
  const statement = el("div.statement", { text: card.statement });

  // Your own answer is typeset with the same renderer the statement below it
  // gets. Showing the source back would undo the point of being able to write
  // it: the card is what you read when checking whether the grader was fair,
  // and comparing rendered maths against raw backslashes is not that.
  const half = (ok, label, yours) => {
    const written = el("div.dim.small", { text: yours });
    typeset(written);
    return el("div.recall-half", null,
      el("div.row.spread", null,
        el("span", { text: label }),
        el(`span.${ok ? "ok" : "bad"}`, { text: ok ? "✓" : "✗" }),
      ),
      written,
    );
  };

  const delta = theorems.delta;
  const sign = delta > 0 ? "+" : "";

  const node = el("div", null,
    el("div.row.spread", null,
      el("div.big.value", { text: card.name }),
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
