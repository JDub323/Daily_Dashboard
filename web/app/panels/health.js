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
import { put, store } from "../store.js";
import { toast } from "../ui.js";

const BAND = { low: 0.8, high: 1.5 };
const MONOTONY_FLAG = 2.0;
const DIP_DAYS = 3;
/** Runs, not readings, for the two overnight scalars — same argument as the
 *  readiness dip, and the same number. */
const HRV_DIP_DAYS = 3;
const RHR_SPIKE_DAYS = 3;
const DARK_DAYS = 3;
/** Under this, as a weekly mean, is worth one line. Seven rather than eight:
 *  eight would have the panel say this most weeks, and a warning that is
 *  always on is a warning you stop reading. */
const SLEEP_FLOOR = 7.0;
const LIQUID_DAYS = 3;
const NO_STOOL_DAYS = 3;
/** Below this the week's food was mostly processed. Deliberately under the
 *  midpoint: 3 is a normal week with some convenience food in it, and a panel
 *  that complains about a normal week is one you stop reading. */
const QUALITY_FLAG = 2.5;
/** How far the meal estimate may disagree with the scale before the panel
 *  stops quoting it. A pound a week is roughly 500 kcal a day, which is one
 *  medium meal's worth of bucketing error — inside that the estimate is doing
 *  its job, outside it the buckets are wrong and saying so is the useful
 *  sentence. */
const BALANCE_DISAGREEMENT = 1.0;

