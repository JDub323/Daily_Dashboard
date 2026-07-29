// Theorem recall. One theorem a day, drawn server-side from a deck you curate.
//
// This replaced an SM-2 queue whose UI was "here are the 0-6 cards that came
// due". The deck now answers a different question — of everything I have told
// it to keep, which one today — so the modal has exactly one card in it, and
// the interesting affordance is the add box rather than the grade buttons.
//
// The draw is a GET that writes, so it happens when this modal opens rather
// than at boot: a day you never came looking is a day nothing appeared.
//
// Two modes, not two surfaces. Browsing the whole deck is a reference
// activity, so it does not want a dashboard panel — the columns are for
// things you glance at daily, and an archive consulted once a fortnight would
// hold that space permanently. It does not want its own chip either; the chip
// row is the day's checklist, and reading old theorems is not a task you
// complete. What it wants is to be where theorems already are, which is here.
// `#modal` is already `max-height: 84vh; overflow-y: auto` with a sticky
// head, so a long list scrolls correctly with no new CSS for the container.
//
// The archive replaces the collapsed deck list that used to sit under the
// add box. That list had every card in it already, but three lines tall and
// competing with today's draw — this is that list given the room to be read.

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

/**
 * The archive. Re-entering `openModal` swaps the title and body in place, so
 * the two modes are one overlay rather than a modal stacked on a modal.
 *
 * Needs no fetch: the deck ships whole in the boot payload, so this opens
 * populated even on a day the draw has not happened.
 */
export function openTheoremArchive() {
  openModal("theorem archive", (body) => drawArchive(body));
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

// --- the archive -----------------------------------------------------------

const ORDERS = {
  recent: {
    label: "recently seen",
    // Never-seen sink rather than lead: this order answers "what have I been
    // doing", and a card with no history has no place in that story yet.
    compare: (a, b) => (a.days_since ?? Infinity) - (b.days_since ?? Infinity),
  },
  stale: {
    label: "longest unseen",
    compare: (a, b) => (b.days_since ?? Infinity) - (a.days_since ?? Infinity),
  },
  added: {
    label: "newest first",
    compare: (a, b) => b.card.id - a.card.id,
  },
};

let order = "recent";

function drawArchive(body) {
  const theorems = store.state.theorems;
  const retired = theorems.deck.length - theorems.deck_size;

  if (!theorems.deck.length) {
    mount(body,
      el("div.faint", { text: "no theorems yet" }),
      el("div.row.gap", null, el("button", { text: "← today", onclick: openTheoremModal })),
    );
    return;
  }

  const sorted = [...theorems.deck].sort(ORDERS[order].compare);

  mount(body,
    el("div.row.spread", null,
      el("span.faint", {
        text: `${theorems.deck.length} theorems · ${theorems.deck_size} in rotation`
          + (retired ? ` · ${retired} retired` : ""),
      }),
      el("button", { text: "← today", onclick: openTheoremModal }),
    ),
    el("div.row.gap", null,
      el("span.faint", { text: "sort:" }),
      ...Object.entries(ORDERS).map(([key, { label }]) =>
        el(`button${key === order ? ".primary" : ""}`, {
          text: label,
          onclick: () => {
            order = key;
            drawArchive(body);
          },
        }),
      ),
    ),
    el("div.archive", null, ...sorted.map((entry) => archiveEntry(body, entry))),
  );
}

/** One row: meta always visible, statement behind a disclosure. */
function archiveEntry(body, entry) {
  const card = entry.card;
  const statement = el("div.statement", { text: card.statement });

  // Lives in the details body, not the summary, so it needs no guard against
  // toggling the disclosure -- only a summary click does that. Retiring
  // therefore costs opening the card first, which suits an action you should
  // take after rereading the statement rather than off a name alone.
  const toggle = el("button", {
    text: card.archived ? "restore" : "retire",
    onclick: async () => {
      put("theorems", await api.post("/api/theorem/archive", {
        card_id: card.id,
        archived: !card.archived,
      }));
      drawArchive(body);
    },
  });

  const node = el("details.archive-item", null,
    el("summary", null,
      el("span", { class: card.archived ? "faint" : null, text: card.name }),
      el("span.faint", {
        text: ` · ${ago(entry.days_since)} · seen ${entry.seen}×`
          + ` · recalled ${entry.recalled}×${card.archived ? " · retired" : ""}`,
      }),
    ),
    statement,
    el("div.row.spread", null,
      el("span.faint", { text: `${card.topic} · added ${card.added}` }),
      toggle,
    ),
  );

  // Typeset the one you opened, not all of them. KaTeX over a hundred
  // statements on mount is seconds of blocked main thread for content that is
  // mostly still scrolled off screen.
  node.addEventListener("toggle", () => typeset(statement), { once: true });
  return node;
}
