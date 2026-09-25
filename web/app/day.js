// What a day is. The list itself, and nothing that draws it.
//
// This is `chips.js`'s table, lifted out whole, and the lift is the point: the
// phone now has a screen that asks the same question, and a second copy of
// "what does today ask for" is a second copy free to disagree. Two devices
// disagreeing about whether the day is finished is worse than either of them
// being wrong, because there is no way to tell which one to believe.
//
// So the definition lives here and both clients import it. `chips.js` keeps the
// ring, the gold borders, `n`, and the completion write — everything that is
// about *this* screen — and `web/m/app.js` draws the same list as a list.
//
// --- the one rule this file has to keep -------------------------------------
//
// It imports `logs.js` and nothing else, and `logs.js` imports nothing at all.
// That is not tidiness: `web/m/sw.js` caches the phone's whole module graph so
// the app opens with the laptop off, and an ES module graph fails *whole* — one
// unlisted import is a blank screen on the one morning the cache exists for.
// `tests/js/shell.test.js` is what enforces it. Anything added here that
// reaches for `api.js`, `store.js` or a panel breaks the phone, silently, until
// the next cold open.
//
// That is also why `metricToday` moved here from `panels/health.js` rather than
// being imported from it: it is four lines over `state`, and importing it would
// have dragged the health panel, the charts and the store onto the phone.
//
// --- the two layers ---------------------------------------------------------
//
// The list used to be eleven things you owed, and the ring was `done/11`. That
// made every task equally load-bearing, which meant none of them were: a day
// where the math got done and the Pokedoku did not scored the same as its
// opposite. Worse, it made a perfect day rare enough to stop meaning anything,
// and the streak counted off it spent most of its life at zero.
//
// So the day is layered:
//
//   required   the math tab minus the deck — the problem, the trick, the
//              theorem — plus the morning check-in and three logged meals.
//              Five things. This is the ring, this is what `perfect ✦` means,
//              and this is the only thing the streak is counted off. Small on
//              purpose: a floor you can hold every day is worth more than a
//              ceiling you reach twice a month.
//
//   optional   everything else, worth `points`. Still tracked, still turns its
//              panel gold, still walked by `n` once the core is clear — what it
//              no longer does is stand between you and a perfect day. The
//              points are the reason to do them anyway, and they are written to
//              `day_completion.bonus`, so a heavy day is still legible a month
//              later rather than evaporating at midnight.
//
// Why those five. The three math cells are the only ones on the list that
// compound — the problem moves an elo, and the trick and the theorem are the
// deck that makes the next problem cheaper — so doing them on the days you do
// not feel like it is the whole point of the app. The check-in is here for the
// opposite reason: it is thirty seconds of typing whose value is *entirely* in
// being unbroken, because everything derived from it is a windowed mean, and a
// series answered four mornings a week produces baselines that drift for
// reasons that have nothing to do with training. The meals are here on a
// third argument again, spelled out at the task: they are the one required
// thing that is a *report* rather than a task, and a report you can always
// file is the safest thing there is to require.
//
// The weights are a judgement call and meant to be edited. The rule they follow
// is minutes-and-attention, not importance: the ballot is an evening, the chess
// puzzle and the word are a real sitting each, and a Wordle is a Wordle.

import { squareMeals } from "./logs.js";

/**
 * How many meals a day the log expects.
 *
 * Three, and it is a claim about the eater rather than a target: he eats
 * three times a day, so a day with two rows in it is a day with a row
 * missing. Named here rather than left as a literal because both the task
 * and the nudge that chases it have to mean the same number.
 */
export const MEALS_A_DAY = 3;

// Counted through `squareMeals`, which drops the rows flagged `snack`. Three
// apples are three logged rows and are not three meals, and the task saying
// otherwise would mark a required thing done on a day two meals went
// unrecorded -- which is exactly the hole the required half of the day exists
// to keep shut.

/** Today's reading for one metric, or null.
 *
 * The series is oldest-first, so the last point is today's only if its date
 * says so — which is the whole check: "the most recent weight" and "this
 * morning's weight" are the same number on most days and very different ones
 * on the day you skipped. */
export function metricToday(state, metric) {
  const series = state.health.series.find((s) => s.metric === metric);
  const last = series?.points?.[series.points.length - 1];
  return last && last[0] === state.date ? last[1] : null;
}

export const weightToday = (state) => metricToday(state, "weight");

/**
 * What the training plan puts on the day.
 *
 * Two kinds of thing, and the split is the plan's own apostrophe:
 *
 *   `training`  the blocks the plan asks for outright. Required — it joins the
 *               ring, and a day the plan prescribes something is a `done/5`.
 *               Safe to require precisely *because* the plan is what decides:
 *               a rest day prescribes nothing and the task is simply not
 *               offered, which is the objection that kept session logging off
 *               this list until there was a plan to consult.
 *
 *   a group     a standing commitment worth points — practice. Optional by
 *               construction: it has a `points` field, and "required exactly
 *               when it is worth no points" is still the one definition.
 *
 * Done means *every* prescribed block logged, not any. Otherwise a twelve-minute
 * neck workout would close a Monday that also wanted a lift and half an hour of
 * cardio.
 */
