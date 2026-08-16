// Argument patterns: the theorem deck's machinery, pointed at the moves.
//
// You do not learn a pattern from a card. You learn it by being hit with one
// and not seeing it coming — which is why the ballot is asked to name the ones
// that showed up, and why the buttons at the bottom of this panel are the main
// way the deck grows. The card is what stops you forgetting it again.
//
// Same shape as theorem recall, and deliberately so: two boxes, written before
// anything is revealed, each worth half, +3 / 0 / −3. What differs is where the
// result lands — the debate track's `argument-patterns` bucket, which is not
// one of the issues, because principal-agent is not *about* immigration.
//
// [Invariant] The draw is a GET that writes, so it is a button rather than
// something that happens on mount. A panel that drew on render would burn a
// card every morning the politics tab was drawn, which is every morning.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";

/** Loose on purpose — see the placeholder text. Matches the server's floor. */
const MIN_ANSWER = 3;

/** Half-typed answers, kept across re-renders. Keyed by card, so tomorrow's
 *  draw starts empty rather than inheriting an answer to something else. */
let draft = { cardId: null, explanation: "", application: "" };

function drafted(cardId) {
  if (draft.cardId !== cardId) draft = { cardId, explanation: "", application: "" };
  return draft;
}

/** The rating this deck moves, and how much evidence is behind it. */
function header(patterns) {
  return el("div.row", null,
    el("span.rating", { text: patterns.rating }),
    el("span.faint", {
      text: patterns.events
        ? `pattern recall · ${patterns.events} graded`
        : "pattern recall · nothing graded yet — showing your debate rating",
    }),
  );
}

/** Before the draw. */
function undrawn(body, patterns) {
  if (!patterns.deck_size) {
    return el("div.faint", { text: "the deck is empty — add a pattern below" });
  }
  const button = el("button.primary", {
    text: "draw today's pattern",
    onclick: async () => {
      button.disabled = true;
      button.textContent = "drawing…";
      try {
        put("patterns", await api.get("/api/pattern/today", { date: api.today() }));
        render(body);
      } catch (error) {
        button.disabled = false;
        button.textContent = "draw today's pattern";
        body.append(el("div.err", { text: error.message }));
      }
    },
  });
  return el("div", null,
    el("div.faint", { text: `${patterns.deck_size} patterns in rotation` }),
    el("div.row", null, button),
  );
}

