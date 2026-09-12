// Am I recovering, am I training enough, which way is my weight going.
//
// Three questions, and this panel still exists to answer those three and stay
// quiet about everything else. What changed in this pass is that it is now the
// *only* thing on the tab: the plan card, the five-tab logging card and the
// overnight strip are all gone, and their content is either here or on the
// phone. So the rule the panel was built on — **nothing is said until it can
// be said honestly** — is extended rather than repealed, and it had to be,
// because it now has to cover a screen that used to have five other cards on
// it to be quiet next to.
//
// --- what was repealed, and what it cost -------------------------------------
//
// This file used to say, at some length, that there would be no readiness
// score, no recovery percentage and nothing resembling a single "are you
// ready" number — on the grounds that a composite of five things that all move
// for unrelated reasons is exactly the figure you learn to check every morning
// and act on every week. That argument was right about composites and wrong
// about this screen, and what made it wrong was the deletions: a panel that
// speaks only when it has a fortnight of history is a *fine* panel next to
// four cards you are filling in, and a blank one when it is the whole tab.
//
// So there are four scores now, and the objection is answered three ways
// rather than by not building them. The argument is written out in full at the
// head of the rail section in dashboard/health.py; the half that lives here is
// the third one: **every part is one keystroke away.** `f` then a letter opens
// a zone in place and draws each of its inputs as its own bar against its own
// baseline, with the raw reading printed next to it. A composite you cannot
// take apart is a composite you end up believing.
//
// --- and what did not change -------------------------------------------------
//
// Every derived field still arrives null until it has the history behind it,
// and a null still renders as nothing at all — not a dash, not a zero, not
// "collecting data". What is new is that it now renders as *words saying which
// kind of nothing it is*, which is [PART 5] of this pass and is the one thing
// the deletions made unavoidable: while there were boxes on this tab, a
// missing reading was a hand entry that had not arrived yet. Now nothing is
// typed here, so a missing reading is a hole — and a night the watch spent on
// the charger renders identically to a rest day unless something insists
// otherwise. `InputState` is what insists.

import * as api from "../api.js";
import { el, mount, svg } from "../dom.js";
import { rerender } from "../panel.js";
import { put, store } from "../store.js";

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
/** Drinks of coffee a day, as a weekly mean, before it is worth a sentence.
 *  Three twelve-ounce mugs — under that it is a habit, over it there is enough
 *  of it landing after noon to be worth naming next to the sleep line. */
const CAFFEINE_FLAG = 3.0;
/** Standard drinks in seven days before the total is said out loud. Not a
 *  limit and not advice: it is the point at which the number is likely to be
 *  explaining something else already on this panel, and putting the two
 *  sentences next to each other is the whole job here. */
const ALCOHOL_WEEK_FLAG = 4.0;

const one = (n) => n.toFixed(1);
const whole = (n) => Math.round(n).toLocaleString();
/** One decimal, and none when it is a whole number: `4`, `4.5`. */
const trim = (n) => String(Math.round(n * 10) / 10);

/**
 * The four zones of the rail, in the order they are read.
 *
 * `key` is the letter `f` waits for, and it is printed on the zone — the same
 * argument as the digit on a section tab, that a shortcut you have to look up
 * is one you never learn.
 *
 * The letters are the logging carousel's, inherited rather than invented. That
 * card is gone and its five letters came free, and four of them land on
 * roughly what they used to reach: `c` opened the check-in and recovery is
 * where the check-in's answers now surface, `l` opened the session log and
 * load is what sessions make, `m` was meals and `d` was drinks. `p` — the old
 * in-and-out tab — is kept as a second way to say hydration, since urine
 * colour is one of that zone's inputs and `p` is the letter that used to reach
 * it.
 *
 * Inheriting them is not sentiment, it is what keeps `nav.HINT_KEYS` correct:
 * those five letters are already withheld from the link alphabet, so this
 * table spends nothing new. Picking mnemonic letters instead — r, o, u, y —
 * would have taken four letters *out* of the hint alphabet and dropped it
 * under the twenty a headline list needs, which `tests/js/keys.test.js`
 * guards. See the note there.
 */
