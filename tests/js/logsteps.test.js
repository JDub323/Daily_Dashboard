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
  parseAmount,
} from "../../web/app/logs.js";

const names = (steps) => steps.map((s) => s.name);

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
    assert.deepEqual(names(applicableSteps(MEALS, {})), ["size", "quality"]);
    assert.deepEqual(names(applicableSteps(MEALS, { size: "large" })), ["size", "quality"]);
  });

  it("costs three keystrokes counting the one that opens it", () => {
    assert.equal(applicableSteps(MEALS, {}).length + 1, 3);
  });
});

describe("the drinks ladder", () => {
  it("asks which, then how many, and nothing else", () => {
    assert.deepEqual(names(applicableSteps(DRINKS, {})), ["kind", "amount"]);
  });

  it("answers the common case in two keystrokes", () => {
    // `w` opens the card and answers the kind; the box arrives holding 1 with
    // the text selected, so Enter logs one drink of water. That is the whole
    // claim of this panel — the number is only typed when there is something
    // to say — and a preset that was blank would cost a keystroke on every
    // single entry to save one on the occasional 1.4.
    const [, amount] = DRINKS;
    assert.equal(parseAmount(amount.field.preset), 1);
  });

  it("keeps its first question on the keyboard", () => {
    // The first step's keys are what open the card from the focus ring, and
    // `panel.js` will not hand a panel any of these — so a kind bound to one
    // would answer while the ladder was up and do nothing at a card sitting
    // idle. `g` is the trap here: gatorade is `s` for exactly this reason.
    const reserved = new Set(["h", "j", "k", "l", "i", "f", "g", "n"]);
    for (const [key] of DRINKS[0].options) {
      assert.ok(!reserved.has(key), `drinks ▸ kind binds ${key}, which never reaches the panel`);
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
  for (const [panel, steps] of [["bathroom", BATHROOM], ["meals", MEALS], ["drinks", DRINKS]]) {
    it(`are unique within each ${panel} step`, () => {
      for (const step of steps) {
        if (!step.options) continue;
        const keys = step.options.map(([key]) => key);
        assert.equal(
          new Set(keys).size,
          keys.length,
          `${panel} ▸ ${step.name} has a key on two options: ${keys.join(" ")}`,
        );
      }
    });

    it(`do not collide with the hour keys on ${panel}`, () => {
      // `-` and `+` walk the hour from any step, so an option bound to one of
      // them would be unreachable in exactly the same silent way.
      const reserved = new Set(["-", "_", ",", "+", "=", "."]);
      for (const step of steps) {
        for (const [key] of step.options ?? []) {
          assert.ok(!reserved.has(key), `${panel} ▸ ${step.name} binds the hour key ${key}`);
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
