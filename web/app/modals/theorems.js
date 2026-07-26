// Theorem recall. The SM-2 scheduler moved to the server with the rest of the
// arithmetic; this posts a quality grade and re-reads what's due.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openModal, typeset } from "../ui.js";

const QUALITIES = [
  { quality: 1, label: "blank" },
  { quality: 2, label: "hard" },
  { quality: 3, label: "good" },
  { quality: 4, label: "easy" },
];

export function openTheoremModal() {
  openModal("theorem recall", (body) => draw(body));
}

function draw(body) {
  const theorems = store.state.theorems;
  const message = el("span.faint");

  const addCard = el("button", {
    text: "+ card from this week's topic",
    onclick: async () => {
      addCard.disabled = true;
      message.textContent = "asking…";
      try {
        put("theorems", await api.post("/api/theorem/new"));
        draw(body);
      } catch (error) {
        addCard.disabled = false;
        message.textContent = `failed: ${error.message}`;
      }
    },
  });
  const footer = el("div.row.gap", null, addCard, message);

  if (!theorems.due.length) {
    mount(body, el("div.faint", { text: `no cards due · ${theorems.deck_size} in deck` }), footer);
    return;
  }

  const card = theorems.due[0];
  const answer = el("div");

  const reveal = el("button.primary", {
    text: "reveal",
    onclick: () => {
      reveal.disabled = true;
      const statement = el("div.statement", { text: card.statement });
      mount(answer, statement,
        el("div.row", null,
          ...QUALITIES.map(({ quality, label }) =>
            el("button", {
              text: label,
              onclick: async () => {
                put("theorems", await api.post("/api/theorem/review", { card_id: card.id, quality }));
                draw(body);
              },
            }),
          ),
        ),
      );
      typeset(statement);
    },
  });

  mount(body,
    el("div.big.gold", { text: card.name }),
    el("div.faint", { text: `${card.topic} · ${theorems.due.length} due · try to recall it, then reveal` }),
    el("div.row", null, reveal),
    answer,
    footer,
  );
}
