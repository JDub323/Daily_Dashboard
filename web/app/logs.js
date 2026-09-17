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

/**
 * What was in it, as a set. The meal log's answer to `DRINK_TAGS`, and read
 * the same way: none of them is a real answer, so an unflagged meal is a meal
 * that was none of these things rather than one that was not asked.
 *
 * Four, and the shortness is the design rather than a first draft. `size`
 * already carries how much and `quality` carries how processed; these are the
 * things neither of those can see, and a fifth would be one answered out of
 * habit rather than by looking at the plate.
 *
 * `snack` is the only one anything reads today, and what reads it is the
 * required-tasks count — three logged apples must not close out "eat three
 * meals". It is a flag rather than an inference from `size` because a small
 * meal during a cut is still a meal, and any rule keyed on size would throw
 * that away too.
 */
export const MEAL_FLAGS = [
  ["p", "protein", "protein"],
  ["f", "fried", "fried"],
  ["n", "snack", "snack"],
  ["y", "salty", "salty"],
];

/**
 * The meals that are the same meal every time.
 *
 * `[value, label, fills]` — no key, and one element longer than every other
 * row in this file. The third element is the whole point: a preset is a
 * *shortcut through the questions below it*, not a fifth question. Tapping one
 * fills size, quality and flags, all of which stay editable afterwards, so it
 * is the fast path rather than a different way of answering.
 *
 * What it costs is that quality becomes a decision taken once rather than a
 * judgement made per meal — and that is the improvement, not the price. A
 * hand-scored 4-or-5 drifts over a camp; the same plate is now the same number
 * every time it is eaten, and a series where it is not is a series where the
 * plate actually changed.
 *
 * The four small ones carry `snack`, which is what keeps an apple from
 * counting as one of the day's three meals. That mapping lives here rather
 * than in the flag row because it is a fact about the *food* — an apple is a
 * snack whoever logs it — and the flag row stays free to disagree on the day
 * you eat four of them for lunch.
 *
 * There is no key row here and no keyboard to use one: this is the list most
 * likely to be edited, and eight arbitrary letters would be eight more things
 * to keep unique for a surface that has no keys. Presets are tapped.
 */
export const MEAL_PRESETS = [
  ["chicken_broccoli_sweet_potato", "chicken · broccoli · sweet potato",
    { size: "large", quality: 5, flags: ["protein"] }],
  ["chicken_rice", "chicken + rice",
    { size: "large", quality: 5, flags: ["protein"] }],
  ["scrambled_eggs", "loaded scrambled eggs",
    { size: "medium", quality: 4, flags: ["protein"] }],
  ["eating_out", "eating out",
    { size: "large", quality: 1, flags: [] }],
  ["trail_mix", "trail mix",
    { size: "small", quality: 3, flags: ["protein", "snack", "salty"] }],
  ["protein_shake", "protein shake",
    { size: "small", quality: 4, flags: ["protein", "snack"] }],
  ["banana", "banana", { size: "small", quality: 5, flags: ["snack"] }],
  ["apple", "apple", { size: "small", quality: 5, flags: ["snack"] }],
];

/** What a preset fills in, or null for a value that is not one. Kept as a
 *  lookup rather than a `find` at every call site so that a preset the client
 *  does not recognise — a row written before the list was edited — reads as
 *  "no fills" instead of throwing on a property of undefined. */
export const mealFills = (preset) =>
  MEAL_PRESETS.find(([value]) => value === preset)?.[2] ?? null;

/**
 * Four questions now, and only two of them have to be answered.
 *
 * `optional` is what makes the two new rows additive rather than a tax on the
 * meal that is none of them: the commit button has always been held hostage to
 * the questions with no default answer, and a preset row that did that would
 * make "some lunch, medium, 3" — the ordinary case — the one entry you cannot
 * file without inventing a category for it.
 *
 * `fills` on the preset step is honoured by the client, which writes the whole
 * object into the draft rather than the value alone. It is expressed here
 * rather than in the client for the reason everything else in this file is:
 * the next surface that logs a meal should inherit what a preset means instead
 * of a copy of it.
 */
