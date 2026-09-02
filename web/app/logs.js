// What the five logs ask. Nothing here knows how any of it is drawn.
//
// These tables used to live one per panel, beside the code that rendered them.
// Then there were two things rendering them — the desktop cards and the phone
// at `/m` — and they were moved here so the two could not disagree about what
// the questions were, which is the part the database has an opinion about.
//
// **There is now exactly one client again, and this file stays where it is.**
// The desktop cards are gone: the laptop stopped being a logging surface in
// the pass that made the health tab one panel, and every log is answered on
// the phone. Folding these tables back into `m/app.js` would be undoing the
// wrong half of that — this is the *contract*, and it is worth having in a
// file with no client in it precisely so the next surface that needs it (the
// watch, a shortcut, a script) inherits the questions rather than a copy.
//
// So a question still lives here once. A step added anywhere else is a column
// the table then rejects, after the answers have already been given, which is
// the worst possible moment to find out.
//
// The keys stay even though the phone has no keyboard. They cost one character
// each, a `[key, value, label]` triple that sometimes has a key and sometimes
// does not is a shape every reader has to check, and they are the part of this
// that a keyboard surface would need again.
//
// Nothing in this file imports anything. That is deliberate and it is what
// makes it shareable: the panels drag in the store, the DOM helpers, the
// capture stack and the nav ring, and none of that has any business on a phone
// that exists to post five fields.

// --- meals ------------------------------------------------------------------

/**
 * `[key, value, label]`. Sizes go by first letter and there is no collision to
 * resolve — `s`, `m`, `l` mean the same thing here that they mean on the
 * bathroom log's volume row, which is the point of using them twice.
 */
export const MEAL_SIZE = [
  ["s", "small", "small"],
  ["m", "medium", "medium"],
  ["l", "large", "large"],
];

/** 1–5, and the ends are printed beside the row. A scale whose direction you
 *  have to remember is one you will eventually answer backwards — the same
 *  argument the check-in makes at greater length. */
export const MEAL_QUALITY = [
  ["1", 1, "1"],
  ["2", 2, "2"],
  ["3", 3, "3"],
  ["4", 4, "4"],
  ["5", 5, "5"],
];

/** Two questions, no branch — the whole difference from the bathroom log. */
export const MEAL_STEPS = [
  { name: "size", hint: "roughly, in calories", options: MEAL_SIZE },
  { name: "quality", hint: "1 processed · 5 whole food", options: MEAL_QUALITY },
];

// --- drinks -----------------------------------------------------------------
//
// The four things about this log you cannot guess by looking at it. They were
// behind a `?` on the desktop card until that card was deleted, and they are
// here rather than gone because they are rules about what the *rows mean* —
// two years of them are going to be summed by `health.fluid_mean`, and a
// series where half the Gatorade Zeros were filed as gatorade is a series with
// two hundred calories a day of noise in it.
//
//   **One drink is twelve ounces of it.** A can is 1, a 16.9oz bottle is 1.4,
//   a mug about 0.8 — and the number is printed on the side of most of them.
//   This is the one quick log with a typed answer rather than a row of
//   buttons, because the answer is *written on the bottle*: size and volume
//   are buckets because nobody knows how big their lunch was, and rounding a
//   number you can read off the label would be throwing away accuracy that
//   cost nothing to have.
//
//   **`electrolyte` and `gatorade` differ on sugar and nothing else.** Which
//   is why a Gatorade Zero is `electrolyte` whatever the bottle says. Two
//   hundred calories is either a mid-session necessity or a soft drink, and
//   the log cannot tell them apart if they share a row.
//
//   **`coffee` counts as fluid.** The diuretic effect is real and smaller than
//   the water it arrives in. It is its own kind because the caffeine is worth
//   counting, not because the water in it is suspect.
//
//   **`alcohol` counts as no fluid at all, and changes the unit.** There a
//   drink is a *standard* drink — a shot, a beer and a glass of wine are each
//   1 — because the point is the alcohol rather than the volume, and twelve
//   ounces of spirits is not a quantity anybody means.

/**
 * `[key, value, label]`, in the order they are reached for.
 *
 * Four of the five keys are the first letter. Gatorade is `s`, for *sports
 * drink*, and the reason is the same one that made the old in-and-out tab `p`:
 * `g` is spent. It is half of `gg`, which is "first panel of this section" and
 * one of the keys `panel.js` will not let a card claim — so a `g` bound here
 * would answer while the ladder was up and do nothing at all at a card sitting
 * idle, which is worse than a letter you have to learn once.
 */
export const DRINK_KIND = [
  ["w", "water", "water"],
  ["e", "electrolyte", "electrolyte"],
  ["s", "gatorade", "gatorade"],
  ["c", "coffee", "coffee"],
  ["a", "alcohol", "alcohol"],
];

