import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { openMathArchive } from "../modals/math.js";
import { put, store } from "../store.js";
import { minutesBox, priceLabel } from "../timebonus.js";
import { typeset } from "../ui.js";

/**
 * The way through to past problems — and the only surface that says a lapsed
 * day can still be submitted, since the archive is where you would go to do it.
 */
function archiveLink() {
  return el("div.row.gap", null,
    el("button", { text: "past problems →", onclick: openMathArchive }),
  );
}

/**
 * The elo header both hero panels share.
 *
 * The rating is the number each of these tabs exists to move, so it is drawn at
 * the size that says so. It used to render at 20px — four pixels off the app
 * title and six off body text, which made the one figure worth looking at from
 * the doorway indistinguishable from the sentence next to it.
 */
export function summaryRow(summary) {
  const { rating, k_next, measured_count, success_rate } = summary;
  const detail = [`elo · K=${k_next}`];
  if (success_rate !== null) detail.push(`${success_rate}% last ${Math.min(measured_count, 10)}`);
  return el("div.figure-row", null,
    el("span.figure", { text: rating }),
    el("span.figure-label", { text: detail.join(" · ") }),
  );
}

/** The delta from the most recent event on a track, for the "done" line. */
export function lastDelta(summary) {
  const last = summary.history[summary.history.length - 1];
  if (!last || last.delta == null) return null;
  const sign = last.delta >= 0 ? "+" : "";
  return el(`span.${last.delta >= 0 ? "delta-up" : "delta-down"}`, { text: `${sign}${last.delta}` });
}

/**
 * The per-area breakdown, strongest first.
 *
 * The headline above stays the pooled rating. These are what difficulty is
 * actually pitched against: the week's area drives the tier the generator is
 * asked for, so a 1450 at geometry gets geometry problems aimed at 1450 even
 * while the headline reads 1700.
 *
 * An area with no results yet shows the pooled rating standing in, dimmed —
 * it is a placeholder, not something earned, and rendering it identically to
 * a real one would be the cold-start problem wearing a disguise.
 */
function areaRow(math) {
  if (!math.areas.length) return null;
  return el("div.areas", null,
    ...math.areas.map((a) =>
      el(`span.area${a.area === math.target_area ? ".area-live" : ""}`, {
        title: a.events
          ? `${a.area}: ${a.events} result${a.events === 1 ? "" : "s"}`
          : `${a.area}: no results yet — showing your overall rating`,
      },
        el("span.faint", { text: `${a.area} ` }),
        el(`span${a.events ? "" : ".faint"}`, { text: a.rating }),
      ),
    ),
  );
}

function topicLinks() {
  const info = store.topic;
  if (!info) return el("span.faint", { text: "topic links loading…" });
  return el("span", null,
    link(info.textbook.url, info.textbook.title),
    " · theorems: ",
    ...info.theorems.flatMap((t, i) => (i ? [", ", link(t.url, t.title)] : [link(t.url, t.title)])),
  );
}

/**
 * `answer: 42 · full solution ↗` — the one place the answer line is built.
 *
 * The link is `verified_url`, the page the generator checked the problem
 * against before serving it. It was already on screen as a bare "verified ✓"
 * in the header, where it reads as a provenance claim rather than as what it
 * also is: an AoPS page with worked solutions on it. Nothing new is fetched.
 */
function answerLine(problem, ...extra) {
  // A proof has no bare answer, so `answer` holds the book's own solution
  // sketch instead — the same column meaning the same thing, "what you were
  // supposed to produce", which for a proof is an argument.
  const label = problem.kind === "proof" ? "the book's solution: " : "answer: ";
  return el("div.answer", null,
    `${label}${problem.answer} `,
    ...extra,
    problem.verified_url ? " · " : null,
    problem.verified_url ? link(problem.verified_url, "full solution ↗") : null,
  );
}

/** Is today one of the four days whose problem comes out of the book? */
function isProof(math) {
  return math.mode === "proof" || math.mode === "proof-long" || math.mode === "proof-showcase";
}

