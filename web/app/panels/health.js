// Am I recovering, am I training enough, which way is my weight going.
//
// Three questions, and this panel exists to answer those three and stay quiet
// about everything else. It used to be two number inputs over two line charts,
// which is a different thing entirely: a chart asks you to do the reading, and
// what you actually want at 7am is a sentence.
//
// The rule here is that **nothing is said until it can be said honestly.**
// Every derived field arrives null until it has the history behind it (see
// dashboard/health.py), and a null renders as nothing at all — not a dash, not
// a zero, not "collecting data". Week one of a camp shows a weight chart and
// silence, and that is the correct week-one panel. The alternative is a screen
// full of numbers computed off four days, which you would read, believe, and
// train differently because of.
//
// What is deliberately absent: a daily readiness score, a recovery percentage,
// and anything resembling a single "are you ready" number. The watch already
// offers Body Battery for that. Those numbers move for reasons that have
// nothing to do with training and everything to do with when you happened to
// fall asleep, and the honest window for this kind of signal is a week.

import * as api from "../api.js";
import { lineChart } from "../charts.js";
import { el, mount, svg } from "../dom.js";
import { store } from "../store.js";

const BAND = { low: 0.8, high: 1.5 };
const MONOTONY_FLAG = 2.0;
const DIP_DAYS = 3;

const one = (n) => n.toFixed(1);

/**
 * The verdicts, in the order the questions matter.
 *
 * Each returns a line or nothing. The thresholds live here rather than on the
 * server because they are editorial — what counts as worth mentioning — while
 * the numbers behind them are not.
 */
function verdicts(d) {
  const lines = [];

  // -- recovery. Speaks only on a run, never on one bad morning.
  if (d.readiness_dip_days >= DIP_DAYS && d.readiness_week != null) {
    lines.push([
      "bad",
      `readiness has sat below your own floor ${d.readiness_dip_days} mornings running `
      + `(${one(d.readiness_week)} this week against a ${one(d.readiness_baseline)} baseline). `
      + `that is a dip, not noise.`,
    ]);
  }

  // -- load.
  if (d.acwr != null) {
    const pct = Math.round(Math.abs(1 - d.acwr) * 100);
    if (d.acwr > BAND.high) {
      lines.push([
        "bad",
        `this week is ${pct}% above your four-week normal (ratio ${d.acwr.toFixed(2)}). `
        + `that is the jump the injury data doesn't like — hold it here rather than building on it.`,
      ]);
    } else if (d.acwr < BAND.low) {
      lines.push([
        "dim",
        `this week is ${pct}% below your four-week normal (ratio ${d.acwr.toFixed(2)}). `
        + `a deload, whether or not you meant to take one.`,
      ]);
    }
  }

  // -- monotony. The one warning you cannot feel and volume cannot show.
  if (d.monotony != null && d.monotony > MONOTONY_FLAG) {
    lines.push([
      "dim",
      `every day this week was about the same size (monotony ${d.monotony.toFixed(1)}). `
      + `it is the sameness that makes a block stale, not the total — put a genuinely easy day in.`,
    ]);
  }

  // -- weight. Stated flatly whenever there is a trend, since it is the one
  //    number with a deadline attached to it.
  if (d.weight_ema != null && d.weight_per_week != null) {
    const rate = d.weight_per_week;
    const direction =
      Math.abs(rate) < 0.15
        ? "holding steady"
        : `${rate < 0 ? "down" : "up"} ${Math.abs(rate).toFixed(1)} lb a week`;
    lines.push(["plain", `weight ${one(d.weight_ema)} lb trend, ${direction}.`]);
  }

  return lines;
}

/**
 * What to say on a day the plan asks for nothing — and null when it asks for
 * something, so the caller can just test it.
 *
 * Three different sentences on purpose, and this is the whole reason the
 * function is exported and tested rather than inlined. A rest day the plan
 * chose, a week nobody has written yet, and a plan that has run out all arrive
 * as the same empty list, and they mean completely different things. The one
 * that would do real damage is the middle reading as the first: eleven
 * unwritten weeks quietly telling you to rest for three months, in a panel
 * whose entire job is to notice that kind of thing.
 */
export function emptyLine(plan, today) {
  if (plan.today.length) return null;
  if (plan.week_planned) return "rest day.";
  return plan.ends && plan.ends < today
    ? `the plan runs out on ${plan.ends}. nothing written past it yet.`
    : "no plan written for this week.";
}

/**
 * The camp: how far out the fight is, and what today is asking for.
 *
 * Read-only. Filling these in is the session panel's job; this is the panel
 * that answers "what is the state of my training", the countdown belongs to
 * that question, and the day's list belongs beside it because the first thing
 * a plan is for is remembering what you are supposed to be doing.
 */
