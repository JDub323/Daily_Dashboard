// The spelling bee: five words a day, heard rather than read.
//
// Autocorrect is very good at hiding the fact that you have stopped being able
// to spell. This is the instrument that stops hiding it: the word is spoken,
// the hint disambiguates the homophone, and you type it cold. Five results a
// day on their own elo track, each against its own tier anchor — so the
// `brutal` word is cheap to miss and the `everyday` word is not.
//
// [Not an invariant, deliberately] The words are in the payload before the
// round is marked, unlike the math answer or the theorem statement. They have
// to be: `speechSynthesis` runs in this process, so the string it says has to
// be here. That is the same bargain the math panel already makes about its
// answer — declining to render it was never a control, only an inconvenience.
// What is not conceded is the marking, which happens on the server.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";

/** Speak the word, if this browser can. Silent no-op if it cannot. */
function say(word) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  // Cancel first: pressing twice otherwise queues a second reading behind the
  // first rather than restarting it, which reads as a stuck button.
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(word);
  utterance.lang = "en-US";
  // Slower than the word panel's 0.8. There the word is on screen and the
  // speech is a gloss; here it is the entire question.
  utterance.rate = 0.7;
  synth.speak(utterance);
}

/** What has been typed but not submitted, kept across re-renders. Keyed by
 *  date, so tomorrow's round starts empty. */
let draft = { date: null, answers: [] };

function drafted(count) {
  const date = api.today();
  if (draft.date !== date) draft = { date, answers: Array(count).fill("") };
  // A round drawn after the draft was created is longer than the array.
  while (draft.answers.length < count) draft.answers.push("");
  return draft;
}

function header(panel) {
  const { rating, k_next, measured_count, success_rate } = panel.summary;
  const detail = [`elo · K=${k_next}`];
  if (success_rate !== null) detail.push(`${success_rate}% last ${Math.min(measured_count, 10)}`);
  return el("div.row", null,
    el("span.rating", { text: rating }),
    el("span.faint", { text: detail.join(" · ") }),
  );
}

function undrawn(body, panel) {
  if (!panel.enabled) {
    return el("div.faint", { text: "not configured — see [spelling] in config.toml" });
  }
  const start = el("button.primary", {
    text: "start today's bee",
    onclick: async () => {
      start.disabled = true;
      start.textContent = "drawing…";
      try {
        put("spelling", await api.post("/api/spelling/new"));
        render(body);
      } catch (error) {
        start.disabled = false;
        start.textContent = "start today's bee";
        body.append(el("div.err", { text: error.message }));
      }
    },
  });
  return el("div", null,
    el("div.faint", { text: "five words, spoken · easiest first" }),
    el("div.row", null, start),
  );
}

/**
 * The open round: one row per word, each a speak button, a hint and a box.
 *
 * All five at once rather than one at a time. A bee that advanced word by word
 * would be a bee you cannot go back and fix a letter in, and the thing being
 * measured is whether you can spell the word — not whether you can do it
 * without second thoughts.
 */
function open(body, panel) {
  const speakable = Boolean(window.speechSynthesis);
  const held = drafted(panel.words.length);
  const inputs = [];

  const rows = panel.words.map((word, i) => {
    const input = el("input.spell-box", {
      value: held.answers[i] ?? "",
      autocomplete: "off",
      autocorrect: "off",
      autocapitalize: "off",
      spellcheck: false,
      placeholder: "spell it",
      oninput: (e) => { held.answers[i] = e.target.value; },
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        // Enter walks down the list and submits from the last box — the
        // keyboard path through the whole round without reaching for a mouse.
        if (i + 1 < inputs.length) inputs[i + 1].focus();
        else submit.click();
      },
    });
    inputs.push(input);

    return el("div.spell-row", null,
      el("div.row", null,
        el("span.faint.spell-n", { text: `${i + 1}` }),
        speakable
          ? el("button.say", { text: "🔊", title: "say it", onclick: () => say(word.word) })
          : null,
        el("span.tag", { text: word.tier }),
        input,
      ),
      word.hint ? el("div.dim.small.spell-hint", { text: word.hint }) : null,
    );
  });

  const message = el("span.faint");
  const submit = el("button.primary", {
    text: "check",
    onclick: async () => {
      submit.disabled = true;
      message.textContent = "marking…";
      try {
        put("spelling", await api.post("/api/spelling/submit", {
          answers: inputs.map((node) => node.value),
        }));
        draft = { date: null, answers: [] };
        render(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  // Autofocus is deliberate and only here: the round has just been drawn by a
  // press, so the cursor belongs in the first box rather than one click away.
  queueMicrotask(() => inputs[0]?.focus());

  return el("div", null,
    speakable
      ? null
      : el("div.err.small", { text: "this browser has no speech synthesis — the words cannot be spoken" }),
    ...rows,
    el("div.row.divider", null, submit, message),
  );
}

/** Marked: what you wrote against what it was, and what it cost. */
function marked(panel) {
  const delta = panel.delta;
  const sign = delta > 0 ? "+" : "";

  return el("div", null,
    el("div.row.spread", null,
      el("span.big.gold", { text: `${panel.correct}/${panel.words.length}` }),
      el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
        text: delta == null ? "" : delta === 0 ? "no change" : `${sign}${delta}`,
      }),
    ),
    ...panel.words.map((word, i) => {
      const ok = panel.marks[i];
      const typed = panel.answers[i] ?? "";
      return el("div.spell-row", null,
        el("div.row", null,
          el(`span.${ok ? "ok" : "bad"}`, { text: ok ? "✓" : "✗" }),
          el("span", { text: word.word }),
          el("span.faint", { text: word.tier }),
          window.speechSynthesis
            ? el("button.say", { text: "🔊", title: "say it", onclick: () => say(word.word) })
            : null,
        ),
        // Only shown when it was wrong. Echoing a correct answer back adds
        // nothing, and the row is trying to be scannable.
        ok
          ? null
          : el("div.dim.small.spell-hint", {
              text: typed ? `you wrote “${typed}”` : "left blank",
            }),
      );
    }),
  );
}

/** The words you keep getting wrong. The one line here that is not about today. */
function trouble(panel) {
  if (!panel.trouble.length) return null;
  return el("div.divider", null,
    el("div.faint", { text: "keeps catching you" }),
    el("div.dim.small", {
      text: panel.trouble.map(([word, missed, asked]) => `${word} ${missed}/${asked}`).join(" · "),
    }),
  );
}

function render(body) {
  const panel = store.state.spelling;
  let main;
  if (!panel.words.length) main = undrawn(body, panel);
  else if (panel.status === "done") main = marked(panel);
  else main = open(body, panel);

  mount(body, header(panel), main, trouble(panel));
}

export default {
  id: "spelling",
  title: "🐝 spelling bee",
  tab: "home",
  slot: "grid",

  async render(node) {
    render(node);
  },
};