/**
 * The rating the day is actually pitched at, and can actually move.
 *
 * Proof results rate into the `proofs` bucket and leave the pooled rating
 * alone, so drawing the pooled number as the headline on a Thursday would put
 * the one figure the day cannot change at the top of the panel.
 */
function heroRow(math) {
  if (!isProof(math) || !math.proof) return summaryRow(math.summary);
  const detail = math.proof.events
    ? `proofs elo · ${math.proof.events} result${math.proof.events === 1 ? "" : "s"}`
    : "proofs elo · no results yet";
  return el("div.figure-row", null,
    el("span.figure", { text: math.proof.rating }),
    el("span.figure-label", { text: detail }),
    el("span.faint.small", { text: ` · ${math.summary.rating} overall` }),
  );
}

/**
 * Where in the book it is, and the checkpoints a finished proof has to hit.
 *
 * The rubric is on screen *before* the buttons rather than behind them,
 * because it is what makes "partial" a claim about the mathematics instead of
 * a feeling. It comes out of the book's own printed solution, so ticking it is
 * reading your write-up against the author rather than against your memory of
 * what you meant to prove.
 */
function rubricBox(problem, boxes) {
  if (!problem.rubric.length) return null;
  return el("div.rubric", null,
    el("div.faint.small", { text: "a complete proof shows:" }),
    ...problem.rubric.map((line, i) => {
      const box = el("input", { type: "checkbox" });
      boxes.push(box);
      return el("label.rubric-line", null, box, el("span", { text: ` ${line}` }));
    }),
  );
}

/**
 * Paste a problem you found, on the two days that allow one.
 *
 * The tier is not asked for. A box that let you type "putnam-late" would be
 * asking for the one number in the panel nobody can check — so the statement
 * goes to a cheap level check instead, which is the only paid call the math
 * problem makes and only ever fires because you pressed this.
 *
 * Collapsed behind a button because most Saturdays there is nothing to paste,
 * and an open textarea is a panel asking a question it usually should not.
 */
function ownBox(node) {
  const open = el("button", { text: "bring your own problem" });
  const slot = el("div");
  open.addEventListener("click", () => {
    const statement = el("textarea.recall-box", {
      rows: 4,
      placeholder: "the problem, stated in full — LaTeX between $…$ renders",
    });
    const source = el("input", {
      type: "text",
      placeholder: "what to call it — \"the numbered cylinder / Fourier\"",
    });
    const go = el("button.primary", { text: "check the level and start" });
    const err = el("div");
    open.disabled = true;
    go.addEventListener("click", async () => {
      go.disabled = true;
      mount(err, el("div.row", null, el("span.spinner"), el("span.dim", { text: "rating it…" })));
      try {
        put("math", await api.post("/api/math/own", {
          statement: statement.value,
          source: source.value,
        }));
        await this.render(node);
      } catch (error) {
        go.disabled = false;
        mount(err, el("div.err", { text: error.message }));
      }
    });
    mount(slot, statement, el("div.row.gap", null, source, go), err);
  });
  return el("div", null, el("div.row.gap", null, open), slot);
}

/**
 * Which day of the two you are on, and what that costs.
 *
 * Written out because the barrier is the feature and an invisible rule is a
 * rule you argue with. Day one says there are no hints *and* what solving it
 * today is worth, so the choice between pressing on and carrying is made
 * knowing both halves.
 */
function dayBanner(math) {
  if (math.day === 1) {
    return el("div.faint.small", {
      text: "day 1 of 2 — no hints today. Solve it now and it is worth several times "
        + "an ordinary result, and tomorrow is off.",
    });
  }
  return el("div.faint.small", {
    text: "day 2 of 2 — the nudge is available, and the second hint gives the argument away.",
  });
}

/**
 * Sunday: read, and work many easy problems.
 *
 * Three segments, one or two expected. Nothing here is rated and nothing here
 * lapses — the day is ticked by doing any of it — so the controls are a tick
 * and a skip, and the skip asks why in one word. That word is the only thing
 * that stops next month's plan opening with the introduction you already knew.
 */
