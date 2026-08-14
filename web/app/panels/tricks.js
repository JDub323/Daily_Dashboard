// The trick of the day: one identity from the week's topic, and the set of
// problems that puts it in your hands.
//
// The theorem deck asks you to recall a statement. This asks you to *use* one,
// which is a different act and the reason the two panels sit next to each other
// rather than being one panel: a theorem you can state and have never applied is
// exactly what this is for.
//
// [Invariant] The answers are not in the payload until the set is marked. The
// marking is a string comparison the server performs, so shipping the answers
// with the problems would put the answer key in the page — worse than the
// theorem panel's old reveal button, which at least required a click. The
// trick's *statement* is not withheld, and that is not an inconsistency: it is
// the thing being taught, not the thing being tested.
//
// [Invariant] Drawing is a button, not a render. On a morning the overnight
// task did not run, drawing spends a live generation — so a panel that drew on
// mount would spend money every time the math tab was painted.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { openTrickArchive } from "../modals/tricks.js";
import { put, store } from "../store.js";
import { typeset } from "../ui.js";

/** Matches the server's floor on the challenge box. */
const MIN_ANSWER = 3;

/**
 * What has been typed but not submitted, kept across re-renders.
 *
 * The panel is redrawn by any store change — a feed landing, a rollover check,
 * the theorem panel above it being answered — and each redraw builds fresh
 * inputs. Without this, eight worked answers vanish because something unrelated
 * finished loading. Keyed by date, so tomorrow's set starts empty.
 */
let draft = { date: null, answers: [], challenge: "" };

function drafted(count) {
  const date = api.today();
  if (draft.date !== date) draft = { date, answers: Array(count).fill(""), challenge: "" };
  while (draft.answers.length < count) draft.answers.push("");
  return draft;
}

function header(panel) {
  const detail = [
    panel.events
      ? `${panel.events} result${panel.events === 1 ? "" : "s"}`
      : "no results yet — your math rating",
  ];
  if (panel.total) detail.push(`${panel.total} trick${panel.total === 1 ? "" : "s"} learned`);
  return el("div.row", null,
    el("span.rating", { text: panel.rating }),
    el("span.faint", { text: detail.join(" · ") }),
  );
}

function archiveLink() {
  return el("div.row.divider", null,
    el("button", { text: "past tricks →", onclick: openTrickArchive }),
  );
}

/** The card itself: what the trick says, and when to reach for it. */
function card(trick, callbackOf) {
  const statement = el("div.statement", { text: trick.statement });
  const node = el("div.trick-card", null,
    el("div.row.spread", null,
      el("span.big.gold", { text: trick.name }),
      // Said out loud rather than smoothed over. A `technique` day means the
      // week's topic had no computational shortcut in it, and knowing that is
      // the difference between "today was thin" and "today was a different
      // kind of thing".
      el("span.tag", { text: trick.kind }),
    ),
    el("div.faint", { text: trick.topic }),
    statement,
    trick.idea ? el("div.dim.small", { text: trick.idea }) : null,
    callbackOf
      ? el("div.faint.small", { text: `one problem below is a callback to ${callbackOf}` })
      : null,
  );
  // The whole card, not only the statement: `idea` is prose about a formula and
  // routinely has an inline `$p$` in it, which typesetting the statement alone
  // leaves on screen as a literal dollar sign.
  typeset(node);
  return node;
}

/** Before the draw. */
function undrawn(body, panel) {
  if (!panel.enabled) {
    return el("div.faint", { text: "not configured — see [tricks] in config.toml" });
  }
  const start = el("button.primary", {
    text: "learn today's trick",
    onclick: async () => {
      start.disabled = true;
      start.textContent = "writing today's set…";
      try {
        put("tricks", await api.post("/api/trick/new"));
        render(body);
      } catch (error) {
        start.disabled = false;
        start.textContent = "learn today's trick";
        body.append(el("div.err", { text: error.message }));
      }
    },
  });
  return el("div", null,
    el("div.faint", {
      text: `from this week's topic: ${store.config.topic}`,
    }),
    // The price is printed because it is sometimes real. Free from the
    // overnight batch, a live generation otherwise, and there is no way for
    // the panel to know which until it asks.
    el("div.faint.small", { text: "free if the 3am task ran · one generation if it did not" }),
    el("div.row", null, start),
  );
}

const BAND_LABEL = { easy: "easy", medium: "medium", callback: "callback", challenge: "challenge" };

/** One problem of the open set: its number, its band, the prompt, a box. */
function askRow(problem, i, input) {
  const prompt = el("div.trick-prompt", { text: problem.prompt });
  const row = el("div.trick-row", null,
    el("div.row", null,
      el("span.faint.spell-n", { text: `${i + 1}` }),
      el("span.tag", { text: BAND_LABEL[problem.band] ?? problem.band }),
      problem.callback_of
        ? el("span.faint.small", { text: `↩ ${problem.callback_of}` })
        : null,
    ),
    prompt,
    input,
  );
  typeset(prompt);
  return row;
}

/**
 * The open set: eight boxes and one button.
 *
 * All of them at once rather than one at a time, on the spelling bee's
 * reasoning: the thing being measured is whether the trick is in your hands,
 * not whether you can apply it without going back to fix a sign.
 */
