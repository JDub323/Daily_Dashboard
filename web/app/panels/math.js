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

function topicLinks() {
  const info = store.topic;
  if (!info) return el("span.faint", { text: "topic links loading…" });
  return el("span", null,
    link(info.textbook.url, info.textbook.title),
    " · theorems: ",
    ...info.theorems.flatMap((t, i) => (i ? [", ", link(t.url, t.title)] : [link(t.url, t.title)])),
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

    if (!math.problem) {
      mount(node, head,
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
      const answer = el("div.answer", { text: `answer: ${problem.answer}` });
      mount(node, head,
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
      mount(answerSlot,
        el("div.answer", null, `answer: ${panel.problem.answer} `, lastDelta(panel.summary),
          el("span.faint", { text: ` → ${panel.summary.rating}` })),
      );
      typeset(answerSlot);
    };

    const hintButton = el("button", { text: `hint ${math.hints_revealed}/3` });
    const solved = el("button.ok", { text: "solved" });
    const failed = el("button.bad", { text: "couldn't solve" });
    const buttons = [hintButton, solved, failed];

    hintButton.addEventListener("click", async () => {
      if (math.hints_revealed >= 3) return;
      put("math", await api.post("/api/math/hint"));
      await this.render(node);
    });
    solved.addEventListener("click", () => finish("solved", buttons));
    failed.addEventListener("click", () => finish("failed", buttons));

    mount(node, head,
      el("div.faint", null,
        `${problem.source} · tier ${problem.tier} (${problem.anchor})`,
        problem.verified_url ? " · " : null,
        problem.verified_url ? link(problem.verified_url, "verified ✓") : null,
      ),
      statement,
      revealed,
      el("div.row", null, hintButton, solved, failed),
      answerSlot,
    );
    typeset(node);
  },
};
