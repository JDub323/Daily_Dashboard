// The morning check-in: what you weigh, and how you feel about it.
//
// Thirty seconds, before anything else, in this order — weight first because
// it is the one reading that is ruined by breakfast, coffee or a shower.
//
// The five questions are the Hooper index plus motivation. They are five and
// not fifteen on purpose: the failure mode of a self-report questionnaire is
// not that it is too short, it is that it gets long enough to be answered
// carelessly, and a carelessly-answered scale is worse than no scale because
// it still produces a number. Five taps is a thing you will still be doing in
// February.
//
// Every item runs 1 = worst, 5 = best, including the three the literature
// publishes the other way round. See the schema-17 comment for why the
// divergence is deliberate; the labels below carry the direction so nobody has
// to remember it.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";

const ITEMS = [
  ["sleep_quality", "sleep quality", "slept badly", "slept well"],
  ["fatigue", "fatigue", "wiped out", "fresh"],
  ["soreness", "soreness", "wrecked", "loose"],
  ["stress", "stress", "frazzled", "calm"],
  ["motivation", "motivation", "dreading it", "keen"],
];

/** Today's weight, or null. The series is oldest-first, so the last point is
 *  today's only if its date says so. */
export function weightToday(state) {
  const series = state.health.series.find((s) => s.metric === "weight");
  const last = series?.points?.[series.points.length - 1];
  return last && last[0] === state.date ? last[1] : null;
}

export default {
  id: "checkin",
  title: "morning check-in",
  tab: "health",
  slot: "grid",
  span: 3,

  async render(node) {
    // The draft is local until all five are answered, because the row is
    // all-or-nothing in the database. A half-filled questionnaire is not a
    // state worth persisting: `score` would be a sum over three items and
    // every comparison against it would be wrong.
    const answers = { ...(store.state.health.readiness_today ?? {}) };
    const message = el("span.faint");

    const weight = el("input", {
      type: "number",
      step: "0.1",
      inputmode: "decimal",
      placeholder: "morning weight",
      value: weightToday(store.state) ?? "",
      onchange: async (e) => {
        const value = Number.parseFloat(e.target.value);
        if (Number.isNaN(value) || value <= 0) return;
        // No re-render here: `put` notifies, and this panel watches the slice
        // (see WATCHES in main.js) along with the two others that read it.
        put("health", await api.post("/api/health", { metric: "weight", value }));
      },
    });

    const save = async () => {
      if (ITEMS.some(([key]) => !answers[key])) {
        message.textContent = `${ITEMS.filter(([k]) => answers[k]).length}/5`;
        return;
      }
      put("health", await api.post("/api/readiness", answers));
    };

    const scale = ([key, label, low, high]) => {
      // The highlight is moved here rather than left to the next render. Until
      // the fifth answer lands there IS no next render — `save` refuses to
      // post a partial row — so a tap on the first four would light nothing at
      // all, and a scale that does not respond to being tapped reads as
      // broken. Same reason the session panel does it this way.
      const buttons = el("div.scale", null,
        ...[1, 2, 3, 4, 5].map((n) =>
          el("button.tiny", {
            text: String(n),
            class: answers[key] === n ? "on" : "",
            "aria-pressed": answers[key] === n,
            onclick: () => {
              answers[key] = n;
              for (const b of buttons.children) {
                const picked = b.textContent === String(n);
                b.classList.toggle("on", picked);
                b.setAttribute("aria-pressed", String(picked));
              }
              save();
            },
          }),
        ),
      );
      return el("div.scale-row", null,
        el("div.row.spread", null,
          el("span", { text: label }),
          el("span.faint", { text: `1 ${low} · 5 ${high}` }),
        ),
        buttons,
      );
    };

    const done = store.state.health.readiness_today;
    mount(node,
      el("div.row", null, weight, el("span.faint", { text: "lb" })),
      el("div.divider"),
      ...ITEMS.map(scale),
      el("div.row", null,
        done
          ? el("span.dim", { text: `${done.score}/25 ✓` })
          : el("span.faint", { text: "answer all five" }),
        message,
      ),
    );
  },
};
