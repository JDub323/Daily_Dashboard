import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { put, store } from "../store.js";
import { typeset } from "../ui.js";

/** The elo header both hero panels share. */
export function summaryRow(summary) {
  const { rating, k_next, measured_count, success_rate } = summary;
  const detail = [`elo · K=${k_next}`];
  if (success_rate !== null) detail.push(`${success_rate}% last ${Math.min(measured_count, 10)}`);
  return el("div.row", null,
    el("span.rating", { text: rating }),
    el("span.faint", { text: detail.join(" · ") }),
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
  return el("div.answer", null,
    `answer: ${problem.answer} `,
    ...extra,
    problem.verified_url ? " · " : null,
    problem.verified_url ? link(problem.verified_url, "full solution ↗") : null,
  );
}

async function generate(node, panel) {
  const status = el("span.dim", { text: "starting…" });
  mount(node, el("div.row", null, el("span.spinner"), status));
  await api.stream("/api/math/new", {}, (message) => {
    status.textContent = message;
  });
  // The stream returns the problem, but the panel wants the whole slice —
  // hints_visible, status and the refreshed summary all move together.
  put("math", await api.get("/api/state", { date: api.today() }).then((s) => s.math));
  await panel.render(node);
}

export default {
  id: "math",
  title: "∑ math problem of the day",
  col: "center",
  hero: true,

  async render(node) {
    const math = store.state.math;
    const head = summaryRow(math.summary);
    const areas = areaRow(math);

    if (!math.problem) {
      mount(node, head, areas,
        el("button.primary", {
          text: "get today's problem",
          onclick: (e) => {
            e.target.disabled = true;
            generate(node, this).catch((error) => {
              mount(node, head,
                el("div.err", { text: error.message }),
                el("div.row", null, el("button", { text: "retry", onclick: () => this.render(node) })),
              );
            });
          },
        }),
        el("div.faint", { text: `topic: ${store.config.topic} · verified against AoPS via web search` }),
      );
      return;
    }

    const problem = math.problem;

    if (math.status !== "open") {
      const answer = answerLine(problem);
      mount(node, head, areas,
        el("div.ok", null, `done for today ✓ (${problem.source}) `, lastDelta(math.summary)),
        answer,
      );
      typeset(answer);
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

    const finish = async (outcome, buttons) => {
      buttons.forEach((b) => (b.disabled = true));
      const panel = put("math", await api.post("/api/math/finish", { outcome }));
      // The statement stays on screen — you want the answer next to the
      // problem you just worked on — so the header is swapped in place rather
      // than re-rendering the panel into its "done for today" form.
      head.replaceWith(summaryRow(panel.summary));
      // The area that just took the result moved too, and it is the number
      // that decides tomorrow's difficulty — so it has to be visibly current,
      // not correct only after a reload.
      areas?.replaceWith(areaRow(panel));
      mount(answerSlot,
        answerLine(panel.problem, lastDelta(panel.summary),
          el("span.faint", { text: ` → ${panel.summary.rating}` })),
      );
      typeset(answerSlot);
    };

    const hintButton = el("button", { text: `hint ${math.hints_revealed}/3` });
    // Free, and deliberately not a fourth hint. The score is self-reported
    // either way, and the answer was already in the panel payload — declining
    // to render it was never a control, only an inconvenience. What this buys
    // is checking your work *before* committing to "solved", which the old
    // order made impossible: you had to declare, then find out.
    const revealAnswer = el("button", { text: "reveal answer" });
    const solved = el("button.ok", { text: "solved" });
    const failed = el("button.bad", { text: "couldn't solve" });
    const buttons = [hintButton, revealAnswer, solved, failed];

    revealAnswer.addEventListener("click", () => {
      revealAnswer.disabled = true;
      mount(answerSlot, answerLine(problem));
      typeset(answerSlot);
    });

    hintButton.addEventListener("click", async () => {
      if (math.hints_revealed >= 3) return;
      put("math", await api.post("/api/math/hint"));
      await this.render(node);
    });
    solved.addEventListener("click", () => finish("solved", buttons));
    failed.addEventListener("click", () => finish("failed", buttons));

    mount(node, head, areas,
      el("div.faint", null,
        `${problem.source} · tier ${problem.tier} (${problem.anchor})`,
        problem.verified_url ? " · " : null,
        problem.verified_url ? link(problem.verified_url, "verified ✓") : null,
      ),
      statement,
      revealed,
      el("div.row", null, hintButton, revealAnswer, solved, failed),
      answerSlot,
    );
    typeset(node);
  },
};