function planTasks(s) {
  const plan = s.health.plan;
  if (!plan) return [];

  const logged = new Set(
    s.health.sessions_today.filter((x) => x.plan_key).map((x) => x.plan_key),
  );
  const out = [];

  // Extras are excluded: sparring is a thing a practice sometimes contained,
  // and a day that could not be finished without it would be unfinishable most
  // of the week.
  const owed = plan.today.filter((b) => !b.optional && !b.extra);
  if (owed.length) {
    out.push({
      id: "training",
      label: owed.length > 1 ? `training (${owed.length})` : "training",
      done: owed.every((b) => logged.has(b.key)),
      panel: "health",
    });
  }

  for (const group of plan.groups) {
    if (!group.keys.length) continue;
    out.push({
      id: `plan:${group.name}`,
      label: group.name,
      done: group.keys.every((key) => logged.has(key)),
      panel: "health",
      points: group.points,
      provisional: group.provisional,
    });
  }
  return out;
}

/**
 * What ticks the day's required math, now that the week has four shapes.
 *
 * `status !== "open"` was the whole rule and it is wrong twice over on the new
 * week. A reading day has no attempt at all, so `status` sits at its default
 * and Sunday could never be ticked. A day-two-with-nothing-carried has no
 * attempt either, and that day was *earned* by solving day one — leaving it
 * unfinishable would make the reward cost a perfect day.
 *
 * `carried` counts as done deliberately. You did the day's work; the result
 * lands tomorrow, and the problem is still yours. The thing that must not be
 * ticked is a drawn problem you have not touched, which is exactly `open`.
 */
function mathDone(math) {
  if (math.mode === "reading") return Boolean(math.plan?.started);
  if (math.free_day) return true;
  return Boolean(math.problem) && math.status !== "open";
}

function mathLabel(math) {
  if (math.mode === "reading") return "reading";
  if (math.mode === "proof-long") return math.day === 2 ? "proof, day 2" : "proof, day 1";
  return math.mode === "compute" ? "math problem" : "proof";
}

/**
 * Everything today asks for, required first.
 *
 * `s` is a `DashboardState` — the desktop's store, or the copy the phone gets
 * back from `GET /api/day`. Pure over it, which is the property that lets two
 * clients share it.
 */
