// Past math problems, and the way to submit one you did late.
//
// A modal rather than a panel, for the reason the theorem archive is one: the
// columns are for things you glance at daily, and a reference surface consulted
// when you want to look something up would hold that space permanently. No chip
// either — the chip row is today's checklist, and rereading old problems is not
// a task you complete.
//
// Unlike the theorem archive this cannot render from the boot payload. Sixty
// statements, answers and hint lists is a lot of bytes to put on every page
// load for a modal most days never open, so it fetches when opened.
//
// The answer is shown outright on a day that is closed, and hidden behind a
// reveal on a day still open to late submission. That asymmetry is the whole
// integrity story: the archive exists to be read, but a self-reported "solved"
// means nothing if the answer was on screen when you clicked it.

import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { rerender } from "../panel.js";
import { put } from "../store.js";
import { openModal, typeset } from "../ui.js";

let cache = null;

export function openMathArchive() {
  openModal("math archive", async (body) => {
    mount(body, el("div.faint", { text: "loading…" }));
    try {
      cache = await api.get("/api/math/archive", { date: api.today() });
    } catch (error) {
      mount(body, el("div.err", { text: error.message }));
      return;
    }
    draw(body);
  });
}

const STATUS = {
  solved: { label: "solved", cls: "ok" },
  failed: { label: "couldn't solve", cls: "bad" },
  lapsed: { label: "unmarked", cls: "faint" },
  open: { label: "open", cls: "faint" },
};

function draw(body) {
  const { entries, late_window_days } = cache;

  if (!entries.length) {
    mount(body, el("div.faint", { text: "no problems yet — the archive fills as you go" }));
    return;
  }

  const pending = entries.filter((e) => e.late_eligible);

  mount(body,
    el("div.row.spread", null,
      el("span.faint", { text: `${entries.length} problems` }),
      pending.length
        ? el("span.gold", {
            text: `${pending.length} still submittable`,
            title: `a lapse can be submitted for ${late_window_days} days`,
          })
        : null,
    ),
    pending.length
      ? el("div.faint.small", {
          text: `An unmarked day can be submitted for ${late_window_days} days.`
            + " A late result replaces the zero it was auto-scored, and is worth"
            + " what it would have been worth on the day minus two hints.",
        })
      : null,
    el("div.archive", null, ...entries.map((entry) => archiveEntry(body, entry))),
  );
}

/** `+12` / `-8` — what the day did to the pooled rating. */
function deltaTag(delta) {
  if (delta == null || delta === 0) return null;
  return el(`span.${delta > 0 ? "delta-up" : "delta-down"}`, {
    text: ` ${delta > 0 ? "+" : ""}${delta}`,
  });
}

function archiveEntry(body, entry) {
  const status = STATUS[entry.status] ?? { label: entry.status, cls: "faint" };
  const statement = el("div.statement", { text: entry.problem.statement });
  const slot = el("div");

  const node = el("details.archive-item", null,
    el("summary", null,
      el("span", { text: entry.date }),
      el("span.faint", { text: ` · ${entry.problem.source} · tier ${entry.problem.tier}` }),
      " ",
      el(`span.${status.cls}`, { text: status.label }),
      deltaTag(entry.delta),
      entry.late_eligible ? el("span.tag", { text: "submittable" }) : null,
    ),
    statement,
    el("div.faint.small", null,
      entry.problem.area ? `${entry.problem.area} · ` : null,
      `${entry.hints_revealed}/3 hints`,
      entry.score != null ? ` · scored ${entry.score}` : null,
    ),
    slot,
  );

  mount(slot, entry.late_eligible ? lateBox(body, entry) : answerBlock(entry));

  // Typeset the one you opened, not all sixty. KaTeX over every statement on
  // mount is seconds of blocked main thread for content mostly scrolled off.
  node.addEventListener("toggle", () => typeset(node), { once: true });
  return node;
}

/** `answer: 42 · full solution ↗` — the archive's whole reason to exist. */
function answerBlock(entry) {
  const problem = entry.problem;
  return el("div", null,
    el("div.answer", null,
      `answer: ${problem.answer} `,
      problem.verified_url ? " · " : null,
      problem.verified_url ? link(problem.verified_url, "full solution ↗") : null,
    ),
    problem.hints.length
      ? el("div.hints", null, ...problem.hints.map((h) => el("div.dim", { text: `▸ ${h}` })))
      : null,
  );
}

/**
 * The submit-late box: report first, reveal second.
 *
 * The buttons come before the answer and the answer starts hidden, so the
 * order of operations is the honest one. Revealing does not disable the
 * buttons — you are allowed to change your mind about what you deserve, and a
 * lock here would only teach you to decide before looking, which is the thing
 * it was trying to enforce anyway.
 */
function lateBox(body, entry) {
  const revealed = entry.hints_revealed;
  const message = el("div.faint.small", {
    text: `submitting solved scores ${entry.late_score_if_solved}`
      + (revealed ? ` (${revealed} hint${revealed === 1 ? "" : "s"} already taken)` : ""),
  });
  const answer = el("div");

  const submit = async (outcome, buttons) => {
    buttons.forEach((b) => (b.disabled = true));
    message.textContent = "submitting…";
    try {
      const result = await api.post("/api/math/late", { target: entry.date, outcome });
      // `put` refreshes the store and, through the subscription, the elo
      // chart. The math panel is *not* on that list — it updates its own
      // header in place when you finish today's problem, precisely so the
      // statement stays on screen — so a late submission has to ask for it.
      // Without this the rating behind the modal stays at yesterday's figure
      // until something else redraws.
      put("math", result.panel);
      rerender("math");
      cache = result.archive;
      draw(body);
    } catch (error) {
      buttons.forEach((b) => (b.disabled = false));
      message.textContent = `failed: ${error.message}`;
    }
  };

  const solved = el("button.ok", { text: "I solved it" });
  const failed = el("button.bad", { text: "couldn't solve" });
  const reveal = el("button", { text: "reveal answer" });
  const buttons = [solved, failed];

  solved.addEventListener("click", () => submit("solved", buttons));
  failed.addEventListener("click", () => submit("failed", buttons));
  reveal.addEventListener("click", () => {
    reveal.disabled = true;
    mount(answer, answerBlock(entry));
    typeset(answer);
  });

  return el("div", null,
    message,
    el("div.row", null, solved, failed, reveal),
    answer,
  );
}