export const ZONES = [
  { key: "c", id: "recovery", label: "recovery" },
  { key: "l", id: "load", label: "load" },
  { key: "m", id: "fuel", label: "fuel" },
  { key: "d", id: "hydration", label: "hydration", alias: "p" },
];

/** `key: zone id`, for the hint layer in keys.js. Derived rather than written
 *  out a second time, so the letter printed on the zone and the letter that
 *  works are one fact. */
export const ZOOMS = Object.fromEntries(
  ZONES.flatMap((zone) => [
    [zone.key, zone.id],
    ...(zone.alias ? [[zone.alias, zone.id]] : []),
  ]),
);

/**
 * Which zone is expanded, or null.
 *
 * Module-level because the panel re-renders underneath it — every store change
 * rebuilds this body, and a cursor held in a render closure would be thrown
 * away by the write that opened it.
 */
let zoomed = null;

/**
 * Open a zone's zoom in place, or close it if it is already open. True when
 * the key meant something, so the caller knows whether to swallow it.
 *
 * Redraws through the harness rather than through the store, and that is the
 * distinction worth keeping: opening a zoom changes nothing the server knows
 * about, so a `notify` would be announcing a write that never happened.
 */
export function zoom(id) {
  if (!ZONES.some((zone) => zone.id === id)) return false;
  zoomed = zoomed === id ? null : id;
  rerender("health");
  return true;
}

// `metricToday` and `weightToday` used to live here, and they live in `day.js`
// now. Nothing about them changed and nothing here calls them: their one caller
// is the day's required list, which the phone reads too, and `day.js` is the
// file the phone is allowed to import — this one reaches for the store, the
// charts and half the app.

/**
 * The verdicts, in the order the questions matter.
 *
 * Each returns a line or nothing. The thresholds live here rather than on the
 * server because they are editorial — what counts as worth mentioning — while
 * the numbers behind them are not.
 *
 * These are the panel's older half and they are unchanged. The rail says what
 * the state *is*; these say the handful of things worth interrupting you
 * about, and a sentence still beats a bar for anything that took three days in
 * a row to become true.
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
    // The drink log first and the watch second, and the watch's half of this
    // is now what it sweated out rather than what its glyph was tapped for.
    // `fluid_ml` is the thing you were going to log anyway; `sweat_loss_ml` is
    // the thing nobody had to remember at all, and it is the reason the
    // sentence can say what the day *asked* for rather than only what went in.
    const water = d.fluid_ml != null
      ? ` you logged about ${whole(d.fluid_ml)} ml a day`
        + (d.sweat_loss_ml != null ? ` and sweated about ${whole(d.sweat_loss_ml)}.` : ".")
      : "";
    lines.push([
      "bad",
      `urine has read dark ${d.dark_urine_days} days running, first of the morning `
      + `excluded.${water} that is the reading that matters while you are cutting: `
      + `it comes off the scale as progress and out of your legs as work.`,
    ]);
  }

  // -- the other two things in the drink log, and both are here for the same
  //    reason: they are the cheapest explanations for lines already above
  //    them. A flat HRV week and a sleep score that does not match the hours
  //    in bed both have a duller cause than overtraining more often than not,
  //    and the panel is more useful for saying so than for another warning.
  //
  //    Neither speaks unless the log was being kept — the null does that, in
  //    health.py — and neither says anything about a normal week.
  if (d.caffeine_drinks != null && d.caffeine_drinks >= CAFFEINE_FLAG) {
    lines.push([
      "dim",
      `coffee has run ${trim(d.caffeine_drinks)} drinks a day this week, twelve ounces each. `
      + `it is the half of it after noon that shows up in the sleep score rather than `
      + `in how awake you feel.`,
    ]);
  }

  if (d.alcohol_week != null && d.alcohol_week >= ALCOHOL_WEEK_FLAG) {
    lines.push([
      "dim",
      `${trim(d.alcohol_week)} standard drinks in the last seven days. `
      + `it lands on hrv and on deep sleep for about two nights each, which is `
      + `where to look for it if those are down.`,
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
 * Four different sentences on purpose, and this is the whole reason the
 * function is exported and tested rather than inlined. A rest day the plan
 * chose, a week nobody has written yet, a plan that has run out, and a camp
 * that is stopped all arrive as the same empty list, and they mean completely
 * different things. The one that would do real damage is the second reading as
 * the first: eleven unwritten weeks quietly telling you to rest for three
 * months, in a panel whose entire job is to notice that kind of thing.
 *
 * Paused is checked before `week_planned` and that order is load-bearing. A
 * paused week is still a written week — `week_planned` stays true, because the
 * plan for it exists and you are simply not in it — so testing that first
 * would render a fortnight of illness as a fortnight of "rest day.".
 */