function camp(plan) {
  if (!plan) return null;

  const asks = plan.today;
  const outings = [...new Set(asks.map((b) => b.slot))].sort((a, b) => a - b);
  const empty = emptyLine(plan, api.today());

  return el("div.camp", null,
    el("div.figure-row", null,
      plan.days_out != null
        ? el("span.figure", { text: String(plan.days_out) })
        : null,
      plan.days_out != null
        ? el("span.figure-unit", { text: "days out" })
        : null,
      plan.week != null
        ? el("span.figure-label", { text: `week ${plan.week} of ${plan.weeks}` })
        : null,
    ),
    empty
      ? el("div.faint.gap", { text: empty })
      : el("div.asks", null,
          ...outings.map((slot) =>
            // One row per outing, because `*` in the plan means a separate trip
            // out of the house and `+` means the same one. Collapsing them
            // would lose the only thing the notation says about your day's
            // shape rather than its contents.
            el("div.row.spread.gap", null,
              el("span", null,
                ...asks
                  .filter((b) => b.slot === slot)
                  .flatMap((b, i) => [
                    i ? el("span.faint", { text: " + " }) : null,
                    el("span.value", { text: b.label }),
                    b.asks ? el("span.faint", { text: ` ${b.asks}` }) : null,
                    b.optional ? el("span.faint", { text: " (optional)" }) : null,
                  ]),
              ),
              el("span.faint", {
                text: asks.filter((b) => b.slot === slot).map((b) => b.name).join(" + "),
              }),
            ),
          ),
        ),
  );
}

/** Daily load, as bars. The gaps are rest days and are the interesting part. */
function loadBars(loads, { w = 300, h = 60 } = {}) {
  if (!loads?.length) return null;
  const values = loads.map(([, v]) => v);
  const max = Math.max(...values, 1);
  const slot = w / values.length;
  return svg("svg.load-bars", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none" },
    ...values.map((v, i) =>
      svg("rect", {
        x: (i * slot + slot * 0.15).toFixed(1),
        y: (h - (v / max) * h).toFixed(1),
        width: (slot * 0.7).toFixed(1),
        height: Math.max(v > 0 ? 1 : 0, (v / max) * h).toFixed(1),
        fill: "var(--accent-dim)",
      }),
    ),
  );
}

export default {
  id: "health",
  title: "training",
  tab: "health",
  slot: "hero",
  hero: true,

  async render(node) {
    const panel = store.state.health;
    const d = panel.derived;
    const lines = verdicts(d);

    const weight = panel.series.find((s) => s.metric === "weight")?.points ?? [];

    // The week's load leads, because it is the only figure here that is a
    // plain count rather than a judgement, and it is true from day one.
    const head = el("div.figure-row", null,
      el("span.figure", { text: String(d.week_load) }),
      el("span.figure-unit", { text: "load this week" }),
      d.prev_week_load
        ? el("span.figure-label", {
            text: `${d.week_load >= d.prev_week_load ? "+" : ""}`
              + `${Math.round(((d.week_load - d.prev_week_load) / d.prev_week_load) * 100)}% on last`,
          })
        : null,
    );

    // The camp leads. Once there is a fight on the calendar, "how long have I
    // got" is the question every other number on this panel is in service of —
    // and unlike the rest of them it is true on day one, because a countdown
    // needs no history to be honest.
    const strip = camp(panel.plan);

    mount(node,
      strip,
      strip ? el("div.divider") : null,
      head,
      lines.length
        ? el("div.verdicts", null,
            ...lines.map(([tone, text]) =>
              el(`div.verdict-line${tone === "plain" ? "" : `.${tone}`}`, { text }),
            ),
          )
        // Not an error state and not a progress bar. There is genuinely
        // nothing to say yet, and saying so once is the whole message.
        : el("div.faint.gap", { text: "not enough history to say anything worth acting on yet." }),
      el("div.divider"),
      el("div.chart-row", null,
        el("div.chart-block", null,
          el("div.chart-title", null,
            el("span", { text: "daily load, 4 weeks" }),
            d.monotony != null
              ? el("span.value", { text: `monotony ${d.monotony.toFixed(1)}` })
              : null,
          ),
          loadBars(panel.loads),
        ),
        el("div.chart-block", null,
          el("div.chart-title", null,
            el("span", { text: "weight" }),
            el("span.value", {
              text: weight.length ? `${weight[weight.length - 1][1]} lb` : "—",
            }),
          ),
          // Rounded before it is drawn, not for the line's sake but for the
          // axis labels: `lineChart` prints its own min and max verbatim, and
          // a value that arrives with float noise on it (a sync, or an average)
          // renders as `179.09514951112925` down the side of the chart.
          lineChart(weight.map(([, v]) => Math.round(v * 10) / 10), { color: "var(--gold)" }),
        ),
      ),
    );
  },
};
