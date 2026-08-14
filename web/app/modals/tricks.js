// Every trick you have been taught, with the set that taught it.
//
// A modal rather than a panel, on `modals/theorems.js`'s reasoning: this is a
// reference activity you do occasionally, and a panel is space spent every day.
//
// Unlike the theorem archive it has to fetch. The deck there ships whole in the
// boot payload because a card is two short strings; a set is nine problems with
// their prompts, answers and worked solutions, and sixty of those on every page
// load would be most of the payload for a surface nobody has opened.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { focusPanel } from "../panel.js";
import { closeModal, openModal, typeset } from "../ui.js";

/** Cached for the session: past sets never change, so a second open is free. */
let archive = null;

/** Dropped when a set is submitted, so today's row is not stale. */
export function invalidateTrickArchive() {
  archive = null;
}

export function openTrickArchive() {
  openModal("trick archive", async (body) => {
    if (archive) {
      draw(body);
      return;
    }
    mount(body, el("div.row", null, el("span.spinner"), el("span.dim", { text: "loading…" })));
    try {
      // Dated: a challenge is answerable on the day it is set, so its answer is
      // withheld on today's row and shown on every other — and only the browser
      // knows which row is today's.
      archive = await api.get("/api/trick/archive", { date: api.today() });
      draw(body);
    } catch (error) {
      mount(body, el("div.err", { text: error.message }));
    }
  });
}

function backToToday() {
  closeModal();
  focusPanel("tricks");
}

function draw(body) {
  if (!archive.entries.length) {
    mount(body,
      el("div.faint", { text: "no tricks yet" }),
      el("div.row.gap", null, el("button", { text: "← today", onclick: backToToday })),
    );
    return;
  }

  const scored = archive.entries.filter((e) => e.correct !== null);
  const total = scored.reduce((sum, e) => sum + e.correct, 0);
  const asked = scored.reduce((sum, e) => sum + e.problems.length, 0);

  mount(body,
    el("div.row.spread", null,
      el("span.faint", {
        text: `${archive.entries.length} trick${archive.entries.length === 1 ? "" : "s"}`
          + (asked ? ` · ${total}/${asked} problems right` : ""),
      }),
      el("button", { text: "← today", onclick: backToToday }),
    ),
    el("div.archive", null, ...archive.entries.map(entry)),
  );
}

/** One past day: the trick in the summary, the whole set behind a disclosure. */
function entry(item) {
  const trick = item.trick;
  const statement = el("div.statement", { text: trick.statement });
  const score =
    item.correct === null
      ? "unfinished"
      : `${item.correct}/${item.problems.length}`;
  const delta = item.delta == null ? "" : ` · ${item.delta >= 0 ? "+" : ""}${item.delta}`;

  const body = el("div", null,
    statement,
    trick.idea ? el("div.dim.small", { text: trick.idea }) : null,
    ...item.problems.map((problem, i) => row(problem, i + 1)),
    item.challenge ? row(item.challenge, "★") : null,
    el("div.faint.small", { text: `${trick.topic} · ${trick.kind}` }),
  );

  const node = el("details.archive-item", null,
    el("summary", null,
      el("span", { text: trick.name }),
      el("span.faint", { text: ` · ${item.date} · ${score}${delta}` }),
    ),
    body,
  );

  // Typeset the one you opened, not all sixty. KaTeX over five hundred prompts
  // on mount is seconds of blocked main thread for content still scrolled off
  // screen — the theorem archive's rule, and it matters more here, since a set
  // is nine formulas rather than one.
  node.addEventListener("toggle", () => typeset(body), { once: true });
  return node;
}

function row(problem, label) {
  const ok = problem.correct;
  return el("div.trick-row", null,
    el("div.row", null,
      el(`span.${ok === null ? "faint" : ok ? "ok" : "bad"}`, {
        text: ok === null ? "·" : ok ? "✓" : "✗",
      }),
      el("span.faint.spell-n", { text: String(label) }),
      el("span.tag", { text: problem.band }),
      problem.callback_of ? el("span.faint.small", { text: `↩ ${problem.callback_of}` }) : null,
    ),
    el("div.trick-prompt.dim", { text: problem.prompt }),
    el("div.small", null,
      el("span.faint", { text: "answer: " }),
      el("span", { text: problem.answer }),
    ),
    problem.solution ? el("div.dim.small.trick-solution", { text: problem.solution }) : null,
  );
}