export const MEAL_STEPS = [
  {
    name: "preset",
    hint: "optional — fills the rest in",
    optional: true,
    fills: mealFills,
    // Drawn behind a button rather than as eight caps in the open. This is a
    // hint to the client about *space*, not a change to what is asked: the
    // question, its answers and the fills are all unchanged, and a surface
    // with room for eight caps is free to ignore it.
    //
    // It earns the flag because the row is the wrong shape for the screen it
    // is on. Eight presets is three rows of a three-across grid — about a
    // third of a phone panel — sitting above the three questions every meal
    // has to answer, spent on a shortcut taken twice a day. Collapsed, the
    // row is one control that either offers the shortcut or names the one you
    // took.
    sheet: true,
    options: MEAL_PRESETS.map(([value, label]) => [null, value, label]),
  },
  { name: "size", hint: "roughly, in calories", options: MEAL_SIZE },
  { name: "quality", hint: "1 processed · 5 whole food", options: MEAL_QUALITY },
  { name: "flags", hint: "none is an answer", multi: true, options: MEAL_FLAGS },
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
//   **A drink is a set of tags, and none of them is a real answer.** Every
//   drink here is water with something in it, so `water` was a tag that would
//   have been true of every row and told you nothing — an untagged row *is*
//   the glass of water. `gatorade` is gone for the neighbouring reason: it was
//   `electrolyte` and `sugar` together and nothing else, so now that a row can
//   be both, the word would only be a second spelling free to disagree with
//   the first. Two hundred calories of sugar is either a mid-session necessity
//   or a soft drink, and `sugar` is the tag that keeps them countable.
//
//   **`caffeine` counts as fluid.** The diuretic effect is real and smaller
//   than the water it arrives in. It is a tag because the caffeine is worth
//   counting, not because the water in it is suspect. It was `coffee` until
//   `carbonated` arrived beside it: a tag naming a *drink* where every other
//   one names a substance had nowhere to put an energy drink, which is
//   caffeine and is not coffee. `coffee` is a name now, one level down.
//
//   **`carbonated` is recorded and not yet scored.** Nothing reads it. It is
//   here because it costs nothing to tap and cannot be recovered afterwards,
//   and because it is the entire difference between a beer and a shot and
//   between a soda and a juice — three names that would otherwise each have
//   had to become a tag.
//
//   **`alcohol` counts as no fluid at all, and changes the unit.** There a
//   drink is a *standard* drink — a shot, a beer and a glass of wine are each
//   1 — because the point is the alcohol rather than the volume, and twelve
//   ounces of spirits is not a quantity anybody means.

/**
 * `[key, value, label]`, in the order they are reached for.
 *
 * Every key is the first letter but one. `caffeine` and `carbonated` both want
 * `c`; it goes to caffeine, which inherits it from `coffee` and is the one
 * reached for daily, and carbonated takes `z` for the fizz. That is the only
 * collision left — `w` is not needed because the answer for water is *no
 * answer*, and `g` was never available, being half of `gg`, "first panel of
 * this section", one of the keys `panel.js` will not let a card claim.
 */
export const DRINK_TAGS = [
  ["m", "milk", "milk"],
  ["s", "sugar", "sugar"],
  ["e", "electrolyte", "electrolyte"],
  ["c", "caffeine", "caffeine"],
  ["a", "alcohol", "alcohol"],
  ["z", "carbonated", "carbonated"],
];

/**
 * What the drink *was*, given what was in it.
 *
 * Keyed by the tag set, written as the tags sorted and joined — so a lookup is
 * `DRINK_NAMES.get(canonical.join("+"))` and the order the tags were tapped in
 * cannot change the answer. The empty key is deliberately absent: an untagged
 * drink is a glass of water, and there is nothing to name.
 *
 * **A name is identity; a tag is composition.** Nothing sums a name and
 * nothing may — the tags are the scored half, and `health.py` never looks at
 * this. What it buys is a row that is still legible a year from now, when
 * `sugar + carbonated + caffeine` is a complete description that does not say
 * whether that was a Coke or a Celsius.
 *
 * Three chips is the cap and it is a real rule, not the longest row happening
 * to be three. A fourth is a menu rather than a button, and this row exists
 * *because* the tag row above it does not need to be read carefully — the
 * moment you have to scan it, it has cost more than it saves.
 *
 * A combination absent from this table shows no third row at all, which is
 * itself a signal: you have tagged something unusual, and the tags have
 * already recorded everything that gets counted.
 *
 * Nothing here is required. Not tapping a name is "other", it costs no tap,
 * and it is what most rows will be.
 */
export const DRINK_NAMES = new Map([
  ["carbonated", [["sparkling_water", "sparkling water"], ["diet_soda", "diet soda"]]],

  ["sugar", [["oj", "OJ"], ["lemonade", "lemonade"], ["juice", "juice"]]],
  ["sugar+carbonated", [["soda", "soda"], ["ginger_ale", "ginger ale"]]],

  // Caffeine, and the four things that get added to it. `coffee` is a name
  // here and was a tag until migration 33 — see `DrinkTag`, which explains why
  // a tag naming a drink rather than a substance had nowhere to put a Celsius.
  ["caffeine", [["coffee", "coffee"]]],
  ["milk+caffeine", [["latte", "latte"]]],
  ["sugar+caffeine", [["sweet_coffee", "sweet coffee"]]],
  ["milk+sugar+caffeine", [["mocha", "mocha"], ["frappe", "frappe"]]],
  ["caffeine+carbonated", [["diet_energy_drink", "diet energy drink"], ["zevia", "Zevia"]]],
  ["sugar+caffeine+carbonated", [["energy_drink", "energy drink"], ["cola", "cola"]]],

  ["milk", [["lowfat_milk", "lowfat"], ["whole_milk", "whole"], ["protein_shake", "protein shake"]]],
  ["milk+sugar", [["chocolate_milk", "chocolate milk"]]],

  ["electrolyte", [["electrolyte_tab", "electrolyte tab"]]],
  ["sugar+electrolyte", [["gatorade", "gatorade"]]],

  // Alcohol, where `carbonated` is doing the most work of any tag on the row
  // above: it is the whole difference between a beer and a shot, and it was
  // unrecordable before it existed.
  ["alcohol", [["liquor", "liquor"], ["dry_wine", "dry wine"]]],
  ["alcohol+carbonated", [["beer", "beer"], ["hard_seltzer", "hard seltzer"]]],
  ["sugar+alcohol", [["cocktail", "cocktail"], ["sweet_wine", "sweet wine"]]],
  ["sugar+alcohol+carbonated", [["rum_and_coke", "rum & coke"], ["cider", "cider"]]],
]);

/**
 * The names offered for a set of tags, as `[key, value, label]` triples.
 *
 * Sorted into `DRINK_TAGS` order before the lookup rather than trusting the
 * caller, because the tags arrive in tap order from the phone and in whatever
 * order a script felt like everywhere else — and a table keyed by a string is
 * a table that will silently miss on `carbonated+sugar`.
 *
 * Empty for anything unlisted, which is how the step below hides itself.
 */
export const drinkNames = (tags) => {
  const order = DRINK_TAGS.map(([, value]) => value);
  const key = [...new Set(tags ?? [])]
    .filter((tag) => order.includes(tag))
    .sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .join("+");
  return (DRINK_NAMES.get(key) ?? []).map(([value, label]) => [null, value, label]);
};

/**
 * Three questions: what was in it, which one of those it was, and how many.
 *
 * `multi` is the difference from every other step in this file, and it is the
 * whole point of the change that introduced it: the answers are not exclusive,
 * because coffee with sugar in it is both and a sports drink is salt and sugar
 * both. Anything reading these steps has to treat a `multi` step as answered
 * from the start — its answer is an array, and the empty array is the
 * commonest drink of the day rather than a question left blank.
 *
 * The name step is the first in this file whose `options` is a *function* of
 * what has been answered so far. `when` could not do this on its own: it hides
 * a step whose question is wrong, and here the question is always right and it
 * is the answers that change. The two work together — the step is hidden
 * exactly when it would have no answers to offer, which is the same test.
 *
 * The box is preset to `1`, so the common answer — one can, one mug, one glass
 * of water — is the amount alone, and typing happens only when there is
 * actually something to say.
 */
export const DRINK_STEPS = [
  // No hint. It said "none = water", which was true and was being read every
  // time the log was opened — the rule it states is the one thing about this
  // screen you learn on the first drink and never need again, and a caption
  // that is only useful once is a caption you stop seeing.
  { name: "tags", multi: true, options: DRINK_TAGS },
  {
    name: "name",
    hint: "optional",
    optional: true,
    options: (draft) => drinkNames(draft.tags),
    when: (draft) => drinkNames(draft.tags).length > 0,
  },
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
 *  so strength — the one you reach for less often — is `t`, and sport, which
 *  arrived last and wants `s` just as badly, takes `p`. */
export const SESSION_KINDS = {
  boxing: "b",
  sparring: "s",
  roadwork: "r",
  strength: "t",
  conditioning: "c",
  sport: "p",
  other: "o",
};

/**
 * What a session was, as a tree that unfolds one level at a time.
 *
 * `{ value, label, kind?, children? }`. Depth varies by branch because the
 * branches vary: sparring is two answers and does not need three levels,
 * strength is a named programme and does.
 *
 * **The node carries the kind, and that is the whole trick.** The top row is
 * six words in the language the gym uses; `SessionKind` is seven words the
 * load model uses; they are not the same six and they were never going to be.
 * `cardio` is not a kind and never becomes one — it resolves, one level down,
 * into `conditioning` if the work had intervals in it and `roadwork` if it did
 * not, which is the distinction `daily_load` has always drawn and the last one
 * anybody wants to be asked for in those words at 6am.
 *
 * So `roadwork` now means steady-state aerobic work rather than specifically
 * running, which is what the word means in a boxing gym anyway. An elliptical
 * session stores as roadwork and its leaf says it was an elliptical.
 *
 * **Nothing below the top is required.** Stopping early is a real answer: it
 * stores the kind and no focus, which reads as "some session of that sort".
 * That is why there is no `other` button on any leaf row — it would be a tap
 * that means exactly what not tapping already means, on every row, forever.
 * The one exception is `cardio`, which has no kind of its own, so its second
 * level has to be answered before there is anything to log; `sessionKind`
 * below returns null until it is, and the commit button reads that.
 *
 * The dotted path a walk down this tree produces is `TrainingSession.focus`,
 * and every path that reaches a leaf appears in `SESSION_FOCUS` on the Python
 * side. `tests/test_logs_contract.py` is what stops the two drifting.
 */
export const SESSION_TREE = [
  {
    value: "boxing",
    label: "boxing",
    kind: "boxing",
    children: [
      { value: "shadowboxing", label: "shadowboxing" },
      { value: "bag", label: "heavy bag" },
      { value: "mitts", label: "mitts" },
      { value: "drills", label: "drills" },
      { value: "class", label: "class" },
    ],
  },
  {
    value: "sparring",
    label: "sparring",
    kind: "sparring",
    // Two, because the recovery cost of the two is genuinely different and
    // nothing finer would be answered honestly. `SessionKind.sparring` exists
    // to answer "how much of it did this camp contain"; this answers "how much
    // of that was real".
    children: [
      { value: "full", label: "full" },
      { value: "touch", label: "touch" },
    ],
  },
  {
    value: "cardio",
    label: "cardio",
    // No `kind`. The one umbrella that is genuinely two kinds, which is why it
    // is the one whose second level must be answered.
    children: [
      {
        value: "hiit",
        label: "HIIT",
        kind: "conditioning",
        children: [
          { value: "core", label: "core" },
          { value: "lower", label: "lower" },
          { value: "upper", label: "upper" },
          { value: "full", label: "full" },
          { value: "interval_run", label: "interval run" },
        ],
      },
      {
        value: "steady",
        label: "steady",
        kind: "roadwork",
        children: [
          { value: "incline", label: "incline treadmill" },
          { value: "run", label: "run" },
          { value: "stairmaster", label: "stairmaster" },
          { value: "elliptical", label: "elliptical" },
        ],
      },
    ],
  },
  {
    value: "strength",
    label: "strength",
    kind: "strength",
    children: [
      {
        // The named programme. Split from `general` rather than flattened
        // because these five are nearly always prefilled by the plan — they
        // are prescribed — so the split costs one tap on the lifts that are
        // logged by hand and saves scanning nine long labels on the ones that
        // are not.
        value: "vjp",
        label: "VJP",
        children: [
          { value: "lower_french_contrast", label: "lower french contrast" },
          { value: "upper_french_contrast", label: "upper french contrast" },
          { value: "lower_concentrics", label: "lower concentrics" },
          { value: "upper_concentrics", label: "upper concentrics" },
          { value: "potentiation", label: "potentiation" },
        ],
      },
      {
        value: "general",
        label: "general",
        children: [
          { value: "1x20", label: "1x20" },
          { value: "upper", label: "upper" },
          { value: "lower", label: "lower" },
          { value: "full", label: "full" },
        ],
      },
    ],
  },
  {
    // A game, and the branch nothing on the plan can reach. `library.A` and
    // the rest of `training.toml` resolve into boxing, cardio and strength;
    // this one is only ever picked freehand, which is what it is *for* — it
    // is the load that happened because somebody had a ball, and the whole
    // reason it stopped being `other` is that an afternoon of it and a
    // twelve-minute neck circuit were the same row to the load model.
    //
    // Indoor volleyball and the sand game are two leaves rather than one
    // because they are two different afternoons: sand is roughly twice the
    // work for the same scoreline. `throw` is playing catch — it is here
    // rather than under `other.mobility` because it is a thing you do for an
    // hour with somebody else, not a warm-up.
    value: "sport",
    label: "sport",
    kind: "sport",
    children: [
      { value: "volleyball", label: "volleyball" },
      { value: "sand_volleyball", label: "sand volleyball" },
      { value: "basketball", label: "basketball" },
      { value: "football", label: "football" },
      { value: "throw", label: "throw" },
    ],
  },
  {
    // `other` is a kind, not an absence: the neck circuit and the core circuit
    // are prescribed work, and folding twelve minutes of neck into `strength`
    // would quietly inflate the lifting volume the load model reads. That
    // argument is `library.N`'s and it is why this branch exists at all.
    value: "other",
    label: "other",
    kind: "other",
    children: [
      { value: "neck", label: "neck" },
      { value: "core", label: "core" },
      { value: "mobility", label: "mobility" },
      { value: "walk", label: "walk" },
      { value: "rehab", label: "rehab" },
    ],
  },
];

/** The nodes along `picked`, stopping at the first value that is not a child
 *  of the one before it. Returns fewer nodes than `picked` has values when the
 *  path has gone stale — which happens whenever the tree is edited under a
 *  half-filled form, and is why every reader below walks rather than indexes. */
const walk = (picked, tree = SESSION_TREE) => {
  const nodes = [];
  let level = tree;
  for (const value of picked ?? []) {
    const node = level?.find((n) => n.value === value);
    if (!node) break;
    nodes.push(node);
    level = node.children;
  }
  return nodes;
};

/**
 * The rows to draw for a path, as `{ depth, options, chosen }` — one per level
 * that has anything to ask.
 *
 * The last row is the children of the deepest node picked, so answering grows
 * the form by one row and un-answering shrinks it. A leaf has no children and
 * adds no row, which is how the form knows it is finished without being told.
 *
 * `depth` is on the row rather than left to the caller's index, and that is
 * not tidiness. The caller filters these — a level offering one option is not
 * a question — so after the filter the array index is no longer the level, and
 * the client uses `depth` to truncate the path when a row is re-answered.
 * Reading the index there instead was a bug: the truncation silently became a
 * no-op, so answering the first row again appended to a path it should have
 * replaced, and the entry logged the kind you had picked first.
 */
export const focusLevels = (picked, tree = SESSION_TREE) => {
  const nodes = walk(picked, tree);
  const rows = [{ depth: 0, options: tree, chosen: nodes[0]?.value ?? null }];
  for (const [depth, node] of nodes.entries()) {
    if (!node.children?.length) break;
    rows.push({
      depth: depth + 1,
      options: node.children,
      chosen: nodes[depth + 1]?.value ?? null,
    });
  }
  return rows;
};

/**
 * The `SessionKind` a path lands in, or null when it has not reached one yet.
 *
 * The deepest kind along the path wins, which is what lets `cardio` carry none
 * and its two children carry one each. Null is the commit button's cue to stay
 * disabled: every other branch answers this at the first tap, and cardio is
 * the only one that makes you say which sort.
 */
export const sessionKind = (picked, tree = SESSION_TREE) =>
  walk(picked, tree).reduce((kind, node) => node.kind ?? kind, null);

/**
 * The dotted path to store, or null.
 *
 * Null unless the walk reached a leaf, and that is the rule the whole "no
 * `other` button" decision rests on: `cardio.steady` with nothing under it is
 * not a focus, it is a kind of roadwork with no focus, and storing the
 * half-path would invent a category that means "they stopped here" rather than
 * anything about the session. `SESSION_FOCUS` on the Python side holds leaves
 * only, and `SessionRequest` refuses anything that is not in it.
 */
export const sessionFocus = (picked, tree = SESSION_TREE) => {
  const nodes = walk(picked, tree);
  if (!nodes.length || nodes[nodes.length - 1].children?.length) return null;
  return nodes.map((node) => node.value).join(".");
};

/**
 * The values a pruned tree leaves no choice about, from the top down.
 *
 * A plan block that names a kind prunes the tree to one branch, and a row with
 * one button in it is not a question — it is a tap you have to make before you
 * are allowed to answer the real one. This walks the forced part so the path
 * can start below it, and stops at the first level that genuinely asks
 * something.
 *
 * Empty for the freehand tree, which offers five at the top and forces
 * nothing. That is the ordinary case and this costs it one comparison.
 */
export const forcedPath = (tree) => {
  const path = [];
  let level = tree;
  while (level?.length === 1) {
    path.push(level[0].value);
    level = level[0].children;
  }
  return path;
};

/**
 * The tree with every branch that cannot reach one of `kinds` pruned away.
 *
 * What the plan's `choose` shortlist becomes. `library.A` offers roadwork,
 * boxing or conditioning — three `SessionKind` values — and the honest way to
 * ask that on a screen built around the tree is to show the tree with the
 * unreachable branches gone, rather than a second row of raw kind names beside
 * it saying the same thing in the schema's words.
 *
 * A branch survives if any leaf under it lands in an allowed kind, so `cardio`
 * survives a shortlist of `roadwork` with only its `steady` half attached.
 */
export const prunedTree = (kinds, tree = SESSION_TREE) => {
  if (!kinds?.length) return tree;
  const allowed = new Set(kinds);
  const prune = (nodes, inherited) =>
    nodes
      .map((node) => {
        const kind = node.kind ?? inherited;
        const children = node.children ? prune(node.children, kind) : null;
        if (children?.length) return { ...node, children };
        if (node.children?.length) return null;
        return kind && allowed.has(kind) ? { ...node, children: null } : null;
      })
      .filter(Boolean);
  return prune(tree, null);
};

/** The anchors that stop RPE 7 drifting into RPE 5 over a long camp. */
export const RPE_ANCHOR = {
  1: "nothing", 2: "very easy", 3: "easy", 4: "comfortable", 5: "moderate",
  6: "working", 7: "hard", 8: "very hard", 9: "near maximal", 10: "everything",
};

/**
 * The same job for session quality, and the words are chosen to keep it from
 * collapsing back into RPE.
 *
 * That collapse is the entire risk in the field. Asked "how was it", the
 * honest-sounding answer after a brutal session is a low one — and that number
 * is already recorded, in the column next door, more carefully. So none of
 * these five say anything about effort. They are about *sharpness*: whether
 * the body did what it was told, which is the thing a block of training is
 * supposed to move and the thing an rpe 7 is blind to.
 *
 * Five rather than ten, matching the check-in and not the RPE row beside it.
 * Ten points of resolution on a feeling is precision nobody has.
 */
export const QUALITY_ANCHOR = {
  1: "nothing there", 2: "flat", 3: "ordinary", 4: "sharp", 5: "everything worked",
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
 * Today's meals, not counting snacks.
 *
 * The day requires three *meals*, and three logged apples must not close that
 * out. The rule is here rather than at either of the two call sites because
 * there are two of them — the chip that counts and the nudge that chases —
 * and a day that is complete on one and open on the other is the worst
 * available outcome for a required task.
 *
 * `health._meal_rows` is the same rule on the server, where it gates the
 * fuel score's "was the log even kept" input for the same reason.
 *
 * Note what is *not* here: snacks still count as food everywhere the question
 * is intake rather than completeness. The energy balance sums them and diet
 * quality averages them.
 */
export const squareMeals = (meals) => (meals ?? []).filter((meal) => !meal.is_snack);

// --- printing a row back ----------------------------------------------------
//
// Three lookups, and they are here for the reason the tables are: a stored
// value is a slug (`chicken_broccoli_sweet_potato`) and the words for it live
// beside the slug, once. A client that spelled them itself would be a second
// copy free to disagree, and the disagreement would only ever be visible in
// the log you were reading rather than the one you were writing.
//
// All three fall back to the stored value rather than to a blank. A row
// written before a table was edited still has to print as *something*, and the
// slug is a worse label than the word but a much better one than nothing.

/** The words for a meal preset. */
export const mealPresetLabel = (value) =>
  MEAL_PRESETS.find(([slug]) => slug === value)?.[1] ?? value ?? null;

/** The words for a drink's name. Searched across the whole table rather than
 *  looked up under the row's tags, because a row read back has a name and the
 *  tags it was written with, and those two can only disagree if the table has
 *  been edited — in which case the name is still the thing to print. */
export const drinkNameLabel = (value) => {
  for (const names of DRINK_NAMES.values()) {
    const found = names.find(([slug]) => slug === value);
    if (found) return found[1];
  }
  return value ?? null;
};

/** The words for a session focus — the leaf's label, not the whole path.
 *  `cardio.steady.stairmaster` prints as "stairmaster", because the row it
 *  sits on already says `roadwork` in the column to its left. */
export const focusLabel = (path) => {
  let level = SESSION_TREE;
  let node = null;
  for (const value of String(path ?? "").split(".")) {
    node = level?.find((n) => n.value === value) ?? null;
    if (!node) return path ?? null;
    level = node.children;
  }
  return node?.label ?? path ?? null;
};

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
 * A step's options, which may depend on what has already been answered.
 *
 * One function so that no client has to know which steps are which. Every
 * table in this file is a plain array except the drink log's name row, whose
 * answers are a function of the tags above it — and a client that reached for
 * `step.options` directly would render that row as `undefined` rather than
 * failing, which is the sort of bug you find in a kitchen at 7am.
 */
export const stepOptions = (step, draft) =>
  typeof step.options === "function" ? step.options(draft ?? {}) : (step.options ?? []);

/**
 * The draft with every answer that is no longer on offer taken out of it.
 *
 * Two ways an answer goes stale, and until the drink log grew a name row only
 * the first existed:
 *
 *   The step stops being asked. Picking `urine` after answering a solidity —
 *   `when` hides the question, and the answer has to go with it or the table
 *   rejects the row after the answers have already been given.
 *
 *   The step keeps being asked and stops offering that answer. This is the new
 *   one and it is worse, because nothing on screen looks wrong: name a beer,
 *   then add `sugar` to the tags, and the name row quietly becomes rum & coke
 *   and cider with neither of them lit — while `beer` is still in the draft
 *   and still what the write would carry. A silent disagreement between what
 *   the screen shows and what the button posts, which is the exact shape of
 *   the bug that cost a day of meals.
 *
 * Pure, and here rather than in the client, so it can be tested without a DOM
 * and so the next surface inherits the rule instead of the bug.
 *
 * A `multi` answer is filtered rather than dropped: its options do not depend
 * on anything today, and taking the whole set away over one bad member would
 * lose four good taps to a table edit.
 */
export const prunedDraft = (steps, draft) => {
  const asked = applicableSteps(steps, draft);
  const live = new Set(asked.map((step) => step.name));
  const out = {};
  for (const [name, held] of Object.entries(draft)) {
    if (!live.has(name)) continue;
    const step = asked.find((one) => one.name === name);
    if (!step.options) {
      out[name] = held;
      continue;
    }
    const offered = new Set(stepOptions(step, draft).map(([, value]) => value));
    if (step.multi) out[name] = (held ?? []).filter((value) => offered.has(value));
    else if (held == null || offered.has(held)) out[name] = held;
  }
  return out;
};

/**
 * The steps still holding up the commit button.
 *
 * Three kinds of step are never in it. A `multi` step is answered from the
 * moment it is drawn, because its answer is a set and the empty set means
 * something — an untagged drink is a glass of water, an unflagged meal is a
 * meal. An `optional` step is the same claim made for a single-valued answer:
 * declining to name a drink or pick a preset is what most rows do. And a step
 * hidden by `when` is not being asked at all.
 *
 * Here rather than in the client because the empty-set rule was already
 * written down twice — once in each of two clients — the last time this was
 * asked, and one of the two was wrong about drinks for a week.
 */
export const missingSteps = (steps, draft) =>
  applicableSteps(steps, draft).filter(
    (step) => !step.multi && !step.optional && draft[step.name] == null,
  );

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
