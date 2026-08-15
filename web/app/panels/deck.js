// The theorem deck: adding to it, and the way through to browsing it.
//
// Split out of the recall panel, which had the add box sitting under the
// reveal button. The two share a subject and nothing else: one is the day's
// question, the other is curation you do whenever a theorem you did not know
// turns up in something else. Putting them in one card meant the text field
// under "reveal" was for something entirely unrelated to what you had just
// been asked, which is the kind of adjacency that makes you type in the wrong
// box once and never quite trust either again.
//
// "add Vandermonde's" — a name, not a statement. The point of the deck is that
// adding to it costs one phrase typed while the problem you just met it in is
// still on screen. Writing the statement out is what stopped the old deck from
// growing, so the LLM does it.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { openTheoremArchive } from "../modals/theorems.js";
import { put, store } from "../store.js";

function draw(body) {
  const theorems = store.state.theorems;

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

  const retired = theorems.deck.length - theorems.deck_size;

  mount(body,
    el("div.row.gap", null, input, button),
    el("div.row.gap", null, fromTopic, message),
    theorems.deck.length
      ? el("div.row.spread.divider", null,
          el("span.faint", {
            text: `${theorems.deck_size} in rotation${retired ? ` · ${retired} retired` : ""}`,
          }),
          el("button", {
            text: `browse all ${theorems.deck.length} →`,
            onclick: openTheoremArchive,
          }),
        )
      : el("div.faint.divider", { text: "the deck is empty" }),
  );
}

export default {
  id: "deck",
  title: "theorem deck",
  tab: "math",
  slot: "grid",
  span: 2,

  async render(node) {
    draw(node);
  },
};
