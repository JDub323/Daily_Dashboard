import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openModal } from "../ui.js";

const SENTENCES = 2;
/** Must match `domain.SENTENCES` — the server's WordCheckRequest requires
 * exactly this many, so a mismatch here is a 422 rather than a short list. */

export function openWordModal() {
  openModal("word of the day", (body) => draw(body));
}

function draw(body) {
  const word = store.state.word;

  if (!word) {
    const button = el("button.primary", {
      text: "get today's word",
      onclick: async () => {
        button.disabled = true;
        button.textContent = "asking…";
        try {
          put("word", await api.post("/api/word/today"));
          draw(body);
        } catch (error) {
          mount(body, el("div.err", { text: error.message }));
        }
      },
    });
    mount(body, button);
    return;
  }

  if (word.grade) {
    drawGrade(body, word);
    return;
  }

  drawEditor(body, word);
}

function drawGrade(body, word) {
  const grade = word.grade;
  mount(body,
    el("div.big.gold", { text: word.word }),
    el("div.dim", { text: word.definition }),
    el(`div.${grade.distinct ? "ok" : "bad"}.verdict`, { text: grade.verdict }),
    el("ul.clean", null,
      ...grade.per_sentence.map((result, i) =>
        el("li", null,
          el(`span.${result.ok ? "ok" : "bad"}`, { text: result.ok ? "✓" : "✗" }), " ",
          word.sentences[i] ?? "", " ",
          el("span.faint", { text: result.note }),
        ),
      ),
    ),
    grade.distinct
      ? null
      : el("div.row", null,
          el("button", {
            text: "edit & retry",
            // Clearing the grade locally is enough: the next check overwrites
            // it server-side, and nothing else reads it in between.
            onclick: () => {
              put("word", { ...word, grade: null });
              draw(body);
            },
          }),
        ),
  );
}

function drawEditor(body, word) {
  const inputs = Array.from({ length: SENTENCES }, (_, i) =>
    el("input.sentence", {
      value: word.sentences?.[i] ?? "",
      onchange: () => api.post("/api/word/sentences", { sentences: values() }).then((w) => put("word", w)),
    }),
  );
  const values = () => inputs.map((input) => input.value.trim());
  const message = el("span.faint");

  const check = el("button.primary", {
    text: "check",
    onclick: async () => {
      const sentences = values();
      if (sentences.some((s) => !s)) {
        message.textContent = `need all ${SENTENCES}`;
        return;
      }
      check.disabled = true;
      message.textContent = "judging…";
      try {
        const { grade } = await api.post("/api/word/check", { word: word.word, sentences });
        put("word", { ...word, sentences, grade });
        draw(body);
      } catch (error) {
        check.disabled = false;
        message.textContent = `failed: ${error.message}`;
      }
    },
  });

  mount(body,
    el("div.big.gold", null, word.word, " ", el("span.faint", { text: word.pos })),
    el("div.dim", { text: word.definition }),
    el("div.faint.gap", { text: `write ${SENTENCES} semantically distinct sentences:` }),
    ...inputs,
    el("div.row", null, check, message),
  );
}
