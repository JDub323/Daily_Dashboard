// Theorem recall. One theorem a day, drawn server-side from a deck you curate.
//
// This replaced an SM-2 queue whose UI was "here are the 0-6 cards that came
// due". The deck answers a different question — of everything I have told it
// to keep, which one today — so there is exactly one card here, and the
// interesting affordance is the add box rather than the grade buttons.
//
// [Invariant] The draw is a GET that *writes*: it stamps the card as seen and
// puts it on cooldown. That is why there is a button here rather than a draw
// on render. As a modal this was safe by construction — a day you never came
// looking was a day nothing appeared — and a panel that drew on mount would
// quietly burn a card every morning the math tab happened to be rendered,
// which is every morning. The button is the modal's `open` made explicit.
//
// Browsing the whole deck stays a modal: it is a reference activity consulted
// once a fortnight, and it would hold a panel's worth of tab space daily.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { openTheoremArchive } from "../modals/theorems.js";
import { put, store } from "../store.js";
import { typeset } from "../ui.js";

function draw(body) {
  const theorems = store.state.theorems;
  mount(body, card(body, theorems), addRow(body), archiveLink(theorems));
}

/** The way through to the archive, in place of the old inline deck list. */
function archiveLink(theorems) {
  if (!theorems.deck.length) return null;
  return el("div.row.gap", null,
    el("button", {
      text: `browse all ${theorems.deck.length} →`,
      onclick: openTheoremArchive,
    }),
    el("span.faint", { text: `${theorems.deck_size} in rotation` }),
  );
}

/** Today's theorem: name, then reveal, then the one-bit self-check. */
function card(body, theorems) {
  if (!theorems.today) {
    if (!theorems.deck_size) {
      return el("div.faint", { text: "deck is empty · add a theorem below" });
    }
    const drawButton = el("button.primary", {
      text: "draw today's theorem",
      onclick: async () => {
        drawButton.disabled = true;
        drawButton.textContent = "drawing…";
        try {
          put("theorems", await api.get("/api/theorem/today", { date: api.today() }));
          draw(body);
        } catch (error) {
          drawButton.disabled = false;
          drawButton.textContent = "draw today's theorem";
          body.append(el("div.err", { text: error.message }));
        }
      },
    });
    return el("div", null,
      el("div.faint", { text: `${theorems.deck_size} cards in rotation` }),
      el("div.row", null, drawButton),
    );
  }

  const entry = theorems.deck.find((e) => e.card.id === theorems.today.id);
  const seen = entry ? ` · seen ${entry.seen}× · recalled ${entry.recalled}×` : "";
  const answer = el("div");

  const reveal = el("button.primary", {
    text: "reveal",
    onclick: () => {
      reveal.disabled = true;
      const statement = el("div.statement", { text: theorems.today.statement });
      mount(answer, statement, checkRow(body));
      typeset(statement);
    },
  });

  // Answered already: the statement is the point of coming back, so it is
  // shown outright rather than hidden behind a reveal you have already earned.
  if (theorems.recalled !== null) {
    const statement = el("div.statement", { text: theorems.today.statement });
    const node = el("div", null,
      el("div.big.gold", { text: theorems.today.name }),
      el("div.faint", { text: `${theorems.today.topic}${seen}` }),
      el(theorems.recalled ? "div.ok" : "div.bad", {
        text: theorems.recalled ? "remembered ✓" : "missed ✗",
      }),
      statement,
    );
    typeset(statement);
    return node;
  }

  return el("div", null,
    el("div.big.gold", { text: theorems.today.name }),
    el("div.faint", { text: `${theorems.today.topic}${seen} · recall it, then reveal` }),
    el("div.row", null, reveal),
    answer,
  );
}

function checkRow(body) {
  const mark = async (recalled) => {
    put("theorems", await api.post("/api/theorem/recall", { recalled }));
    draw(body);
  };
  return el("div.row", null,
    el("button.ok", { text: "remembered", onclick: () => mark(true) }),
    el("button.bad", { text: "didn't", onclick: () => mark(false) }),
  );
}

/** "add Vandermonde's" — the whole reason the deck is worth having. */
function addRow(body) {
  const input = el("input", {
    type: "text",
    placeholder: "add a theorem — e.g. roots of unity filter",
    maxlength: 200,
  });
  const message = el("span.faint");
  const button = el("button", { text: "add" });

  const submit = async () => {
    const prompt = input.value.trim();
    if (prompt.length < 2) return;
    button.disabled = input.disabled = true;
    message.textContent = "asking…";
    try {
      put("theorems", await api.post("/api/theorem/add", { prompt }));
      draw(body);
    } catch (error) {
      button.disabled = input.disabled = false;
      message.textContent = `failed: ${error.message}`;
    }
  };

  button.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });

  const fromTopic = el("button", {
    text: "+ from this week's topic",
    onclick: async () => {
      fromTopic.disabled = true;
      message.textContent = "asking…";
      try {
        put("theorems", await api.post("/api/theorem/new"));
        draw(body);
      } catch (error) {
        fromTopic.disabled = false;
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  return el("div.divider", null,
    el("div.row.gap", null, input, button),
    el("div.row.gap", null, fromTopic, message),
  );
}

export default {
  id: "theorem",
  title: "theorem recall",
  tab: "math",
  slot: "grid",

  async render(node) {
    draw(node);
  },
};
