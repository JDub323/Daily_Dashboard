// The three trees, and the rules that hold them together.
//
// `logsteps.test.js` next door tests the *ladder* — which questions get asked
// and in what order. This file tests the level below that: what the answers to
// one question do to the answers offered by the next.
//
// Everything below shares a failure mode, which is why it is worth a separate
// file. None of it throws. A tag combination that misses its name row draws
// a perfectly good screen with one row fewer; a preset that fills the wrong
// quality writes a plausible number; a path that keeps a stale leaf posts a
// body the server accepts. You would find each of them by noticing, weeks
// later, that a series looks wrong — and by then the rows are written and
// there is nothing to recompute them from.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applicableSteps,
  BATHROOM_STEPS as BATHROOM,
  DRINK_STEPS as DRINKS,
  drinkNames,
  focusLabel,
  focusLevels,
  mealFills,
  MEAL_STEPS as MEALS,
  missingSteps,
  prunedDraft,
  prunedTree,
  sessionFocus,
  sessionKind,
  SESSION_TREE,
  squareMeals,
  stepOptions,
} from "../../web/app/logs.js";

const values = (options) => options.map(([, value]) => value);

describe("the drink name row", () => {
  it("is not offered at all for a glass of water", () => {
    // The whole reason the step has a `when`. An untagged drink is water and
    // there is nothing to name — a row of no buttons would be a question
    // asked of every plain glass, which is most of them.
    const asked = applicableSteps(DRINKS, { tags: [] });
    assert.deepEqual(asked.map((s) => s.name), ["tags", "amount"]);
  });

  it("appears once the tags make a name possible", () => {
    const asked = applicableSteps(DRINKS, { tags: ["alcohol", "carbonated"] });
    assert.deepEqual(asked.map((s) => s.name), ["tags", "name", "amount"]);
    assert.deepEqual(values(stepOptions(asked[1], { tags: ["alcohol", "carbonated"] })), [
      "beer",
      "hard_seltzer",
    ]);
  });

  it("does not care what order the tags were tapped in", () => {
    // The bug this is here for: the table is keyed on a joined string, and the
    // phone hands over tags in tap order. Without the sort inside `drinkNames`
    // a beer tagged carbonated-then-alcohol would silently offer nothing, and
    // "nothing offered" is a legitimate state, so it would look like a
    // combination nobody had written a row for.
    assert.deepEqual(
      values(drinkNames(["carbonated", "alcohol"])),
      values(drinkNames(["alcohol", "carbonated"])),
    );
  });

  it("offers nothing for a combination nobody drinks", () => {
    assert.deepEqual(drinkNames(["milk", "alcohol", "electrolyte"]), []);
  });

  it("never holds up the commit button", () => {
    // `optional`. Not naming a drink is what most rows do, and a name row that
    // gated the write would make the fastest entry on the screen — tap a tag,
    // press log — impossible to finish without inventing a category.
    assert.deepEqual(
      missingSteps(DRINKS, { tags: ["alcohol", "carbonated"], amount: 1 }).map((s) => s.name),
      [],
    );
  });
});

describe("meal presets", () => {
  it("fill the two questions and the flags below them", () => {
    assert.deepEqual(mealFills("chicken_broccoli_sweet_potato"), {
      size: "large",
      quality: 5,
      flags: ["protein"],
    });
  });

  it("mark the small ones as snacks", () => {
    // The mapping the required-meals count turns on. An apple is a snack
    // whoever logs it, so it is a fact about the food and belongs on the
    // preset rather than being left to be remembered on the flag row.
    for (const preset of ["apple", "banana", "trail_mix", "protein_shake"]) {
      assert.ok(mealFills(preset).flags.includes("snack"), preset);
    }
    for (const preset of ["chicken_rice", "scrambled_eggs", "eating_out"]) {
      assert.ok(!mealFills(preset).flags.includes("snack"), preset);
    }
  });

  it("fills nothing for a preset the client does not know", () => {
    // A row written before the table was edited. `null`, not a throw — the
    // whole point of the lookup being a lookup.
    assert.equal(mealFills("sushi"), null);
  });

  it("never holds up the commit button", () => {
    assert.deepEqual(
      missingSteps(MEALS, { size: "medium", quality: 3 }).map((s) => s.name),
      [],
    );
  });

  it("leaves size and quality as the only two required answers", () => {
    assert.deepEqual(missingSteps(MEALS, {}).map((s) => s.name), ["size", "quality"]);
  });
});

