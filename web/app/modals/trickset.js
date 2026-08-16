// The day's trick, as a typeset handout you work through.
//
// This used to be the panel body, and it did not fit: nine problems, each of
// them a displayed formula, plus a statement and eight worked solutions is a
// document, and a 300px card in a grid is not where a document goes. The panel
// keeps the one line that says where you are; everything you actually read and
// type is here, on a full-width sheet with room for the maths to be maths.
//
// Deliberately a modal rather than a tenth tab. The set is worked once a day
// and then read back occasionally, which is exactly the shape an overlay is
// for — and it is reachable from two places (the panel, and the archive's
// "back to today"), which a pane is not.
//
// [Invariant] The answers still are not in the payload until the set is marked.
// Moving the questions to a bigger surface changes where they are rendered and
// nothing about what the browser is trusted with.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { focusPanel } from "../panel.js";
import { put, store } from "../store.js";
import { closeModal, openModal, typeset } from "../ui.js";

/** Matches the server's floor on the challenge box. */
const MIN_ANSWER = 3;

/**
 * What has been typed but not submitted, kept across reopenings.
 *
 * The modal is rebuilt from scratch every time it opens, and closing it by
 * accident — Escape, a click on the backdrop — is easy. Losing eight worked
 * answers to a stray keypress would make the sheet a thing you were afraid of.
 * Keyed by date, so tomorrow's set starts empty.
 */
let draft = { date: null, answers: [], challenge: "" };

function drafted(count) {
  const date = api.today();
  if (draft.date !== date) draft = { date, answers: Array(count).fill(""), challenge: "" };
  while (draft.answers.length < count) draft.answers.push("");
  return draft;
}

export function openTrickSet() {
  openModal("trick of the day", (body) => draw(body));
}

function draw(body) {
  const panel = store.state.tricks;
  if (!panel.trick) {
    mount(body, el("div.faint", { text: "no set drawn today" }));
    return;
  }
  const done = panel.status !== "open";
  mount(body,
    el("div.paper", null,
      masthead(panel),
      statement(panel.trick),
      el("div.paper-rule"),
      done ? marked(panel) : open(body, panel),
      challenge(body, panel),
    ),
  );
}

/** The title block: what the trick is called, and what it is filed under. */
function masthead(panel) {
  return el("div.paper-head", null,
    el("h3.paper-title", { text: panel.trick.name }),
    el("div.paper-by", {
      text: [panel.trick.kind, panel.trick.topic].join(" · "),
    }),
  );
}

/** The trick itself: the rule, then when to reach for it. */
function statement(trick) {
  const node = el("div", null,
    el("div.paper-statement", { text: trick.statement }),
    trick.idea ? el("div.paper-idea", { text: trick.idea }) : null,
  );
  // The whole block: `idea` is prose *about* a formula and routinely carries an
  // inline `$p$`, which typesetting the statement alone leaves on screen as a
  // literal dollar sign.
  typeset(node);
  return node;
}

const BAND_NOTE = {
  easy: "apply it",
  medium: "recognise it first",
  callback: "an older trick",
  challenge: "more than the trick",
};

/** One numbered problem: the question, and whatever goes under it. */
function problemBlock(problem, label, ...under) {
  const node = el("div.paper-problem", null,
    el("div.paper-num", null,
      el("span.paper-n", { text: String(label) }),
      el("span.tag", { text: problem.band }),
      problem.callback_of
        ? el("span.faint.small", { text: `↩ ${problem.callback_of}` })
        : el("span.faint.small", { text: BAND_NOTE[problem.band] ?? "" }),
    ),
    el("div.paper-prompt", { text: problem.prompt }),
    ...under,
  );
  typeset(node);
  return node;
}

/**
 * The open set: every problem with a box under it, and one button.
 *
 * All of them at once rather than one at a time, on the spelling bee's
 * reasoning: the thing being measured is whether the trick is in your hands,
 * not whether you can apply it without going back to fix a sign.
 */
