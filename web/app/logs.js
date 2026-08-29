// What the five logs ask. Nothing here knows how any of it is drawn.
//
// These tables used to live one per panel, beside the code that rendered them,
// which was the right place for them while there was one thing rendering them.
// There are now two: the desktop cards under `panels/`, which are a keyboard
// ladder, and the phone at `/m`, which is a grid of thumb-sized buttons. Those
// two disagree about everything *except* what the questions are and what the
// legal answers to them are — which is exactly the part that must never
// disagree, because it is the part the database has an opinion about.
//
// So a question lives here once. Adding a step, a kind, or a rung to a scale is
// one edit, and both clients grow it. A step added on one side only is a column
// the other side never fills, which the table then rejects — after the answers
// have already been given, which is the worst moment to find out.
//
// The keys stay even though the phone has no keyboard. They are still the
// desktop's whole interaction, they cost one character each, and a `[key, value,
// label]` triple that sometimes has a key and sometimes does not is a shape
// every reader has to check.
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

/**
 * `[key, value, label]`, in the order they are reached for.
 *
 * Four of the five keys are the first letter. Gatorade is `s`, for *sports
 * drink*, and the reason is the same one that made the carousel's in-and-out
 * tab `p`: `g` is spent. It is half of `gg`, which is "first panel of this
 * section" and one of the keys `panel.js` will not let a card claim — so a
 * `g` bound here would answer while the ladder was up and do nothing at all
 * at a card sitting idle, which is worse than a letter you have to learn once.
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
 * `when` is a predicate over the answers so far. Both clients honour it — see
 * `quicklog.applicableSteps`, which is the desktop's reading of it and is
 * exported so the phone reads the same rule rather than a second copy of it.
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
