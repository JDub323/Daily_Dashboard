// Theorem recall. One theorem a day, drawn server-side from a deck you curate.
//
// This replaced an SM-2 queue whose UI was "here are the 0-6 cards that came
// due". The deck now answers a different question — of everything I have told
// it to keep, which one today — so the modal has exactly one card in it, and
// the interesting affordance is the add box rather than the grade buttons.
//
// The draw is a GET that writes, so it happens when this modal opens rather
// than at boot: a day you never came looking is a day nothing appeared.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openModal, typeset } from "../ui.js";

export function openTheoremModal() {
  openModal("theorem recall", async (body) => {
    mount(body, el("div.faint", { text: "drawing…" }));
    try {
      put("theorems", await api.get("/api/theorem/today", { date: api.today() }));
    } catch (error) {
      mount(body, el("div.err", { text: error.message }));
      return;
    }
    draw(body);
  });
}

const ago = (days) => {
  if (days == null) return "never seen";
  // <= 0 rather than === 0: the browser is the authority on today's date, so
  // a clock that moved backwards can leave a draw dated after it. The draw
  // itself already treats that as on-cooldown; this stops it rendering as
  // "-3d ago", which reads as a bug in the arithmetic rather than the clock.
  if (days <= 0) return "today";
  return days === 1 ? "yesterday" : `${days}d ago`;
};

function draw(body) {
  const theorems = store.state.theorems;
  mount(body, card(body, theorems), addRow(body), deckList(body, theorems));
}

/** Today's theorem: name, then reveal, then the one-bit self-check. */
function card(body, theorems) {
  if (!theorems.today) {
    return el("div.faint", {
      text: theorems.deck_size
        ? "nothing drawn — reopen to try again"
        : "deck is empty · add a theorem below",
    });
  }

  const entry = theorems.deck.find((e) => e.card.id === theorems.today.id);
  const seen = entry ? ` · seen ${entry.seen}× · recalled ${entry.recalled}×` : "";
  const answer = el("div");

  const reveal = el("button.primary", {
    text: "reveal",
    onclick: () => {
      reveal.disabled = true;
      const statement = el("div.statement", { text: theorems.today.statement });
      mount(answer, statement, checkRow(body, theorems));
      typeset(statement);
    },
  });

  // Answered already: the statement is the point of reopening, so it is shown
  // outright rather than hidden behind a reveal you have already earned.
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

function checkRow(body, theorems) {
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

  return el("div", null,
    el("div.row.gap", null, input, button),
    el("div.row.gap", null, fromTopic, message),
  );
}

/** The deck, with its odds visible and an archive toggle per row. */
function deckList(body, theorems) {
  if (!theorems.deck.length) return null;

  const toggle = async (entry) => {
    put("theorems", await api.post("/api/theorem/archive", {
      card_id: entry.card.id,
      archived: !entry.card.archived,
    }));
    draw(body);
  };

  // Longest-unseen first: that is draw order in expectation, so the list
  // reads as "what is coming up" rather than as insertion order.
  const rows = [...theorems.deck].sort(
    (a, b) => (b.days_since ?? Infinity) - (a.days_since ?? Infinity),
  );

  return el("details", null,
    el("summary.faint", { text: `deck · ${theorems.deck_size} active` }),
    ...rows.map((entry) =>
      el("div.row.gap", { class: entry.card.archived ? "faint" : null },
        el("span", { text: entry.card.name }),
        el("span.faint", { text: ago(entry.days_since) }),
        el("button", {
          text: entry.card.archived ? "restore" : "retire",
          onclick: () => toggle(entry),
        }),
      ),
    ),
  );
}