function renderReading(node, math, head) {
  const plan = math.plan;
  if (!plan) {
    // The same gesture as drawing a problem, and the same endpoint: the plan
    // is sitting in the overnight directory and this is what imports it. The
    // error case — no file at all — comes back from the server saying which
    // command to run, so there is nothing for the panel to guess at.
    mount(node, head,
      el("button.primary", {
        text: "get today's reading",
        onclick: (e) => {
          e.target.disabled = true;
          draw(node, this).catch((error) => {
            mount(node, head,
              el("div.err", { text: error.message }),
              el("div.row", null,
                el("button", { text: "retry", onclick: () => this.render(node) })),
            );
          });
        },
      }),
      el("div.faint", { text: `topic: ${store.config.topic}` }),
      archiveLink(),
    );
    return;
  }

  const mark = async (index, done, reason) => {
    put("math", await api.post("/api/math/segment", { index, done, reason: reason || "" }));
    await this.render(node);
  };

  const segments = plan.segments.map((segment, i) => {
    const done = plan.done.includes(i);
    const skipped = plan.skipped.find((s) => s.index === i);
    const why = el("input.skip-why", { type: "text", placeholder: "knew it / no time" });
    return el(`div.segment${done ? ".segment-done" : ""}`, null,
      el("div.row.gap", null,
        el("span.segment-title", { text: `${segment.minutes}m · ${segment.title}` }),
        done ? el("span.ok", { text: "done ✓" }) : null,
        skipped ? el("span.faint", { text: `skipped — ${skipped.reason || "no reason given"}` }) : null,
      ),
      el("div.small", { text: segment.reading }),
      segment.problems.length
        ? el("div.faint.small", { text: `problems: ${segment.problems.join(", ")}` })
        : null,
      segment.why ? el("div.dim.small", { text: segment.why }) : null,
      done
        ? null
        : el("div.row.gap", null,
            el("button.ok", { text: "did it", onclick: () => mark(i, true) }),
            why,
            el("button", { text: "skip", onclick: () => mark(i, false, why.value) }),
          ),
    );
  });

  const body = el("div", null,
    el("div.faint", null, `${plan.topic} · ${plan.book}`),
    el("div.faint.small", {
      text: plan.started
        ? "the day is ticked. Do a second segment if the first went quickly."
        : "one or two of these, not all three. Take the ones that are new.",
    }),
    ...segments,
  );
  mount(node, head, body, archiveLink());
  typeset(body);
}

/**
 * Claim today's problem.
 *
 * This used to read an ndjson stream and narrate a multi-minute generation
 * — "verifying against AoPS… (search 3)" — because that is what was happening
 * behind it, at $0.15–0.80 a morning. There is no generation any more: the
 * problem comes from the overnight batch or the curated pool, and both are a
 * file read. So it is one POST that returns the panel.
 *
 * Still a button rather than a boot gather, and not for cost reasons. Drawing
 * opens an attempt, and an unmarked attempt lapses at 0.0 — pressing this is
 * the act that puts the day on the record.
 */
async function draw(node, panel) {
  mount(node, el("div.row", null, el("span.spinner"), el("span.dim", { text: "fetching…" })));
  put("math", await api.post("/api/math/new"));
  await panel.render(node);
}