describe("counting the day's meals", () => {
  it("drops the snacks", () => {
    const day = [
      { is_snack: false },
      { is_snack: true },
      { is_snack: false },
      { is_snack: true },
    ];
    assert.equal(squareMeals(day).length, 2);
  });

  it("counts a row that has never heard of snacks", () => {
    // Every meal logged before migration 33. `is_snack` is a computed field on
    // the server so this cannot happen through the API — but the chip reads
    // whatever the panel hands it, and a rule that quietly dropped older rows
    // would rewrite the history of a required task.
    assert.equal(squareMeals([{}, {}]).length, 2);
  });
});

describe("an answer that has gone stale", () => {
  // The bug this function was written for, found in the browser: name a beer,
  // then add `sugar` to the tags. The name row keeps being asked and stops
  // offering beer — so the old rule, which only dropped answers to questions
  // that had *disappeared*, left `beer` in the draft with nothing lit on
  // screen. The screen said one thing and the button would have posted
  // another, silently, which is the shape of the bug that cost a day of meals.

  it("drops a drink name the new tags no longer offer", () => {
    assert.deepEqual(
      prunedDraft(DRINKS, { tags: ["alcohol", "carbonated", "sugar"], name: "beer" }),
      { tags: ["alcohol", "carbonated", "sugar"] },
    );
  });

  it("keeps a drink name the tags still offer", () => {
    assert.deepEqual(
      prunedDraft(DRINKS, { tags: ["alcohol", "carbonated"], name: "beer" }),
      { tags: ["alcohol", "carbonated"], name: "beer" },
    );
  });

  it("drops a name once every tag is taken off", () => {
    // The older rule, still doing its job: with no tags the step is not asked
    // at all, so the answer goes with the question.
    assert.deepEqual(prunedDraft(DRINKS, { tags: [], name: "beer" }), { tags: [] });
  });

  it("still drops a solidity when the visit turns out to be urine", () => {
    // The branch rule this replaced, kept passing. Getting it wrong the other
    // way commits a row the table refuses after four taps have been spent.
    assert.deepEqual(
      prunedDraft(BATHROOM, { kind: "urine", solidity: "soft", color: "light" }),
      { kind: "urine", color: "light" },
    );
  });

  it("leaves a meal alone", () => {
    // Every meal row's options are a fixed table, so nothing here can go
    // stale. Worth pinning: this is the log where a wrong prune would throw
    // away a preset's fills and leave a half-answered form.
    const draft = { preset: "apple", size: "small", quality: 5, flags: ["snack"] };
    assert.deepEqual(prunedDraft(MEALS, draft), draft);
  });
});