/**
 * Two questions: which, and how many.
 *
 * The box is preset to `1` and selected the moment the ladder reaches it, so
 * the common answer — one can, one mug, one beer — is `w` then Enter, and the
 * uncommon one is `w` `1` `.` `4` Enter. That keeps the budget: two keystrokes
 * for the case that happens ten times a day, and typing only when there is
 * actually something to say.
 */
export const DRINK_STEPS = [
  { name: "kind", options: DRINK_KIND },
  { name: "amount", field: { unit: "drinks", preset: "1", max: 20 } },
];

// --- in and out -------------------------------------------------------------

/**
 * `[key, value, label]`, in the order the ladder walks them.
 *
 * The keys are the first letters where they can be, and `s` goes to stool
 * rather than to solid or to small — the collision is resolved once, in favour
 * of the answer that comes first in the sequence, so the first keystroke never
 * has to be thought about. Everything downstream of it is unambiguous inside
 * its own step, which is what a per-step keyboard buys.
 */
export const BATHROOM_KIND = [
  ["s", "stool", "stool"],
  ["u", "urine", "urine"],
];

export const BATHROOM_SOLIDITY = [
  ["1", "liquid", "liquid"],
  ["2", "soft", "soft"],
  ["3", "solid", "solid"],
];

export const BATHROOM_COLOR = [
  ["1", "clear", "clear"],
  ["2", "light", "light"],
  ["3", "yellow", "yellow"],
  ["4", "darker", "darker"],
];

export const BATHROOM_VOLUME = [
  ["s", "small", "small"],
  ["m", "medium", "medium"],
  ["l", "large", "large"],
];

/**
 * The ladder, and the one branch any of the logs has: the second question is a
 * different question depending on the first answer.
 *
 * `when` is a predicate over the answers so far. Both clients honour it through
 * `applicableSteps` at the foot of this file, so the rule is read once rather
 * than copied.
 * Getting this wrong in the direction of *too many* steps means a urine entry
 * stopping to ask for a solidity, and the table rejecting the row after the
 * answers have been given; the other way means committing a row with a field
 * missing, which is the same rejection with less warning.
 */
export const BATHROOM_STEPS = [
  { name: "kind", options: BATHROOM_KIND },
  {
    name: "solidity",
    hint: "1 loosest · 3 formed",
    options: BATHROOM_SOLIDITY,
    when: (draft) => draft.kind === "stool",
  },
  {
    name: "color",
    hint: "1 clear · 4 dark",
    options: BATHROOM_COLOR,
    when: (draft) => draft.kind === "urine",
  },
  { name: "volume", options: BATHROOM_VOLUME },
];

// --- the morning check-in ---------------------------------------------------

/**
 * `[key, label, low, high]` — the five questions and the words at either end.
 *
 * Order is not arbitrary: motivation sits second rather than last, because last
 * is the worst seat in a five-item questionnaire. It is the one answered while
 * you are already reaching for the next thing, and of the five it is the only
 * question about the day ahead rather than a reading off the body.
 *
 * Reordering is free. The row is stored by key, so the five columns are
 * untouched and every morning logged before a reorder stays comparable with
 * every morning after it.
 */
export const CHECKIN_ITEMS = [
  ["sleep_quality", "sleep quality", "slept badly", "slept well"],
  ["motivation", "motivation", "dreading it", "keen"],
  ["fatigue", "fatigue", "fresh", "wiped out"],
  ["soreness", "soreness", "loose", "wrecked"],
  ["stress", "stress", "calm", "frazzled"],
];

/**
 * The three shown upside down from how the table stores them.
 *
 * Every question on screen counts *up* toward the good answer, so 5 is always
 * the one you want — but "5 fatigue" in the table means wiped out. Membership
 * here is the entire difference between the two directions. Adding a question
 * whose words count up into something bad means adding its key here and
 * nothing else.
 */
export const INVERTED = new Set(["fatigue", "soreness", "stress"]);

/** What a tap on `n` means to the database. */
export const toStored = (key, shown) => (INVERTED.has(key) ? 6 - shown : shown);

/** What a stored value means on screen. Its own inverse — 6-(6-n) is n. */
export const toShown = (key, stored) => (INVERTED.has(key) ? 6 - stored : stored);

// --- a training session -----------------------------------------------------

/** `kind: key`. Alphabetical collision resolved once, here: sparring is `s`,
 *  so strength — the one you reach for less often — is `t`. */
export const SESSION_KINDS = {
  boxing: "b",
  sparring: "s",
  roadwork: "r",
  strength: "t",
  conditioning: "c",
  other: "o",
};