function open(body, panel) {
  const held = drafted(panel.problems.length);
  const inputs = [];

  const rows = panel.problems.map((problem, i) => {
    const input = el("input.trick-box", {
      value: held.answers[i] ?? "",
      autocomplete: "off",
      spellcheck: false,
      placeholder: "answer",
      oninput: (e) => { held.answers[i] = e.target.value; },
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        // Enter walks down the set and submits from the last box — the
        // keyboard path through the whole thing, exactly as the bee's does.
        if (i + 1 < inputs.length) inputs[i + 1].focus();
        else submit.click();
      },
    });
    inputs.push(input);
    return askRow(problem, i, input);
  });

  const message = el("span.faint");
  const submit = el("button.primary", {
    text: "check",
    onclick: async () => {
      submit.disabled = true;
      message.textContent = "marking…";
      try {
        put("tricks", await api.post("/api/trick/submit", {
          answers: inputs.map((node) => node.value),
        }));
        draft = { date: null, answers: [], challenge: "" };
        render(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div", null,
    ...rows,
    el("div.row.divider", null, submit, message,
      el("span.faint.small", { text: "a blank counts as wrong" })),
  );
}

/** Marked: what you wrote, what it was, and the line that shows the trick. */
function marked(panel) {
  return el("div", null,
    ...panel.problems.map((problem, i) => {
      const ok = problem.correct;
      const prompt = el("div.trick-prompt.dim", { text: problem.prompt });
      const answer = el("div.small", null,
        el("span.faint", { text: "answer: " }),
        el("span", { text: problem.answer }),
      );
      const node = el("div.trick-row", null,
        el("div.row", null,
          el(`span.${ok ? "ok" : "bad"}`, { text: ok ? "✓" : "✗" }),
          el("span.faint.spell-n", { text: `${i + 1}` }),
          el("span.tag", { text: BAND_LABEL[problem.band] ?? problem.band }),
          problem.callback_of ? el("span.faint.small", { text: `↩ ${problem.callback_of}` }) : null,
        ),
        prompt,
        answer,
        // Only when it was wrong. Echoing a correct answer back adds nothing,
        // and the list is trying to be scannable — the bee's rule.
        ok ? null : el("div.dim.small", {
          text: problem.typed ? `you wrote “${problem.typed}”` : "left blank",
        }),
        // The worked line, on the ones that need it. This is where the trick
        // is actually taught: a wrong answer with no route shown teaches the
        // answer and not the move.
        ok || !problem.solution ? null : el("div.dim.small.trick-solution", { text: problem.solution }),
      );
      typeset(node);
      return node;
    }),
  );
}

/**
 * The optional problem, kept apart from the set.
 *
 * Its own box and its own button because it is answered at a different time:
 * this is the one you go away and think about, and folding it into the set
 * would mean either submitting the day early or holding the other eight
 * hostage to it.
 */
function challenge(body, panel) {
  const problem = panel.challenge;
  if (!problem) return null;

  const prompt = el("div.trick-prompt", { text: problem.prompt });
  const head = el("div.row.spread", null,
    el("span.gold", { text: "challenge" }),
    el("span.faint.small", { text: "optional · needs more than the trick" }),
  );

  if (panel.challenge_ok !== null) {
    const solution = problem.solution ? el("div.dim.small.trick-solution", { text: problem.solution }) : null;
    const node = el("div.trick-challenge.divider", null,
      head,
      prompt,
      el("div.row", null,
        el(`span.${panel.challenge_ok ? "ok" : "bad"}`, { text: panel.challenge_ok ? "✓" : "✗" }),
        el("span.small", { text: `answer: ${problem.answer}` }),
      ),
      panel.challenge_note ? el("div.dim.small", { text: panel.challenge_note }) : null,
      solution,
    );
    typeset(node);
    return node;
  }

  const held = drafted(panel.problems.length);
  const box = el("textarea.recall-box", {
    rows: 3,
    value: held.challenge,
    placeholder: "your answer, and the step that got you there",
    oninput: (e) => { held.challenge = e.target.value; },
  });
  const message = el("span.faint");
  const submit = el("button", {
    text: "submit challenge",
    onclick: async () => {
      if (box.value.trim().length < MIN_ANSWER) {
        message.textContent = "write something first — it is optional, not blank";
        return;
      }
      submit.disabled = true;
      message.textContent = "grading…";
      try {
        put("tricks", await api.post("/api/trick/challenge", { answer: box.value.trim() }));
        draft = { date: null, answers: [], challenge: "" };
        render(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  const node = el("div.trick-challenge.divider", null,
    head,
    prompt,
    box,
    el("div.row", null, submit, message),
  );
  typeset(node);
  return node;
}

/** The score line, once the set is closed. */
function scoreLine(panel) {
  const delta = panel.delta;
  const sign = delta > 0 ? "+" : "";
  return el("div.row.spread", null,
    el("span.big.gold", { text: `${panel.correct}/${panel.problems.length}` }),
    el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
      text: delta == null ? "" : delta === 0 ? "no change" : `${sign}${delta}`,
      title: "trick rating",
    }),
  );
}

function render(body) {
  const panel = store.state.tricks;
  if (!panel.trick) {
    mount(body, header(panel), undrawn(body, panel));
    return;
  }
  const done = panel.status !== "open";
  mount(body,
    header(panel),
    card(panel.trick, panel.callback_of),
    done ? scoreLine(panel) : null,
    done ? marked(panel) : open(body, panel),
    challenge(body, panel),
    archiveLink(),
  );
}

export default {
  id: "tricks",
  title: "◆ trick of the day",
  tab: "math",
  slot: "grid",
  collapsible: true,

  /** The one line the panel keeps once the day's set is done. */
  summary() {
    const panel = store.state.tricks;
    if (!panel.trick) return `${panel.rating}`;
    if (panel.status === "open") return `${panel.rating} · ${panel.trick.name}`;
    const delta = panel.delta == null ? "" : ` ${panel.delta >= 0 ? "+" : ""}${panel.delta}`;
    return `${panel.rating}${delta} · ${panel.correct}/${panel.problems.length} · ${panel.trick.name}`;
  },

  async render(node) {
    render(node);
  },
};
