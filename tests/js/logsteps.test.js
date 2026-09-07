// What the three quick logs ask, checked where it can be checked without a
// browser.
//
// This was `quicklog.test.js` and it tested the desktop's logging cards. Those
// are deleted — every log is answered on the phone now — and the file is still
// here because none of what it pins down was ever about the desktop. The
// tables and the two functions that read them live in `logs.js`, which has no
// client in it, and both things below fail silently in ways you would only
// find in the moment you least want to be debugging a dashboard.
//
// The first is the branch. A urine entry that stops to ask for a solidity, or
// a stool entry that skips straight to volume, produces a body the server's
// own validator rejects — *after* the answers have been given, with the error
// arriving as a toast rather than as anything you can correct.
//
// The second is duplicate keys inside a step. Two options sharing a letter is
// an answer no key can reach, and it does not look like a bug: the screen
// draws perfectly, the letter answers with the first of the two, and you would
// simply come to believe the second one is not offered. The phone has no
// keyboard, so this one is now guarding a property nothing currently uses —
// kept because the keys are the contract (see the head of `logs.js`) and a
// collision introduced today would be found by whatever reads them next.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  applicableSteps,
  BATHROOM_STEPS as BATHROOM,
  DRINK_STEPS as DRINKS,
  MEAL_STEPS as MEALS,
  missingSteps,
  parseAmount,
  stepOptions,
} from "../../web/app/logs.js";

const names = (steps) => steps.map((s) => s.name);

/** A step by name. The tables have grown optional rows in the middle of them,
 *  so positional destructuring — which is how this file used to reach the
 *  amount box — now silently picks up whatever was inserted above it. */
const step = (steps, name) => steps.find((s) => s.name === name);

describe("the bathroom branch", () => {
  it("asks kind first, before it can know which branch it is on", () => {
    assert.deepEqual(names(applicableSteps(BATHROOM, {})), ["kind", "volume"]);
  });

  it("asks a stool for its solidity and never for a colour", () => {
    const steps = applicableSteps(BATHROOM, { kind: "stool" });
    assert.deepEqual(names(steps), ["kind", "solidity", "volume"]);
  });

  it("asks urine for its colour and never for a solidity", () => {
    const steps = applicableSteps(BATHROOM, { kind: "urine" });
    assert.deepEqual(names(steps), ["kind", "color", "volume"]);
  });

  it("costs four keystrokes either way, counting the one that opens it", () => {
    // The budget the whole feature is built against. A fifth question here
    // would be a design decision, not a detail — three seconds is the claim,
    // and it is the claim that decides whether this gets logged in February.
    for (const kind of ["stool", "urine"]) {
      assert.equal(applicableSteps(BATHROOM, { kind }).length + 1, 4);
    }
  });
});

describe("the meal ladder", () => {
  it("has no branch at all", () => {
    // Four questions now and still no `when` on any of them: the preset row
    // and the flag row are always drawn, whatever has been answered. That is
    // the difference between an *optional* question and a *conditional* one,
    // and the meal log has only the first kind.
    const asked = ["preset", "size", "quality", "flags"];
    assert.deepEqual(names(applicableSteps(MEALS, {})), asked);
    assert.deepEqual(names(applicableSteps(MEALS, { size: "large" })), asked);
    assert.deepEqual(names(applicableSteps(MEALS, { preset: "apple" })), asked);
  });

  it("still costs three taps counting the one that opens it", () => {
    // The budget the whole feature is built against, and the number the two
    // new rows were required not to change. They do not, because neither is
    // required: `missingSteps` is what the commit button reads, and it counts
    // two — size and quality — exactly as it did before they existed.
    assert.equal(missingSteps(MEALS, {}).length + 1, 3);
  });

  it("costs one tap for a meal that is one of the usual ones", () => {
    // What the preset row buys. Tapping it fills both required answers, so the
    // entry is a tap to open, a tap to name it and a tap to log.
    assert.deepEqual(missingSteps(MEALS, { size: "large", quality: 5 }).map((s) => s.name), []);
  });
});