export function emptyLine(plan, today) {
  if (plan.today.length) return null;
  if (plan.paused) {
    const day = `day ${plan.paused.days}`;
    return `camp paused since ${plan.paused.since} — ${day}. ${plan.paused.reason}.`;
  }
  if (plan.week_planned) return "rest day.";
  return plan.ends && plan.ends < today
    ? `the plan runs out on ${plan.ends}. nothing written past it yet.`
    : "no plan written for this week.";
}

// --- the hero ----------------------------------------------------------------

/**
 * Today's session in large type, how long the camp has left, and the phase.
 *
 * Display only. This is the content the plan card used to carry, minus every
 * box: the plan card was a nudge and a form in one object, and the form half
 * has moved to the phone, where the session actually gets logged from — see
 * `sessionLog` in `m/app.js`, which answers prescribed blocks by key.
 *
 * What survives is the nudge half, and it is the right half to keep on a
 * laptop. "What am I supposed to be doing today" is a thing you read at a desk
 * with coffee; "I have just finished the lift" is a thing you say standing in
 * a doorway with a phone.
 *
 * The phase is one word out of `[program]` in training.toml, printed as it is
 * written. It is also in the header on every tab, and the repetition is
 * deliberate: on this tab it is the frame the whole rail is read against, and
 * a header segment is not something you look at.
 */
function hero(plan) {
  if (!plan) return null;

  const asks = plan.today;
  const outings = [...new Set(asks.map((b) => b.slot))].sort((a, b) => a - b);
  const empty = emptyLine(plan, api.today());
  const phase = store.config?.phase;

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
      // Empty rather than "phase: " with nothing after it, for the same reason
      // the header does it: a label with no value reads as a value that failed
      // to load.
      phase ? el("span.figure-label", { text: phase.toLowerCase() }) : null,
    ),
    empty
      // A paused camp is the one empty line that is not faint. The other three
      // are the plan working as designed and are footnotes to a day you are
      // getting on with; this one is a state the app is in until you come back
      // and end it, and an open break nobody notices is a camp that is quietly
      // off for a month.
      ? el(`div.gap.${plan.paused ? "value" : "faint"}`, { text: empty })
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

// --- the flag line -----------------------------------------------------------

/**
 * One line under the hero: is anything wrong that nothing else has noticed.
 *
 * **This is a placeholder and is meant to look like one.** It renders one
 * sentence and consults nothing, and that is deliberate — the anomaly
 * detection this seam is for gets its own pass, because it is the one part of
 * this tab where a hasty version is worse than none. A flag that fires on
 * noise is a flag you learn to ignore inside a fortnight, and by then it has
 * also taught you to ignore the real one.
 *
 * What will go here: something that reads the *shape* of the last few weeks
 * rather than any one threshold — a metric that has left its own distribution,
 * two that have started disagreeing when they normally track, a series that
 * has simply stopped arriving. Every one of those needs a model of normal, and
 * `health.py` is where it will live, next to the baselines it will be built
 * out of. Nothing about that belongs on the client.
 *
 * The seam is the signature: this takes the derived block and returns a line
 * or null, and the day it stops being a placeholder is the day the argument
 * gets used.
 */