/** Drawn and unanswered: the name, and two boxes. */
function question(body, patterns) {
  const card = patterns.today;
  const entry = patterns.deck.find((e) => e.card.id === card.id);
  const seen = entry ? ` · seen ${entry.seen}× · recalled ${entry.recalled}×` : "";

  const held = drafted(card.id);
  const explanation = el("textarea.recall-box", {
    rows: 3,
    value: held.explanation,
    oninput: (e) => { held.explanation = e.target.value; },
    placeholder:
      "what is the move, and when does it apply? Enough that someone could spot it "
      + "in an argument.",
  });
  const application = el("textarea.recall-box", {
    rows: 3,
    value: held.application,
    oninput: (e) => { held.application = e.target.value; },
    placeholder:
      "an argument where it decided something — one you made, one made at you, or one "
      + "you read. No need to name the debate.",
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
        message.textContent = "both boxes — say so in the second if you cannot place it";
        return;
      }
      submit.disabled = true;
      message.textContent = "grading…";
      try {
        put("patterns", await api.post("/api/pattern/recall", answer));
        draft = { cardId: null, explanation: "", application: "" };
        render(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  return el("div", null,
    el("div.big.value", { text: card.name }),
    el("div.faint", { text: `${card.topic}${seen}` }),
    el("div.faint.gap", { text: "1 · the pattern, in your own words" }),
    explanation,
    el("div.faint.gap", { text: "2 · where it decided an argument" }),
    application,
    el("div.row", null, submit, message),
  );
}

/** Graded: the verdict, what it cost or earned, and finally the statement. */
function graded(patterns) {
  const card = patterns.today;
  const written = patterns.written;
  const delta = patterns.delta;
  const sign = delta > 0 ? "+" : "";

  const half = (ok, label, yours) =>
    el("div.recall-half", null,
      el("div.row.spread", null,
        el("span", { text: label }),
        el(`span.${ok ? "ok" : "bad"}`, { text: ok ? "✓" : "✗" }),
      ),
      el("div.dim.small", { text: yours }),
    );

  return el("div", null,
    el("div.row.spread", null,
      el("div.big.value", { text: card.name }),
      el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
        text: delta === 0 ? "no change" : `${sign}${delta}`,
        title: "pattern recall rating",
      }),
    ),
    el("div.faint", { text: card.topic }),
    half(written.explained, "the pattern", written.explanation),
    half(written.applied, "where it decided something", written.application),
    written.note ? el("div.dim.gap", { text: written.note }) : null,
    el("div.faint.divider", { text: "what the card says" }),
    el("div.statement", { text: card.statement }),
  );
}

/**
 * Patterns the last ballot named that are not in the deck yet.
 *
 * The most valuable thing in this panel, and the reason the ballot has a
 * `patterns` field at all: a curated deck cannot contain a pattern you did not
 * know existed, and this is the thing that noticed one. Nothing is added
 * without a press — the judge is naming what it saw, not what you should study.
 */
function suggestions(body, patterns) {
  if (!patterns.suggested.length) return null;
  return el("div.divider", null,
    el("div.faint", { text: "named in your last ballot" }),
    el("div.row", null,
      ...patterns.suggested.map((name) => {
        const button = el("button", {
          text: `+ ${name}`,
          title: "write a card for this",
          onclick: async () => {
            button.disabled = true;
            button.textContent = "writing…";
            try {
              put("patterns", await api.post("/api/pattern/add", { prompt: name }));
              render(body);
            } catch (error) {
              button.disabled = false;
              button.textContent = `+ ${name}`;
              body.append(el("div.err", { text: error.message }));
            }
          },
        });
        return button;
      }),
    ),
  );
}

/** The add box and the deck list, folded away — curation, not the day's task. */
function curation(body, patterns) {
  const input = el("input", {
    type: "text",
    placeholder: "add a pattern — e.g. motte and bailey",
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
      put("patterns", await api.post("/api/pattern/add", { prompt }));
      render(body);
    } catch (error) {
      button.disabled = input.disabled = false;
      message.textContent = `failed: ${error.message}`;
    }
  };
  button.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });

  // Longest-unseen first: the useful order for a list you are scanning to
  // decide what to retire, since a card you have never been asked is a card
  // that has been crowding the draw.
  const rows = [...patterns.deck].sort(
    (a, b) => (b.days_since ?? Infinity) - (a.days_since ?? Infinity),
  );

  const retired = patterns.deck.length - patterns.deck_size;

  return el("details.archive-item", null,
    el("summary", {
      text: `the deck · ${patterns.deck_size} in rotation${retired ? ` · ${retired} retired` : ""}`,
    }),
    el("div.row.gap", null, input, button, message),
    el("div.archive", null,
      ...rows.map((entry) =>
        el(`div.archive-item${entry.card.archived ? ".retired" : ""}`, null,
          el("div.row.spread", null,
            el("span", { text: entry.card.name }),
            el("button.tiny", {
              text: entry.card.archived ? "restore" : "retire",
              onclick: async () => {
                put("patterns", await api.post("/api/pattern/archive", {
                  card_id: entry.card.id,
                  archived: !entry.card.archived,
                }));
                render(body);
              },
            }),
          ),
          el("div.faint.small", {
            text: `${entry.card.topic} · seen ${entry.seen}× · recalled ${entry.recalled}×`
              + (entry.days_since == null ? " · never drawn" : ` · ${entry.days_since}d ago`),
          }),
        ),
      ),
    ),
  );
}

function render(body) {
  const patterns = store.state.patterns;
  let main;
  if (!patterns.today) main = undrawn(body, patterns);
  else if (patterns.written) main = graded(patterns);
  else main = question(body, patterns);

  mount(body, header(patterns), main, suggestions(body, patterns), curation(body, patterns));
}

export default {
  id: "patterns",
  title: "⚑ argument patterns",
  tab: "politics",
  slot: "grid",
  span: 2,

  async render(node) {
    render(node);
  },
};