function open(body, panel) {
  const held = drafted(panel.problems.length);
  const inputs = [];

  const blocks = panel.problems.map((problem, i) => {
    const input = el("input.paper-box", {
      value: held.answers[i] ?? "",
      autocomplete: "off",
      spellcheck: false,
      placeholder: "answer",
      oninput: (e) => { held.answers[i] = e.target.value; },
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        // Enter walks down the sheet and submits from the last box — the
        // keyboard path through the whole thing, as the bee's is.
        if (i + 1 < inputs.length) inputs[i + 1].focus();
        else submit.click();
      },
    });
    inputs.push(input);
    return problemBlock(problem, i + 1, input);
  });

  const message = el("span.faint");
  const submit = el("button.primary", {
    text: "check the set",
    onclick: async () => {
      submit.disabled = true;
      message.textContent = "marking…";
      try {
        put("tricks", await api.post("/api/trick/submit", {
          answers: inputs.map((node) => node.value),
        }));
        draft = { date: null, answers: [], challenge: draft.challenge };
        draw(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  queueMicrotask(() => inputs[0]?.focus());

  return el("div", null,
    ...blocks,
    el("div.row.divider", null, submit, message,
      el("span.faint.small", { text: "a blank counts as wrong" })),
  );
}

/** Marked: what you wrote, what it was, and the line that shows the trick. */
function marked(panel) {
  return el("div", null,
    el("div.row.spread.paper-score", null,
      el("span.big.gold", { text: `${panel.correct}/${panel.problems.length}` }),
      deltaTag(panel.delta),
    ),
    ...panel.problems.map((problem, i) =>
      problemBlock(problem, i + 1,
        el("div.paper-verdict", null,
          el(`span.${problem.correct ? "ok" : "bad"}`, { text: problem.correct ? "✓" : "✗" }),
          el("span.small", { text: ` ${problem.answer}` }),
          // Only when it was wrong. Echoing a correct answer back adds nothing,
          // and the sheet is trying to be scannable — the bee's rule.
          problem.correct
            ? null
            : el("span.dim.small", {
                text: problem.typed ? ` · you wrote “${problem.typed}”` : " · left blank",
              }),
        ),
        // Where the trick is actually taught. Shown only on the ones you
        // missed: a wrong answer with no route teaches the answer, not the move.
        problem.correct || !problem.solution
          ? null
          : el("div.paper-solution", { text: problem.solution }),
      ),
    ),
  );
}

function deltaTag(delta) {
  const sign = delta > 0 ? "+" : "";
  return el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
    text: delta == null ? "" : delta === 0 ? "no change" : `${sign}${delta}`,
    title: "trick rating",
  });
}

/**
 * The optional problem, at the foot of the sheet.
 *
 * Its own box and its own button because it is answered at a different time:
 * this is the one you go away and think about, and folding it into the set
 * would mean either submitting the day early or holding the other eight
 * hostage to it.
 */
function challenge(body, panel) {
  const problem = panel.challenge;
  if (!problem) return null;

  if (panel.challenge_ok !== null) {
    return el("div.paper-challenge", null,
      el("div.paper-rule"),
      problemBlock(problem, "★",
        el("div.paper-verdict", null,
          el(`span.${panel.challenge_ok ? "ok" : "bad"}`, { text: panel.challenge_ok ? "✓" : "✗" }),
          el("span.small", { text: ` ${problem.answer}` }),
        ),
        panel.challenge_note ? el("div.dim.small", { text: panel.challenge_note }) : null,
        problem.solution ? el("div.paper-solution", { text: problem.solution }) : null,
      ),
    );
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
    text: "submit the challenge",
    onclick: async () => {
      if (box.value.trim().length < MIN_ANSWER) {
        message.textContent = "write something first — it is optional, not blank";
        return;
      }
      submit.disabled = true;
      message.textContent = "grading…";
      try {
        put("tricks", await api.post("/api/trick/challenge", { answer: box.value.trim() }));
        held.challenge = "";
        draw(body);
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div.paper-challenge", null,
    el("div.paper-rule"),
    problemBlock(problem, "★",
      box,
      el("div.row", null, submit, message,
        // The one paid call in the day, and it says so before you press it.
        el("span.faint.small", { text: "graded by the API · optional" })),
    ),
  );
}

/**
 * Open the sheet from another modal — what the archive's "← today" does.
 *
 * Falls back to the panel on a day with no set, because the alternative is
 * swapping one overlay for another that says "no set drawn today", which reads
 * as the button being broken.
 */
export function backToSet() {
  closeModal();
  if (store.state.tricks.trick) openTrickSet();
  else focusPanel("tricks");
}