export function flagLine(_derived) {
  return "everything looks normal.";
}

// --- the rail ----------------------------------------------------------------

/**
 * One score as a bar, plus the zoom when it is open.
 *
 * The bar is drawn *only* when there is a number. A zone with nothing behind
 * it renders its sentence instead, because an empty bar and a bar at zero are
 * the same picture and they are opposite facts — one is a watch that said
 * nothing, one is a week that went badly. That distinction is the whole of
 * [PART 5] and it is why `ZoneScore.value` is nullable rather than defaulted.
 *
 * --- a mean of one is not a mean -------------------------------------------
 *
 * The other rule here, and it was found on the screen rather than reasoned to.
 * In week three of a camp every history-gated input is silent, so the load
 * zone was a single number — the fraction of the week's prescribed blocks that
 * had been logged — wearing the word `load`. It read `load 0` beside `1356
 * load this week`, which is two contradictory statements about the same
 * fortnight, and only one of them was about load at all.
 *
 * So a zone standing on exactly one input says which one: `load · plan logged`.
 * The number is unchanged and still honest; what it stops doing is claiming to
 * be a summary of four things when it is a reading of one. The threshold is
 * one rather than two because the fix is to the *label*, not to the score —
 * blanking these zones would be the panel going quiet through exactly the
 * weeks it was rebuilt to fill.
 */
function zoneNode(zone, score) {
  const open = zoomed === zone.id;
  const has = score.value != null;
  const scored = score.inputs?.filter((i) => i.value != null) ?? [];
  const alone = scored.length === 1 ? scored[0].label : null;

  return el(`div.zone${open ? ".zone-open" : ""}`, { dataset: { zone: zone.id } },
    el("button.zone-head", {
      // The letter, printed on the control it operates — same argument as the
      // digit on a section tab. `.tab`'s own `data-key` styling draws it.
      dataset: { key: zone.key },
      "aria-expanded": String(open),
      onclick: () => zoom(zone.id),
    },
      el("span.zone-name", { text: zone.label }),
      alone ? el("span.zone-only.small.faint", { text: `· ${alone}` }) : null,
      has
        ? el("span.zone-value", { text: String(Math.round(score.value)) })
        : el("span.zone-value.faint", { text: "—" }),
    ),
    has ? meter(score.value) : null,
    // The absence, in words, and it is the line that had to exist once the
    // boxes went. "no watch data: hrv, sleep score" is a different morning
    // from "not logged: meals", and before this pass both rendered as a
    // shorter strip.
    score.note ? el("div.zone-note.small.faint", { text: score.note }) : null,
    open ? zoomNode(score) : null,
  );
}

/** A bar. `.bar`/`.bar-fill` are the app's own, so this is the same object the
 *  rest of the dashboard draws for a proportion. */
function meter(value, { tone = null } = {}) {
  const band = tone ?? (value >= 66 ? "bar-ok" : value >= 33 ? "bar-gold" : "bar-bad");
  return el("div.bar", {
    role: "meter",
    "aria-valuenow": String(Math.round(value)),
    "aria-valuemin": "0",
    "aria-valuemax": "100",
  },
    el(`div.bar-fill.${band}`, { style: `width:${Math.max(2, Math.min(100, value))}%` }),
  );
}

/**
 * The zoom: every input of one zone, each against its own baseline.
 *
 * This is the half that makes the composite above it defensible. Each row is
 * the input's own bar plus the raw reading in words — `58 ms against a 62
 * baseline` — so a score of 34 is a claim you can check rather than one you
 * either believe or ignore.
 *
 * An input that did not score keeps its row and says why. Dropping it would
 * shorten the list on exactly the mornings the list is most worth reading.
 */
