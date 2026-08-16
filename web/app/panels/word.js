// Word of the day: Claude picks one, you write sentences, Claude grades them.
//
// On the home tab rather than with math. It is vocabulary, not problem
// solving, and the thing it actually has in common with the heatmap and the
// day log is that it is part of the morning rather than part of a subject.

import * as api from "../api.js";
import { opener } from "../daily.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";

const SENTENCES = 2;
/** Must match `domain.SENTENCES` — the server's WordCheckRequest requires
 * exactly this many, so a mismatch here is a 422 rather than a short list. */

// --- saying it out loud ----------------------------------------------------
//
// `speechSynthesis` is built into the browser: no audio files to serve, no
// network call, no API key, and it keeps the app's "no external runtime
// dependency" property intact. It is also the only pronunciation that helps
// if you do not read IPA and the respelling still leaves you guessing.

/** Speak the word, if this browser can. Silent no-op if it cannot. */
function say(word) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  // Cancel first: pressing the button twice otherwise queues a second reading
  // behind the first rather than restarting it, which reads as a stuck button.
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(word);
  utterance.lang = "en-US";
  utterance.rate = 0.8; // a word said at conversational speed is a word missed
  synth.speak(utterance);
}

/**
 * `susurrus noun` over `suh-SUR-us · /səˈsʌrəs/ 🔊` — the header both the
 * editor and the graded view show.
 *
 * Every part below the word is optional and independently so: rows generated
 * before the pronunciation columns existed have neither, a generation can
 * come back with one and not the other, and a browser without speech
 * synthesis gets no button. None of those are error states, so none of them
 * render as one — the line simply carries less.
 */
function wordHead(word) {
  const speakable = Boolean(window.speechSynthesis);
  const parts = [word.respelling, word.pronunciation].filter(Boolean);

  return el("div", null,
    el("div.big.value", null, word.word, " ", el("span.faint", { text: word.pos })),
    parts.length || speakable
      ? el("div.row.pronounce", null,
          parts.length ? el("span.dim", { text: parts.join(" · ") }) : null,
          speakable
            ? el("button.say", {
                text: "🔊 say it",
                title: `hear "${word.word}" read aloud`,
                onclick: () => say(word.word),
              })
            : null,
        )
      : null,
  );
}

function draw(body) {
  const word = store.state.word;

  if (!word) {
    // The word is fetched at boot by `daily.js`, so this is normally a busy
    // line for a second or two and then the editor. The button it falls back
    // to is the same one that used to be the only way in.
    mount(body, opener("word"));
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
    wordHead(word),
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
    wordHead(word),
    el("div.dim", { text: word.definition }),
    el("div.faint.gap", { text: `write ${SENTENCES} semantically distinct sentences:` }),
    ...inputs,
    el("div.row", null, check, message),
  );
}

export default {
  id: "word",
  title: "word of the day",
  tab: "home",
  slot: "grid",
  span: 2,

  async render(node) {
    draw(node);
  },
};