/** The anchors that stop RPE 7 drifting into RPE 5 over a long camp. */
export const RPE_ANCHOR = {
  1: "nothing", 2: "very easy", 3: "easy", 4: "comfortable", 5: "moderate",
  6: "working", 7: "hard", 8: "very hard", 9: "near maximal", 10: "everything",
};

// --- what the plan is still owed --------------------------------------------
//
// One definition of "already answered", and the server now shares it: `/m`
// asks this to decide which blocks to offer, and `state.health_panel` computes
// the load zone's prescribed-against-logged off the same rule.
//
// It was written when there were two clients that could each answer a block,
// and the bug it fixed is worth keeping on the record even though one of those
// clients is gone. The phone posted sessions with no plan key on them, so a
// lift logged in the gym left the laptop's card open all evening asking for a
// lift that had already happened — a wrong answer which by construction only
// ever appeared on the screen you did *not* log from, so nothing about logging
// it looked amiss at the time.

/** The plan keys a set of sessions has answered. Freehand rows carry none and
 *  answer nothing, which is the point: a session logged off-plan must not close
 *  a block, because pairing the two up by kind would be the load model
 *  guessing. */
export const answeredKeys = (sessions) =>
  new Set((sessions ?? []).map((s) => s.plan_key).filter(Boolean));

/** Today's prescribed rows that nothing has answered yet. `plan` is the panel's
 *  `TrainingPlan`, or null when there is no camp on — which is silence rather
 *  than an error, and reads here as a day that asks for nothing. */
export const openBlocks = (plan, sessions) => {
  const answered = answeredKeys(sessions);
  return (plan?.today ?? []).filter((block) => !answered.has(block.key));
};

// --- reading the tables above -----------------------------------------------
//
// Two pure functions, and they live here for the same reason the tables do:
// this is the file that imports nothing, so the phone can have them without
// dragging the dashboard in behind them.
//
// They were in the desktop's `quicklog.js` until the phone needed them. That
// was a fine home while the desktop was the only reader, and a bad one the
// moment the phone imported them: that file pulled in the store, the nav ring,
// the capture stack and the HTTP client, so two lines of branching logic
// dragged five desktop modules into the phone's module graph — and, worse,
// into the graph the service worker has to have cached for the app to open
// with no server. Five files that were never in `SHELL`, so the offline app
// was one cache eviction away from a blank screen.
//
// `quicklog.js` has since been deleted with the rest of the laptop's logging
// cards, which is the tidiest available end to that story and no reason at all
// to move these back. The property that mattered was never where they came
// from — it is that this file has no client in it.

/**
 * Which steps this entry actually asks, given what has been answered so far.
 *
 * The whole of the branching, and pure so it can be tested without a DOM —
 * which matters more than it looks. Getting this wrong in the direction of
 * *too many* steps means a urine entry stopping to ask for a solidity, and the
 * table would reject the row after four keystrokes had already been spent on
 * it; getting it wrong the other way means committing an entry with a field
 * missing, which is the same rejection with less warning.
 */
export const applicableSteps = (steps, draft) =>
  steps.filter((step) => !step.when || step.when(draft));

/**
 * What a `field` step's box will accept, as a number, or null.
 *
 * Three spellings, because all three are what somebody standing at a sink
 * actually types: `1.5`, `.5`, and `1 1/2`. The fraction is not decoration —
 * the unit here is a twelve-ounce drink and half of one is the commonest
 * answer there is, and a box that took `1.5` but silently rejected `1/2` would
 * be a box you learn to distrust rather than one you learn the grammar of.
 *
 * Null for anything else, including the empty string and `0`. The caller shows
 * the box's own hint rather than a validation message: there is one rule, it
 * is printed beside the box, and a red sentence explaining it a second time is
 * a sentence that appears when you are already looking at the answer.
 *
 * `max` is the fat-finger guard the table and the request model both carry. It
 * is checked here as well so a mistyped `15` — which is a legal number, just
 * not a legal answer — is refused by the box that took it rather than by a
 * toast a round trip later.
 */
export function parseAmount(text, max = 20) {
  const said = String(text ?? "").trim();
  if (!said) return null;
  const mixed = said.match(/^(?:(\d+)\s+)?(\d+)\s*\/\s*(\d+)$/);
  const value = mixed
    ? Number(mixed[1] ?? 0) + Number(mixed[2]) / Number(mixed[3])
    : Number(said);
  if (!Number.isFinite(value) || value <= 0 || value > max) return null;
  // Two places is past the precision anyone has about a bottle, and it is what
  // stops a third of a drink arriving as 0.3333333333333333 in a table that
  // will be summed for the rest of the year.
  return Math.round(value * 100) / 100;
}