function zoomNode(score) {
  return el("div.zoom", null,
    ...score.inputs.map((input) => {
      const scored = input.state === "scored" && input.value != null;
      return el("div.zoom-row", null,
        el("div.row.spread.gap", null,
          el("span.small", { text: input.label }),
          el("span.small.faint", {
            text: input.reading ?? ABSENT[input.state] ?? "",
          }),
        ),
        scored
          ? meter(input.value)
          // A rule where the bar would be, rather than an empty bar: an
          // unfilled track reads as a zero at a glance, and a zero is a
          // measurement.
          : el("div.bar.bar-absent"),
      );
    }),
  );
}

/**
 * How each absence is said in a zoom row, when the input had no reading to
 * print instead.
 *
 * The server sends the same words in `ZoneScore.note` — this table is only for
 * the per-row case, and it agrees with `health.ABSENT_WORDS` on purpose. The
 * two are allowed to be separate because the note is a summary and this is a
 * label, but they must not disagree about what a missing row *means*.
 */
const ABSENT = {
  no_watch_data: "no watch data",
  no_baseline: "not enough history yet",
  not_logged: "not logged",
  not_asked: "nothing asked today",
};

/**
 * The two slow trends, right of the bars.
 *
 * Not bars, and the reason is worth stating because it is the same reason
 * there are only four bars: neither of these has a 0-100 direction without a
 * target, and nobody has set one. A weight of 172 lb is not 60% of anything,
 * and a VO2max of 52 is not 52% either — it only looks like a percentage.
 *
 * So they are a figure and a direction, which is all either of them supports.
 */
function trends(d) {
  const rate = d.weight_per_week;
  const direction = rate == null
    ? null
    : Math.abs(rate) < 0.15
      ? "holding steady"
      : `${rate < 0 ? "down" : "up"} ${Math.abs(rate).toFixed(1)} lb a week`;

  return el("div.trends", null,
    el("div.trend", null,
      el("div.trend-head.small.faint", { text: "weight" }),
      d.weight_ema != null
        ? el("div.figure-row", null,
            el("span.trend-value", { text: one(d.weight_ema) }),
            el("span.figure-unit", { text: "lb" }),
          )
        : el("div.small.faint", { text: "ten readings before this says anything" }),
      direction ? el("div.small.dim", { text: direction }) : null,
    ),
    el("div.trend", null,
      el("div.trend-head.small.faint", { text: "vo2max" }),
      d.vo2max_last != null
        ? el("div.figure-row", null,
            el("span.trend-value", { text: one(d.vo2max_last) }),
            el("span.figure-unit", { text: "ml/kg/min" }),
          )
        // Not a failure and not a gap to apologise for: this only updates
        // after an outdoor run with heart rate, so a lifting month has none.
        : el("div.small.faint", { text: "no estimate yet — needs an outdoor run" }),
      d.vo2max_change != null
        ? el("div.small.dim", {
            text: `${d.vo2max_change >= 0 ? "+" : ""}${one(d.vo2max_change)} over the quarter`,
          })
        : d.vo2max_on != null
          // The date, so a three-week-old estimate cannot read as this
          // morning's. This is the one number on the tab exempt from "today or
          // nothing", and saying when is what buys the exemption.
          ? el("div.small.faint", { text: `last measured ${d.vo2max_on}` })
          : null,
    ),
  );
}

/**
 * How many logged days it takes before this is a chart.
 *
 * Below it, it is not a sparse chart -- it is two cyan rectangles standing in
 * an empty box with no axis under them and nothing beside them to be read
 * against, which is close enough to a pair of stray scrollbars that that is
 * what they were reported as. The gaps only become the interesting part once
 * there is enough filled in for them to be gaps *in* something.
 */
const MIN_LOGGED_DAYS = 4;

