// The theorem archive: the whole deck, sorted, with statements on disclosure.
//
// Still a modal after the tab rewrite, and deliberately. Everything else that
// was behind a modal became a panel, because tabs gave it somewhere to live —
// but browsing the deck is a reference activity you do once a fortnight, and a
// panel is space spent every day. It also wants to be reachable from two
// places at once (the math panel's archive link and the recall panel), which
// is what an overlay is for.
//
// `#modal` is already `max-height: 84vh; overflow-y: auto` with a sticky head,
// so a long list scrolls correctly with no new CSS for the container.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { focusPanel } from "../panel.js";
import { put, store } from "../store.js";
import { closeModal, openModal, typeset } from "../ui.js";

/**
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

/** Back to the card of the day, which is a panel on the math tab now. */
function backToToday() {
  closeModal();
  focusPanel("theorem");
}

function drawArchive(body) {
  const theorems = store.state.theorems;
  const retired = theorems.deck.length - theorems.deck_size;

  if (!theorems.deck.length) {
    mount(body,
      el("div.faint", { text: "no theorems yet" }),
      el("div.row.gap", null, el("button", { text: "← today", onclick: backToToday })),
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
      el("button", { text: "← today", onclick: backToToday }),
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
  // Today's card arrives with an empty statement until the recall is graded —
  // the server withholds it rather than trusting this list not to show it. Say
  // so, or the one blank card reads as a broken row.
  const statement = card.statement
    ? el("div.statement", { text: card.statement })
    : el("div.faint.statement", { text: "today's card — answer the recall to see it" });

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