describe("the session tree", () => {
  it("opens with the five umbrellas and nothing else", () => {
    const rows = focusLevels([]);
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].options.map((n) => n.value), [
      "boxing", "sparring", "cardio", "strength", "other",
    ]);
  });

  it("grows a row per level answered and stops at a leaf", () => {
    assert.equal(focusLevels(["cardio"]).length, 2);
    assert.equal(focusLevels(["cardio", "steady"]).length, 3);
    assert.equal(focusLevels(["cardio", "steady", "run"]).length, 3);
  });

  it("numbers its own rows, so a filtered list still truncates correctly", () => {
    // `depth` is on the row and not the caller's array index, because the
    // client drops the rows that offer one option. With the index, truncating
    // the path on a re-answer silently became a no-op: tapping the first row
    // again appended instead of replacing, and the session logged the kind
    // that had been picked first.
    assert.deepEqual(focusLevels(["cardio", "steady"]).map((r) => r.depth), [0, 1, 2]);
    const forced = focusLevels([], prunedTree(["roadwork"]));
    assert.deepEqual(forced.map((r) => r.depth), [0]);
  });

  it("replaces the levels under a row that is answered again", () => {
    // What `depth` is for. The path is truncated to the row's own level before
    // the tap is appended, so a stale leaf cannot survive under a new branch.
    const repick = (path, depth, value) => [...path.slice(0, depth), value];
    const path = ["cardio", "steady", "run"];
    assert.deepEqual(repick(path, 0, "sparring"), ["sparring"]);
    assert.equal(sessionKind(repick(path, 0, "sparring")), "sparring");
    assert.equal(sessionFocus(repick(path, 0, "sparring")), null);
  });

  it("knows the kind from the first tap on every branch but cardio", () => {
    assert.equal(sessionKind(["boxing"]), "boxing");
    assert.equal(sessionKind(["sparring"]), "sparring");
    assert.equal(sessionKind(["strength"]), "strength");
    assert.equal(sessionKind(["other"]), "other");
    // The one that must be answered twice, and the reason the commit button
    // reads `sessionKind` rather than the length of the path.
    assert.equal(sessionKind(["cardio"]), null);
    assert.equal(sessionKind(["cardio", "hiit"]), "conditioning");
    assert.equal(sessionKind(["cardio", "steady"]), "roadwork");
  });

  it("stores a focus only for a path that reached a leaf", () => {
    // "no bottom level chosen == other". A half-path is a kind with no focus,
    // and storing it would invent a category meaning "they stopped here".
    assert.equal(sessionFocus(["cardio"]), null);
    assert.equal(sessionFocus(["cardio", "steady"]), null);
    assert.equal(sessionFocus(["cardio", "steady", "run"]), "cardio.steady.run");
    assert.equal(sessionFocus(["boxing"]), null);
    assert.equal(sessionFocus(["boxing", "mitts"]), "boxing.mitts");
  });

  it("ignores a path that has gone stale under an edited tree", () => {
    // Every reader walks rather than indexes, so a half-filled form open while
    // the tree changes underneath resolves to the part that still exists
    // instead of throwing or inventing a leaf.
    assert.equal(sessionKind(["cardio", "stairs", "run"]), null);
    assert.equal(sessionFocus(["boxing", "kettlebells"]), null);
  });

  it("offers no `other` on any row", () => {
    // The rule that saves a button on every screen: stopping is what "other"
    // would have meant. `other` at the top level is a SessionKind — the neck
    // and core circuits — and is a different word doing a different job.
    const walk = (nodes, depth) => {
      for (const node of nodes) {
        assert.ok(depth === 0 || node.value !== "other", node.value);
        if (node.children) walk(node.children, depth + 1);
      }
    };
    walk(SESSION_TREE, 0);
  });

  it("prints a leaf by its own words, not the whole path", () => {
    assert.equal(focusLabel("cardio.steady.stairmaster"), "stairmaster");
    assert.equal(focusLabel("strength.vjp.lower_french_contrast"), "lower french contrast");
    // A row from a table that has since been edited still has to print.
    assert.equal(focusLabel("cardio.steady.rowing"), "cardio.steady.rowing");
    assert.equal(focusLabel(null), null);
  });
});

describe("what the plan leaves to choose", () => {
  it("keeps only the branches that can reach an offered kind", () => {
    // `library.A` — cardio, your choice — offers roadwork, boxing or
    // conditioning. Rendering that as the tree with the rest pruned is what
    // lets one row of buttons ask the question instead of two.
    const tree = prunedTree(["roadwork", "boxing", "conditioning"]);
    assert.deepEqual(tree.map((n) => n.value), ["boxing", "cardio"]);
    assert.deepEqual(
      tree.find((n) => n.value === "cardio").children.map((n) => n.value),
      ["hiit", "steady"],
    );
  });

  it("prunes inside a branch, not just across the top", () => {
    const tree = prunedTree(["roadwork"]);
    assert.deepEqual(tree.map((n) => n.value), ["cardio"]);
    assert.deepEqual(tree[0].children.map((n) => n.value), ["steady"]);
  });

  it("leaves the tree alone when the plan names no shortlist", () => {
    assert.equal(prunedTree([]), SESSION_TREE);
    assert.equal(prunedTree(undefined), SESSION_TREE);
  });

  it("keeps every leaf under a kept branch reachable", () => {
    const tree = prunedTree(["sparring"]);
    assert.deepEqual(tree.map((n) => n.value), ["sparring"]);
    assert.equal(sessionFocus(["sparring", "touch"], tree), "sparring.touch");
  });
});