const one = (n) => n.toFixed(1);
const whole = (n) => Math.round(n).toLocaleString();

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

  // -- the overnight pair, once the watch has a fortnight of its own to
  //    compare against. Said in one sentence when both fire, because they are
  //    one signal: HRV down and resting heart rate up on the same mornings is
  //    the textbook shape of accumulated fatigue, and two separate lines
  //    reporting it would read as two problems.
  const hrvDown = d.hrv_dip_days >= HRV_DIP_DAYS && d.hrv_baseline != null;
  const rhrUp = d.rhr_spike_days >= RHR_SPIKE_DAYS && d.rhr_baseline != null;
  if (hrvDown && rhrUp) {
    lines.push([
      "bad",
      `hrv has been under your own baseline and resting heart rate over it for `
      + `${Math.min(d.hrv_dip_days, d.rhr_spike_days)} mornings `
      + `(${one(d.hrv_last)} against ${one(d.hrv_baseline)} ms, `
      + `${Math.round(d.rhr_last)} against ${Math.round(d.rhr_baseline)} bpm). `
      + `both moving the wrong way at once is fatigue rather than a bad night.`,
    ]);
  } else if (hrvDown) {
    lines.push([
      "dim",
      `hrv has sat below your own baseline ${d.hrv_dip_days} mornings running `
      + `(${one(d.hrv_last)} against ${one(d.hrv_baseline)} ms).`,
    ]);
  } else if (rhrUp) {
    lines.push([
      "dim",
      `resting heart rate has been above your own baseline ${d.rhr_spike_days} mornings `
      + `(${Math.round(d.rhr_last)} against ${Math.round(d.rhr_baseline)} bpm).`,
    ]);
  }

  // -- sleep, as a week rather than as last night. One bad night is a bad
  //    night; seven six-hour nights during a hard block is the cheapest thing
  //    on this whole panel to fix, and the only one that fixes several of the
  //    others at the same time.
  if (d.sleep_week != null && d.sleep_week < SLEEP_FLOOR) {
    lines.push([
      "dim",
      `you have averaged ${one(d.sleep_week)} hours a night this week.`,
    ]);
  }

  // -- hydration. Speaks only on a run, and only off days with enough
  //    readings to have thrown the first one away — see health.day_darkness.
  if (d.dark_urine_days >= DARK_DAYS) {
    const water = d.hydration_ml != null
      ? ` the watch has you at about ${whole(d.hydration_ml)} ml a day.`
      : "";
    lines.push([
      "bad",
      `urine has read dark ${d.dark_urine_days} days running, first of the morning `
      + `excluded.${water} that is the reading that matters while you are cutting: `
      + `it comes off the scale as progress and out of your legs as work.`,
    ]);
  }

  // -- the gut. One line at most, and the liquid case wins when both hold,
  //    since it is the one that says something about this week's food.
  if (d.liquid_days != null && d.liquid_days >= LIQUID_DAYS) {
    lines.push([
      "dim",
      `${d.liquid_days} of the last seven days had a liquid stool on them. `
      + `over a hard block that is usually the food or the stress rather than a bug.`,
    ]);
  } else if (d.days_since_stool != null && d.days_since_stool >= NO_STOOL_DAYS) {
    lines.push([
      "dim",
      `nothing logged in that column for ${d.days_since_stool} days, in a week you were `
      + `otherwise keeping the log.`,
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
    lines.push(["plain", `weight ${one(d.weight_ema)} lb trend, ${direction}.` + balance(d)]);
  }

  // -- what the week's food was made of. Under the flag only, and last,
  //    because it is the slowest-moving thing on the panel.
  if (d.diet_quality != null && d.diet_quality < QUALITY_FLAG) {
    lines.push([
      "dim",
      `the week's meals averaged ${one(d.diet_quality)} out of 5 for how whole the food was.`,
    ]);
  }

  return lines;
}

/**
 * The energy balance, as a clause on the end of the weight sentence.
 *
 * Never its own line, and that is the whole editorial judgement here. The
 * balance is an estimate built on three buckets and the weight trend is a
 * measurement; giving them equal billing would invite you to believe the
 * arithmetic over the scale, which is exactly backwards.
 *
 * So it is attached to the measurement, and when the two disagree by more than
 * `BALANCE_DISAGREEMENT` the clause stops quoting a number and says the
 * estimate is off instead. That is not a hedge — it is the actionable version.
 * A meal log that predicts a pound down a week while the scale holds flat has
 * `MEAL_KCAL` wrong, or has meals missing from it, and both of those are fixed
 * by changing what you write down rather than what you eat.
 */
function balance(d) {
  if (d.balance == null || d.balance_lb_per_week == null) return "";
  if (Math.abs(d.balance_lb_per_week - d.weight_per_week) > BALANCE_DISAGREEMENT) {
    return " the meal log predicts a different rate than that, so it is missing meals"
      + " or the size buckets are wrong for you.";
  }
  const side = d.balance < 0 ? "under" : "over";
  return ` the food log agrees: about ${whole(Math.abs(d.balance))} kcal a day ${side}`
    + ` what the watch says you burned.`;
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
          ...outings.map((slot) => {
            // One row per outing, because `*` in the plan means a separate trip
            // out of the house and `+` means the same one. Collapsing them
            // would lose the only thing the notation says about your day's
            // shape rather than its contents.
            //
            // Extras are excluded outright. Sparring and neck are things a
            // practice sometimes contains, not things today asks for, and
            // listing them here would have the panel appear to prescribe
            // sparring five days a week.
            const parts = asks.filter((b) => b.slot === slot && !b.extra);
            if (!parts.length) return null;

            // A group is named once. Every block of a points-bearing group
            // carries `optional` — that is what puts it on the other layer of
            // the day — but printing it against each part turns one honest fact
            // into four words of noise that read as "none of this matters".
            const group = parts.find((b) => b.group)?.group;
            return el("div.row.spread.gap", null,
              el("span", null,
                el("span.value", { text: group ?? parts.map((b) => b.label).join(" + ") }),
                ...parts.flatMap((b) => [
                  b.asks ? el("span.faint", { text: ` ${b.asks}` }) : null,
                ]),
                !group && parts.every((b) => b.optional)
                  ? el("span.faint", { text: " (optional)" })
                  : null,
              ),
              el("span.faint", { text: parts.map((b) => b.name).join(" + ") }),
            );
          }),
        ),
  );
}

