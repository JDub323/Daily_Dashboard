// What the debate rating will not tell you: which of the eight lines you keep
// losing.
//
// This is the panel the whole feature was asked for — "you don't improve what
// you don't measure" — and it is only possible because the ballot is eight
// fixed lines rather than a score out of ten. A rating says you are getting
// better. This says at what, and the ordering is the point: weakest recent
// first, so the top of the list is the thing to work on tonight.
//
// Its own fetch rather than a slice of the boot payload, because the scorecards
// behind it are the largest rows in the schema and nothing else on any tab
// needs them.

import * as api from "../api.js";
import { lineChart } from "../charts.js";
import { el, mount } from "../dom.js";

const BAND_MAX = 2;

/** Dropped when the day turns, and after a ballot lands. */
let cache = null;
let inflight = null;

export function invalidateDebateHistory() {
  cache = null;
}

function history() {
  if (cache) return Promise.resolve(cache);
  inflight ??= api
    .get("/api/debate/history")
    .then((data) => (cache = data))
    .catch((error) => (cache = { debates: 0, lines: [], error: error.message }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/**
 * One line's bar, with its trend beside it.
 *
 * A bar rather than a sparkline: eight sparklines of 0/1/2 values is eight
 * pieces of noise, and the question being asked ("how am I doing on this")
 * has a single-number answer. The trend arrow carries the direction the
 * sparkline would have shown.
 */
function lineRow(line) {
  const pct = Math.round((line.recent / BAND_MAX) * 100);
  const tone = line.recent >= 1.5 ? "ok" : line.recent >= 1 ? "gold" : "bad";
  const arrow = line.trend > 0.15 ? "↑" : line.trend < -0.15 ? "↓" : "";

  return el("div.ballot-line", null,
    el("div.row.spread", null,
      el("span", { text: line.label }),
      el("span", null,
        el(`span.${tone}`, { text: line.recent.toFixed(2) }),
        el("span.faint", { text: `/2 · ${line.average.toFixed(2)} all-time` }),
        arrow
          ? el(`span.${line.trend > 0 ? "delta-up" : "delta-down"}`, {
              text: ` ${arrow}`,
              title: `${line.trend > 0 ? "+" : ""}${line.trend.toFixed(2)} against everything before the last ten`,
            })
          : null,
      ),
    ),
    el("div.bar", null, el(`div.bar-fill.bar-${tone}`, { style: `width:${pct}%` })),
  );
}

export default {
  id: "debate-stats",
  title: "what the ballot keeps saying",
  tab: "politics",
  slot: "grid",
  span: 2,
  collapsible: true,

  async render(node) {
    const data = await history();
    if (data.error) {
      mount(node, el("div.err", { text: data.error }));
      return;
    }
    if (!data.debates) {
      mount(node, el("div.faint", {
        text: "nothing yet — this fills in once a ballot has been scored",
      }));
      return;
    }

    const scores = data.scores.map(([, score]) => Math.round(score * 16));
    const conviction = data.conviction.filter((c) => c.position);
    const moved = conviction.filter((c) => c.moved).length;

    mount(node,
      el("div.faint", {
        text: `${data.debates} debate(s) · weakest first · "recent" is the last 10`,
      }),
      ...data.lines.map(lineRow),

      data.fouls.length
        ? el("div.divider", null,
            el("div.faint", { text: "fouls called" }),
            el("div.dim.small", {
              text: data.fouls.map((f) => `${f.kind} ×${f.count}`).join(" · "),
            }),
          )
        : null,

      // Surfaced rather than buried: a judge that pads its ballot is one whose
      // other numbers read high, so the count of lines thrown out for having no
      // quotation is a check on the instrument, not on you.
      data.voided
        ? el("div.faint.small", {
            text: `${data.voided} line(s) voided for claiming a point with no quotation`,
          })
        : null,

      scores.length >= 2
        ? el("div.chart-block", null,
            el("div.chart-title", null,
              el("span", { text: "ballot score /16" }),
              el("span.gold", { text: String(scores[scores.length - 1]) }),
            ),
            lineChart(scores),
          )
        : null,

      conviction.length
        ? el("div.divider", null,
            el("div.faint", {
              text: `conviction · ${moved}/${conviction.length} debate(s) moved your position`,
            }),
            // Newest first, and capped: this is a glance at whether arguing
            // changes your mind, not the archive. The dossiers hold the rest.
            ...conviction.slice(-4).reverse().map((point) =>
              el("div.dim.small", null,
                el("span.faint", { text: `${point.date} ${point.issue} ` }),
                `${point.confidence}/100 `,
                point.moved ? el("span.gold", { text: "· moved" }) : null,
              ),
            ),
          )
        : null,
    );
  },
};