describe("the drinks ladder", () => {
  it("asks what was in it, then how many, and nothing else", () => {
    // With no tags there is no drink to name, so the middle step is not asked
    // at all — the one conditional row in this log.
    assert.deepEqual(names(applicableSteps(DRINKS, {})), ["tags", "amount"]);
  });

  it("asks for the tags as a set rather than a choice", () => {
    // The whole change: coffee with sugar in it is both, and a step that is
    // not `multi` is a step where answering the second one throws away the
    // first. A client reading these steps decides between a radio row and a
    // toggle row on this flag alone.
    const [tags] = DRINKS;
    assert.equal(tags.multi, true);
  });

  it("offers no tag for water, none for gatorade and none for coffee", () => {
    // Water is the untagged row — a tag true of every drink in the log tells
    // you nothing about any of them. Gatorade is `electrolyte` and `sugar`
    // together, which a row can say directly. And `coffee` is now a *name*:
    // it was the one tag that named a drink rather than a substance, which
    // left an energy drink with nowhere to record its caffeine. Any of the
    // three offered here would post a body the server refuses with a 422.
    const values = stepOptions(step(DRINKS, "tags"), {}).map(([, value]) => value);
    assert.deepEqual(values, [
      "milk", "sugar", "electrolyte", "caffeine", "alcohol", "carbonated",
    ]);
  });

  it("answers the common case with the amount alone", () => {
    // No tags is a glass of water, so the fastest entry is the amount and
    // nothing else — and the box arrives holding 1. That is the whole claim of
    // this log: the number is only typed when there is something to say.
    assert.equal(parseAmount(step(DRINKS, "amount").field.preset), 1);
  });

  it("keeps its first question on the keyboard", () => {
    // The first step's keys are what open the card from the focus ring, and
    // `panel.js` will not hand a panel any of these — so a tag bound to one
    // would answer while the ladder was up and do nothing at a card sitting
    // idle. `g` is the trap here, and it is why gatorade was ever `s`.
    const reserved = new Set(["h", "j", "k", "l", "i", "f", "g", "n"]);
    for (const [key] of stepOptions(step(DRINKS, "tags"), {})) {
      assert.ok(!reserved.has(key), `drinks ▸ tags binds ${key}, which never reaches the panel`);
    }
  });
});

describe("the amount box", () => {
  it("takes the three spellings somebody actually types", () => {
    assert.equal(parseAmount("1.5"), 1.5);
    assert.equal(parseAmount(".5"), 0.5);
    assert.equal(parseAmount("1 1/2"), 1.5);
    assert.equal(parseAmount("3/4"), 0.75);
    assert.equal(parseAmount("  2  "), 2);
  });

  it("rounds off a repeating fraction", () => {
    // Otherwise a third of a bottle arrives as 0.3333333333333333 in a column
    // that will be summed every day for a year.
    assert.equal(parseAmount("1/3"), 0.33);
  });

  it("refuses everything that is not a quantity", () => {
    for (const said of ["", "   ", "abc", "0", "-1", "1/0", null, undefined]) {
      assert.equal(parseAmount(said), null, `parsed ${JSON.stringify(said)}`);
    }
  });

  it("refuses a misplaced decimal point at the box that took it", () => {
    // The table and the request model both carry this bound. Checked here as
    // well so a typo is refused by the panel rather than by a toast a round
    // trip later, with the ladder already torn down.
    assert.equal(parseAmount("150"), null);
    assert.equal(parseAmount("20"), 20);
  });
});

describe("every step's keys", () => {
  // A draft per log that reaches every option there is. One step's options are
  // a function of the answers above it — the drink names — so an empty draft
  // would check the tag row and silently check nothing at all on the row below
  // it, which is the shape of hole this suite exists to close.
  const drafts = {
    bathroom: {},
    meals: {},
    drinks: { tags: ["milk"] },
  };

  for (const [panel, steps] of [["bathroom", BATHROOM], ["meals", MEALS], ["drinks", DRINKS]]) {
    it(`are unique within each ${panel} step`, () => {
      for (const one of steps) {
        // Null keys skipped rather than counted. Two rows carry no keys at all
        // — the meal presets and the drink names — and that is deliberate:
        // they are the two lists most likely to be edited, and eight arbitrary
        // letters would be eight more things to keep unique for a surface that
        // has no keyboard. A row of nulls is not eight options sharing a key.
        const keys = stepOptions(one, drafts[panel]).map(([key]) => key).filter(Boolean);
        assert.equal(
          new Set(keys).size,
          keys.length,
          `${panel} ▸ ${one.name} has a key on two options: ${keys.join(" ")}`,
        );
      }
    });

    it(`do not collide with the hour keys on ${panel}`, () => {
      // `-` and `+` walk the hour from any step, so an option bound to one of
      // them would be unreachable in exactly the same silent way.
      const reserved = new Set(["-", "_", ",", "+", "=", "."]);
      for (const one of steps) {
        for (const [key] of stepOptions(one, drafts[panel])) {
          assert.ok(!reserved.has(key), `${panel} ▸ ${one.name} binds the hour key ${key}`);
        }
      }
    });

    it(`asks at most one typed question on ${panel}`, () => {
      // A second typed answer in one log would mean two keyboards opening on
      // a phone, one after the other, which is the specific thing that makes a
      // log get abandoned. One box is the budget — see `DRINK_STEPS`, which
      // spends it and argues for why.
      assert.ok(steps.filter((step) => step.field).length <= 1);
    });
  }
});