/**
 * Last night, as the watch measured it.
 *
 * A strip of numbers rather than sentences, and that is a different kind of
 * thing from everything above it on purpose: the verdicts are the panel
 * telling you something, and this is the panel showing you what it has. These
 * five are worth having on screen because they are *cheap* — nobody typed
 * them, they cost a glance, and Body Battery in particular is a number the
 * watch is better at than any model this app could build.
 *
 * What they are not is a readiness score. There is no sum here, no percentage,
 * and no traffic light. The judgement stays in the verdicts, where it has a
 * baseline and a run length behind it; a composite of five numbers that all
 * move for reasons unrelated to training is exactly the kind of figure you
 * would learn to check every morning and act on every week.
 *
 * Absent readings render as nothing, so a night on the charger shows a shorter
 * strip rather than a row of dashes. All five absent renders no strip at all.
 */
function overnight(d) {
  const cells = [
    d.sleep_last != null
      ? [
          `${one(d.sleep_last)}h`,
          d.in_bed_last != null ? `asleep of ${one(d.in_bed_last)} in bed` : "asleep",
        ]
      : null,
    d.sleep_score_last != null ? [String(Math.round(d.sleep_score_last)), "sleep score"] : null,
    d.hrv_last != null ? [`${Math.round(d.hrv_last)}`, "hrv, ms"] : null,
    d.rhr_last != null ? [`${Math.round(d.rhr_last)}`, "resting bpm"] : null,
    d.body_battery_last != null ? [String(Math.round(d.body_battery_last)), "body battery"] : null,
  ].filter(Boolean);

  if (!cells.length) return null;
  return el("div.scalars", null,
    ...cells.map(([value, label]) =>
      el("div.scalar", null,
        el("span.scalar-value", { text: value }),
        el("span.scalar-label", { text: label }),
      ),
    ),
  );
}

/**
 * One line about the watch, and a button when there is something to press.
 *
 * This exists because of how this integration fails. `python-garminconnect` is
 * unofficial and Garmin has broken its predecessor once already; when it goes,
 * every reading above degrades to *silence*, which is indistinguishable from a
 * quiet week. So the panel says out loud when the numbers last arrived, and
 * nothing else on the tab has to carry that worry.
 *
 * Silent when the sync was never turned on — an unconfigured feature is not a
 * problem to report, and the tab was a hand-kept log before the watch and
 * still is one underneath it.
 */
function watch(panel) {
  const g = panel.garmin;
  if (!g.configured) return null;

  const when = g.last_sync
    ? new Date(g.last_sync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;
  const line = !g.installed
    ? "garminconnect is not installed in this venv — pip install '.[garmin]'"
    : g.last_ok
      ? when
        ? `watch synced ${when}${g.wrote ? ` · ${g.wrote} new` : ""}`
        : "watch not synced yet this session"
      : `watch sync failed: ${g.last_error}`;

  return el("div.row.spread.gap", null,
    el(`span.${g.last_ok ? "faint" : "err"}.small`, { text: line }),
    el("button.tiny", {
      text: "sync",
      // Runs on the request rather than behind it, so pressing it and seeing
      // nothing change is not a state this button can be in.
      onclick: async (e) => {
        const button = e.currentTarget;
        button.disabled = true;
        button.textContent = "syncing…";
        try {
          put("health", await api.post("/api/garmin/sync", {}));
        } catch (error) {
          toast(error.message, "error");
        } finally {
          button.disabled = false;
          button.textContent = "sync";
        }
      },
    }),
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

    // Last night, and who measured it. Above the verdicts because it is the
    // half of this panel that is true every morning — the sentences below need
    // a fortnight of history before they will say anything at all.
    const night = overnight(d);

    mount(node,
      strip,
      strip ? el("div.divider") : null,
      head,
      night,
      watch(panel),
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
              // Rounded for the same reason the series below it is: a reading
              // that arrives with float noise on it renders as
              // `172.97172499218095 lb` across the top of the chart.
              text: weight.length ? `${one(weight[weight.length - 1][1])} lb` : "—",
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
