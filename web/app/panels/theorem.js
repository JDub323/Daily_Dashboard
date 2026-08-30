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
import { openEditor, ready as termReady } from "../term.js";
import { toast, typeset } from "../ui.js";

/** Loose on purpose — see the placeholder text. Matches the server's floor. */
const MIN_ANSWER = 3;

/**
 * The editor, if one is open, and the file it is on. At most one, ever.
 *
 * Module-level for the same reason the investigation's is: this panel is
 * rebuilt by any store change at all, and a terminal rebuilt underneath you is
 * a lost buffer. The node is kept and re-attached; only closing it disposes it.
 *
 * Why an editor at all, for two textareas. Half of a good answer here is
 * `$|\langle x,y\rangle| \le \|x\|\,\|y\|$`, and a browser text box is the
 * worst place there is to proofread a line like that — no motions, no undo
 * tree, no matchparen, and a cursor you move with the arrow keys. The pty is
 * already here for the write-up, so this is the same real nvim, with the same
 * config and the same plugins, pointed at a file that is a form of these two
 * boxes rather than a document. See `dashboard/scratch.py`.
 */
let editor = null;
let scratchPath = "";

function closeEditor() {
  editor?.dispose();
  editor = null;
}

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

/**
 * Read the file back into the two boxes.
 *
 * Called however the editor ended — `:wq`, `:q!`, or the close button — because
 * all three leave a file on disk that is at least as new as what the panel is
 * holding. Nothing is submitted by this: it fills the textareas, and the submit
 * button underneath them is still the only thing that hands an answer in.
 *
 * A failed read is said out loud with the path in it. The words are on disk
 * either way, and a toast naming the file is the difference between a bad
 * moment and a lost answer.
 */
async function pullBack(cardId, body) {
  try {
    const got = await api.get("/api/theorem/scratch", { date: api.today() });
    const held = drafted(cardId);
    held.explanation = got.explanation;
    held.application = got.application;
    scratchPath = got.path;
  } catch (error) {
    toast(`could not read the file back: ${error.message} — it is at ${scratchPath}`);
  } finally {
    draw(body);
  }
}

/** The terminal, and the way out of it. */
function editorBlock(cardId, body) {
  const done = el("button", {
    text: "done — close the editor",
    onclick: () => {
      closeEditor();
      pullBack(cardId, body);
    },
  });

  return el("div.gap", null,
    editor.node,
    el("div.row", null, done),
    el("div.faint", {
      text: "esc and tab are vim's in here · alt-hjkl leaves · ctrl-w and ctrl-r "
        + "stay the browser's, so redo is U · keep the two >>> lines, they are "
        + "what splits the boxes",
    }),
    scratchPath ? el("div.faint.small", { text: scratchPath }) : null,
  );
}

/** The way into the editor. Never the way an answer is submitted. */
function writeIt(held, cardId, body, message) {
  const open = el("button", {
    text: "write it in nvim",
    title: "both boxes, in a real editor on a real pty",
    onclick: async () => {
      open.disabled = true;
      try {
        // What is in the boxes goes up first, so the buffer opens on the
        // half-answer rather than on two empty sections. The response is the
        // file as it now reads, which is what the boxes are re-seeded from --
        // one direction of truth per moment, and this is the moment the file
        // takes over.
        const got = await api.post("/api/theorem/open", {
          explanation: held.explanation,
          application: held.application,
        });
        scratchPath = got.path;
        held.explanation = got.explanation;
        held.application = got.application;
        if (!termReady()) {
          toast(`the terminal did not load — the file is at ${got.path}`);
          draw(body);
          return;
        }
        editor = openEditor({
          url: "/api/theorem/term",
          date: api.today(),
          scrollback: got.scrollback,
        });
        // `:wq` is how you finish, and the boxes should fill without being
        // asked. Guarded because the close button gets there first when it is
        // the close button that ended it, and pulling back twice would race
        // two reads of the same file into the same draft.
        editor.closed.then(() => {
          if (!editor) return;
          closeEditor();
          pullBack(cardId, body);
        });
        draw(body);
      } catch (error) {
        open.disabled = false;
        message.textContent = error.message;
      }
    },
  });
  return open;
}

/** Drawn and unanswered: the name, and two boxes. */
function question(body, theorems) {
  const card = theorems.today;
  const entry = theorems.deck.find((e) => e.card.id === card.id);
  const seen = entry ? ` · seen ${entry.seen}× · recalled ${entry.recalled}×` : "";

  const held = drafted(card.id);

  // The editor replaces the boxes rather than sitting beside them. Two live
  // copies of one answer is a way to lose half of it, and the panel is redrawn
  // by any store change at all — a textarea reflowing under a buffer you are
  // typing into would be the same bug the draft above exists to prevent.
  if (editor) {
    return el("div", null,
      el("div.big.value", { text: card.name }),
      el("div.faint", { text: `${card.topic}${seen}` }),
      editorBlock(card.id, body),
    );
  }

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
    el("div.faint.gap", { text: "the theorem" }),
    explanation.box,
    explanation.preview,
    el("div.faint.gap", { text: "the problem" }),
    application.box,
    application.preview,
    el("div.row", null, submit, writeIt(held, card.id, body, message), message),
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
  // The math tab is a single column: every card on it is prose or a formula,
  // and both would rather have the line and the vertical room than a neighbour.
  span: 6,

  async render(node) {
    draw(node);
  },
};