export function dayTasks(s) {
  // Filtered at the end: a day with nothing drawn has no task at all, rather
  // than one that can never go gold. That was about the denominator — an
  // unfinishable required task put a perfect day out of reach — and it still
  // is for the required ones. For the rest it is about the points: a total
  // whose maximum you cannot reach is a score against a moving target.
  return [
    { id: "math", label: mathLabel(s.math), done: mathDone(s.math), panel: "math" },
    {
      // Done means the *marked set* is submitted. The challenge is optional
      // and deliberately not part of this: a task that only went gold when you
      // had also done the hard one would make it not optional, and a perfect
      // day is counted off exactly these.
      id: "tricks",
      label: "math trick",
      done: s.tricks.status === "done",
      panel: "tricks",
    },
    {
      // Done means answered, not merely drawn. The boot payload never draws,
      // so an unopened day shows `today: null` — which is correctly not done.
      //
      // The recall, not the deck. Curating theorems is something you do when
      // you meet one, not something the day asks for, and the `theorem deck`
      // panel beside this one has never had a task.
      id: "theorem",
      label: "theorem recall",
      done: s.theorems.recalled !== null,
      panel: "theorem",
    },
    {
      // Weight *and* the questionnaire — the two halves are useless apart. A
      // weight series with no readiness beside it cannot tell a cut from
      // under-recovery, and readiness with no weight cannot tell either from
      // making weight badly.
      //
      // Logging the session is deliberately NOT a task — some days there is no
      // session, and one you cannot finish on a rest day would put a perfect
      // day out of reach.
      id: "checkin",
      label: "morning check-in",
      done: weightToday(s) !== null && Boolean(s.health.readiness_today),
      panel: "health",
    },
    {
      // Three meals, and required rather than worth points.
      //
      // Every other candidate for the required layer had to argue that it was
      // something you would do anyway; this one is the reverse. Three meals a
      // day is not a habit being built, it is a fact about the day that is
      // going to be true whether or not anybody writes it down -- so the task
      // is not "eat three times", it is "say so", and a task you can always
      // finish is exactly what the required layer is for. Nothing here can put
      // a perfect day out of reach on a bad week the way a prescribed lift
      // could.
      //
      // It earns the place because of what the log is *for*. The panel's whole
      // argument is estimated intake against the watch's measured expenditure,
      // and the honest version of that comparison is impossible on a series
      // with holes in it: two meals logged on a four-meal day does not read as
      // a missing row, it reads as a light day, and `implied_lb_per_week` will
      // quietly believe it.
      id: "meals",
      label: `meals (${Math.min(squareMeals(s.health.meals_today).length, MEALS_A_DAY)}/${MEALS_A_DAY})`,
      done: squareMeals(s.health.meals_today).length >= MEALS_A_DAY,
      panel: "health",
    },

    // The training plan's own tasks. Placed here rather than in the list below
    // because the required one belongs beside the other required ones — `n`
    // walks required first, and a lift you owe should come before a Wordle.
    ...planTasks(s),

    // --- optional: points, not obligations ---------------------------------
    {
      id: "chess",
      label: "chess puzzle",
      done: s.chess.status !== "open",
      panel: "chess",
      points: 2,
    },
    { id: "word", label: "word of day", done: Boolean(s.word?.grade), panel: "word", points: 2 },
    {
      // Beside the word of the day, because they are the same half of the day
      // — one grows the vocabulary, the other stops autocorrect quietly taking
      // the spelling of it away. Different words entirely: the bee draws from
      // `spelling.json` and never touches what the word panel generated.
      id: "spelling",
      label: "spelling bee",
      done: s.spelling.status === "done",
      panel: "spelling",
      points: 1,
    },
    {
      // Done means graded, not merely written. The write-up is the work, but
      // the grade is what checks it — and it is the one step that can tell you
      // a claim you were pleased with is false, which is the whole point of
      // the exercise.
      //
      // The heaviest thing on the list, and priced like it: an afternoon of
      // reading and a page of prose is the biggest single thing a day here
      // asks for.
      id: "investigate",
      label: "investigation",
      done: Boolean(s.investigate.day?.ballot),
      panel: "investigate",
      points: 3,
    },
    {
      // Same rule as the theorem task: done means answered, not merely drawn.
      // Hidden when the deck is empty rather than permanently red.
      id: "patterns",
      label: "argument pattern",
      done: s.patterns.recalled !== null,
      panel: "patterns",
      points: 1,
    },
    {
      id: "paper",
      label: "paper triage",
      done: Boolean(s.papers.surfaced) && s.papers.surfaced.status !== "surfaced",
      panel: "reading",
      points: 2,
    },
    {
      // These two are the only things on the list somebody else schedules: the
      // puzzle exists whether or not it suits your day. That was the argument
      // for making them optional first, and it survives the split unchanged.
      id: "wordle",
      label: "wordle",
      done: Boolean(s.games.wordle_today),
      panel: "games",
      points: 1,
    },
    {
      id: "pokedoku",
      label: "pokedoku",
      done: Boolean(s.games.pokedoku_today),
      panel: "games",
      points: 1,
    },
    { id: "log", label: "day log", done: Boolean(s.daylog), panel: "log", points: 1 },
    // The feel pings, a point each. The log exists to be correlated, and a log
    // answered only on bad afternoons has no baseline to correlate against —
    // so answering on the fine ones is the part worth paying for. A window
    // answered before its ping went out counts: you said it unasked, which is
    // what the ping was for. Absent when pings are off, since a point nobody
    // can ask you for is not on offer.
    ...(s.health.feel?.pings ?? []).map((ping) => ({
      id: `feel:${ping.name}`,
      label: `feel · ${ping.name}`,
      done: ping.state === "answered",
      panel: "health",
      points: 1,
    })),
    {
      // The one self-report on the list, and priced as the short sitting it
      // is. It exists because the panel is otherwise the same card whether or
      // not you played — every other cell here shows its own answer back, and
      // this one had nothing to show.
      id: "piano",
      label: "piano",
      done: s.piano_practiced,
      panel: "piano",
      points: 1,
    },
  ].filter(
    (task) =>
      // Nothing drawn means nothing owed: a day with an empty pool, or one
      // where the button has not been pressed, is not a day you have failed.
      (task.id !== "investigate" || Boolean(s.investigate.today))
      && (task.id !== "patterns" || s.patterns.deck_size > 0)
      && (task.id !== "spelling" || s.spelling.enabled)
      && (task.id !== "tricks" || s.tricks.enabled),
  );
}

/**
 * The ones a perfect day is counted from — the ones with no `points`.
 *
 * A task is required exactly when it is worth no points, which keeps the
 * distinction in one place: there is no second `optional` flag that can fall
 * out of step with the weights.
 */
export const required = (list) => list.filter((task) => !task.points);

/** The other layer. */
export const optional = (list) => list.filter((task) => task.points);

/** Points banked out of the ones on offer today. */
export function score(list) {
  const layer = optional(list);
  return {
    earned: layer.reduce((sum, task) => sum + (task.done ? task.points : 0), 0),
    // A `provisional` task counts toward the total only on a day it was
    // actually done. That is the Saturday practice which will start running at
    // some point and has not yet: leaving it in the denominator would score
    // every Saturday between now and then against three points that cannot be
    // earned, and taking it out entirely would make a Saturday you did go worth
    // nothing. Never a penalty, always a reward.
    available: layer.reduce(
      (sum, task) => sum + (task.provisional && !task.done ? 0 : task.points),
      0,
    ),
  };
}