export default {
  id: "math",
  title: "∑ math problem of the day",
  tab: "math",
  slot: "hero",
  hero: true,
  collapsible: true,

  /**
   * The one line the panel keeps once the day's problem is done.
   *
   * Rating, what happened, and what it cost or earned — enough to not need to
   * expand it, which is the whole test of a summary.
   */
  summary() {
    const math = store.state.math;
    const rating = isProof(math) && math.proof ? math.proof.rating : math.summary.rating;

    if (math.mode === "reading") {
      if (!math.plan) return "reading day — no plan";
      const total = math.plan.segments.length;
      return `reading · ${math.plan.done.length}/${total} segment${total === 1 ? "" : "s"}`;
    }
    if (math.free_day) return `${rating} · day off`;
    if (!math.problem) return `${rating}`;
    // Carried is not a result, so there is no delta to report and the line
    // says where the problem is rather than what it scored.
    if (math.status === "carried") return `${rating} · carried to tomorrow`;
    if (math.status === "open") {
      return math.day === 2 ? `${rating} · day 2` : `${rating}`;
    }
    const last = math.summary.history[math.summary.history.length - 1];
    const delta = last?.delta == null ? "" : ` ${last.delta >= 0 ? "+" : ""}${last.delta}`;
    const verb = math.status === "solved" ? "solved" : math.status;
    const time = math.minutes ? ` · ${math.minutes}m` : "";
    return `${rating}${delta} · ${verb}${time}`;
  },

  async render(node) {
    const math = store.state.math;
    const head = heroRow(math);
    const areas = areaRow(math);

    if (math.mode === "reading") return renderReading.call(this, node, math, head);

    if (!math.problem && math.free_day) {
      // Thursday was solved on the day, so Friday is off. Saying what earned
      // it matters more than the offer: the day is the reward, and a blank
      // panel would read as the app having forgotten.
      mount(node, head, areas,
        el("div.ok", { text: "day off — you solved Thursday's problem on the day ✓" }),
        el("div.faint.small", {
          text: "nothing is drawn and nothing lapses. Work a problem of your own if you found one.",
        }),
        ownBox.call(this, node),
        archiveLink(),
      );
      return;
    }

    if (!math.problem) {
      mount(node, head, areas,
        el("button.primary", {
          text: "get today's problem",
          onclick: (e) => {
            e.target.disabled = true;
            draw(node, this).catch((error) => {
              mount(node, head,
                el("div.err", { text: error.message }),
                el("div.row", null, el("button", { text: "retry", onclick: () => this.render(node) })),
              );
            });
          },
        }),
        el("div.faint", { text: `topic: ${store.config.topic}` }),
        // The showcase day is the one that says "or one you found yourself".
        math.mode === "proof-showcase" ? ownBox.call(this, node) : null,
        archiveLink(),
      );
      return;
    }

    const problem = math.problem;

    if (math.status === "carried") {
      // Not done, and deliberately not drawn as done: nothing was rated, the
      // problem is still yours, and the statement stays on screen because you
      // are meant to keep thinking about it.
      mount(node, head, areas,
        el("div.faint", { text: `${problem.source} · carried to tomorrow` }),
        el("div.statement", { text: problem.statement }),
        el("div.faint.small", {
          text: "day 1 done. Tomorrow the first hint unlocks; the second gives the argument away.",
        }),
        archiveLink(),
      );
      typeset(node);
      return;
    }

    if (math.status !== "open") {
      // Null for a problem you brought yourself: there is no reference
      // solution, so the archive line would be a label with nothing after it.
      const answer = problem.answer ? answerLine(problem) : null;
      mount(node, head, areas,
        el("div.ok", null, `done for today ✓ (${problem.source}) `, lastDelta(math.summary)),
        math.minutes
          ? el("div.faint.small", { text: `${math.minutes} min · ${priceLabel(math.minutes, math.par_minutes)}` })
          : null,
        answer,
        archiveLink(),
      );
      if (answer) typeset(answer);
      return;
    }

    // The topic/textbook links are free. They used to occupy hints[0], so
    // opening reference material cost the same elo as being told the answer.
    const revealed = el("div.hints", null,
      el("div.dim", null, "▸ ", topicLinks()),
      ...math.hints_visible.map((h) => el("div.dim", { text: `▸ ${h}` })),
    );
    const statement = el("div.statement", { text: problem.statement });
    const answerSlot = el("div");
    // No timer on a proof day. `par_minutes` is null there, and a reference
    // time for a problem designed to be sat with would price hurrying on the
    // one day of the week that exists for not hurrying.
    const timer = math.par_minutes ? minutesBox(math.par_minutes) : null;
    const rubricBoxes = [];

    const finish = async (outcome, buttons) => {
      buttons.forEach((b) => (b.disabled = true));
      // Sent on a failure too, and dropped server-side. The client does not
      // get to decide which outcomes time applies to — that rule lives in one
      // place, and it is not this one.
      const panel = put("math", await api.post("/api/math/finish", {
        outcome,
        minutes: timer?.value() ?? null,
        rubric_hit: rubricBoxes.map((b) => b.checked),
      }));
      // The statement stays on screen — you want the answer next to the
      // problem you just worked on — so the header is swapped in place rather
      // than re-rendering the panel into its "done for today" form.
      head.replaceWith(heroRow(panel));
      // The area that just took the result moved too, and it is the number
      // that decides tomorrow's difficulty — so it has to be visibly current,
      // not correct only after a reload.
      areas?.replaceWith(areaRow(panel));
      mount(answerSlot,
        answerLine(panel.problem, lastDelta(panel.summary),
          el("span.faint", { text: ` → ${panel.summary.rating}` })),
      );
      typeset(answerSlot);
      // The box has done its job and the number is now on the record, so it
      // stops being editable — re-typing it would suggest a resubmit, and the
      // attempt is closed.
      timer?.node?.replaceWith(
        panel.minutes
          ? el("div.faint.small", { text: `${panel.minutes} min · ${priceLabel(panel.minutes, panel.par_minutes)}` })
          : el("span"),
      );
    };

    const proof = isProof(math);
    const twoDay = math.mode === "proof-long";

    // `hint_budget` is what *today* allows, not what the problem carries: zero
    // on day one of the two-day problem, whose two hints exist and are not
    // reachable until tomorrow. The button is absent rather than disabled —
    // a greyed-out control invites a click and implies the hint is one press
    // away, which is exactly the wrong thing to say about a day-long barrier.
    const hintButton = math.hint_budget
      ? el("button", { text: `hint ${math.hints_revealed}/${math.hint_budget}` })
      : null;
    // Free, and deliberately not another hint. The score is self-reported
    // either way, and the answer was already in the panel payload — declining
    // to render it was never a control, only an inconvenience. What this buys
    // is checking your work *before* committing, which the old order made
    // impossible: you had to declare, then find out.
    // Absent when there is no reference solution at all — a problem you
    // brought yourself has none, and a button that reveals an empty line is
    // worse than no button.
    const revealAnswer = problem.answer
      ? el("button", { text: proof ? "reveal the book's solution" : "reveal answer" })
      : null;
    const solved = el("button.ok", { text: proof ? "proved it" : "solved" });
    const partial = proof ? el("button", { text: "partial — had the idea" }) : null;
    // Day one ends by carrying, not by failing, so it has no "couldn't do it".
    // Not having solved it yet is the *expected* state of a problem you have
    // another day for, and the button would be a trap: it closes the attempt
    // at zero and throws away tomorrow, when carrying costs nothing and
    // failing tomorrow costs exactly the same.
    const carry = twoDay && math.day === 1
      ? el("button.primary", { text: "keep going tomorrow" })
      : null;
    const failed = carry
      ? null
      : el("button.bad", { text: proof ? "couldn't do it" : "couldn't solve" });
    const buttons = [hintButton, revealAnswer, solved, partial, failed, carry].filter(Boolean);

    revealAnswer?.addEventListener("click", () => {
      revealAnswer.disabled = true;
      mount(answerSlot, answerLine(problem));
      typeset(answerSlot);
    });

    hintButton?.addEventListener("click", async () => {
      if (math.hints_revealed >= math.hint_budget) return;
      put("math", await api.post("/api/math/hint"));
      await this.render(node);
    });
    carry?.addEventListener("click", async () => {
      buttons.forEach((b) => (b.disabled = true));
      put("math", await api.post("/api/math/carry"));
      await this.render(node);
    });
    solved.addEventListener("click", () => finish("solved", buttons));
    partial?.addEventListener("click", () => finish("partial", buttons));
    failed?.addEventListener("click", () => finish("failed", buttons));

    mount(node, head, areas,
      el("div.faint", null,
        `${problem.source} · tier ${problem.tier} (${problem.anchor})`,
        problem.location ? ` · ${problem.location}` : null,
        problem.verified_url ? " · " : null,
        problem.verified_url ? link(problem.verified_url, "verified ✓") : null,
      ),
      twoDay ? dayBanner(math) : null,
      statement,
      rubricBox(problem, rubricBoxes),
      revealed,
      timer?.node,
      el("div.row", null, ...buttons),
      answerSlot,
      archiveLink(),
    );
    typeset(node);
  },
};