/** Daily load, as bars. The gaps are rest days and are the interesting part. */
function loadBars(loads, { w = 300, h = 40 } = {}) {
  if (!loads?.length) return null;
  const values = loads.map(([, v]) => v);
  if (values.filter((v) => v > 0).length < MIN_LOGGED_DAYS) return null;
  const max = Math.max(...values, 1);
  const slot = w / values.length;
  return svg("svg.load-bars", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none" },
    // The baseline, first and always. Bars drawn against nothing float; drawn
    // against a rule they sit on a week, and a rest day reads as a gap in it
    // rather than as the absence of anything.
    svg("line", {
      x1: 0, y1: h - 0.5, x2: w, y2: h - 0.5,
      stroke: "var(--border)", "stroke-width": 1,
      // The chart is stretched horizontally to fill the card, so a plain
      // stroke would be scaled with it and come out heavier than a 1px rule.
      "vector-effect": "non-scaling-stroke",
    }),
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

/**
 * One line about the watch, and nothing to press.
 *
 * This exists because of how this integration fails. `python-garminconnect` is
 * unofficial and Garmin has broken its predecessor once already; when it goes,
 * every reading on this panel degrades to *silence*, which used to be
 * indistinguishable from a quiet week and is now the difference between a bar
 * and four words. So the panel says out loud when the numbers last arrived.
 *
 * The manual sync button is gone with the rest of the tab's controls. It was
 * there because a morning with no numbers was a morning you might want to go
 * and fetch them; the sync now runs on every boot, and the honest answer to
 * "the numbers are missing" is the sentence this line already prints.
 *
 * Silent when the sync was never turned on — an unconfigured feature is not a
 * problem to report.
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

  return el(`span.${g.last_ok ? "faint" : "err"}.small`, { text: line });
}

// --- stopping the camp -------------------------------------------------------
//
// The one control on this tab, and it is deliberately the least convenient
// thing in the app.
//
// Everything else here is built to be answered in one keystroke from a doorway.
// This is the opposite case: it fires a handful of times a year, it moves weeks
// of training, and the cost of pressing it by accident is a camp that silently
// stops. So it sits in the foot rather than the hero, it takes two presses
// rather than one, the second of them will not go without a reason typed into
// it, and it has no `f`-key — the hint alphabet is close enough to the floor
// `keys.test.js` guards that spending a letter here would be spending it badly,
// and this is not a thing you should be able to reach by muscle memory.
//
// What it does NOT do is ask "are you sure". The reason field is the
// confirmation, and it is a better one: it costs the same pause for thought and
// leaves something behind that is worth reading in February.

/** Whether the reason field is showing.
 *
 *  Module-level rather than in the store, because it is not a fact about the
 *  day — nothing has happened yet. Putting it through `put` to open a text box
 *  would have every other subscriber redraw for a keystroke.
 *
 *  What survives a redraw is the flag and not the text: a meal logged on the
 *  phone lands through `pulse.js`, redraws this panel, and the field comes back
 *  empty. That is worth knowing and not worth fixing — the field holds four
 *  words for about as many seconds, and the alternative is a draft cached
 *  somewhere for a control used twice a year. */
let asking = false;

/** Open or close the reason field without touching the day. */
function ask(open) {
  asking = open;
  rerender("health");
  if (open) document.querySelector(".camp-why")?.focus();
}

async function send(path, body) {
  put("health", await api.post(path, body));
}

function campControl(plan) {
  if (!plan) return null;
  const paused = plan.paused;

  if (paused?.committed) {
    return el("span.row.gap", null,
      el("span.small.faint", { text: `camp resumes ${paused.resumes}` }),
      // The undo, and the reason the committed state is worth distinguishing
      // at all: resume is pressed on the day you feel better, and feeling
      // better on a Friday is a claim the Saturday sometimes withdraws.
      el("button.ghost.tiny", {
        text: "not yet",
        onclick: () => send("/api/camp/resume", { cancel: true }),
      }),
    );
  }

  if (paused) {
    // The whole arithmetic, printed before it is agreed to. Which week runs
    // again and from what date are exactly the two things that would otherwise
    // be discovered a week later, by which time the shift is in the table and
    // the only way back is the one button that deliberately does not exist.
    const week = paused.restarts_week;
    const what = week == null ? "camp restarts" : `week ${week} restarts`;
    return el("button", {
      text: `resume — ${what} ${paused.resumes}`,
      onclick: () => send("/api/camp/resume", {}),
    });
  }

  if (!asking) {
    return el("button.ghost.tiny", { text: "pause camp", onclick: () => ask(true) });
  }

  const reason = el("input.camp-why", {
    placeholder: "why — illness, travel, injury",
    maxlength: 200,
    oninput: () => reason.classList.remove("bad"),
  });
  const commit = async () => {
    const why = reason.value.trim();
    // The reason IS the confirmation step. An empty one refuses rather than
    // defaulting to something bland, because a break labelled "paused" is a
    // break you will find in February and be unable to explain.
    if (!why) return void reason.classList.add("bad");
    asking = false;
    await send("/api/camp/pause", { reason: why });
  };
  reason.onkeydown = (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") ask(false);
  };

  return el("span.row.gap", null,
    reason,
    el("button", { text: "pause camp", onclick: commit }),
    el("button.ghost.tiny", { text: "cancel", onclick: () => ask(false) }),
  );
}

export default {
  id: "health",
  title: "training",
  tab: "health",
  slot: "hero",
  hero: true,

  /**
   * `f` then a letter, dispatched from the hint layer in keys.js.
   *
   * Exported through `ZOOMS` rather than handled here, for the same reason the
   * carousel's letters were: the hint layer has to know which letters are
   * spoken for before it paints the alphabet on this panel's links, and it
   * cannot know that by asking a panel that may not be mounted.
   */
  async render(node) {
    const panel = store.state.health;
    const d = panel.derived;
    const lines = verdicts(d);
    const flag = flagLine(d);

    mount(node,
      // The camp leads. Once there is a fight on the calendar, "how long have
      // I got" is the question every other number on this panel is in service
      // of — and unlike the rest of them it is true on day one, because a
      // countdown needs no history to be honest.
      hero(panel.plan),

      // One line, always, and for now always the same one. See `flagLine`.
      flag ? el("div.flag.small.dim", { text: flag }) : null,

      el("div.divider"),

      // The rail: four bars on the left, two trends on the right.
      el("div.rail", null,
        el("div.zones", null,
          ...ZONES.map((zone) => zoneNode(zone, d[zone.id] ?? {})),
        ),
        trends(d),
      ),

      lines.length
        ? el("div.verdicts", null,
            ...lines.map(([tone, text]) =>
              el(`div.verdict-line${tone === "plain" ? "" : `.${tone}`}`, { text }),
            ),
          )
        // Not an error state and not a progress bar. There is genuinely
        // nothing to say yet, and saying so once is the whole message.
        : el("div.faint.gap.small", {
            text: "nothing worth interrupting you about.",
          }),

      // The week's load, and who last spoke to the watch. Both are footnotes:
      // the bars are a shape rather than a number, and the sync line is only
      // ever read on a morning something is missing.
      el("div.row.spread.gap.foot", null,
        el("span.small.faint", {
          text: `${d.week_load} load this week`
            + (d.prev_week_load
              ? `, ${d.week_load >= d.prev_week_load ? "+" : ""}`
                + `${Math.round(((d.week_load - d.prev_week_load) / d.prev_week_load) * 100)}%`
                + " on last"
              : ""),
        }),
        watch(panel),
      ),
      loadBars(panel.loads),

      // Last of all, under the bars. This is the only thing on the tab that
      // writes, and where it sits is most of what stops it being pressed by
      // accident — see `campControl`. It keeps its own row so the two states
      // of it (a four-word button, or a text field and two more) do not shove
      // the load figure around as they swap.
      el("div.row.gap.camp-control", null, campControl(panel.plan)),
    );
  },
};
