// The two quick logs, checked where they can be checked without a browser.
//
// Almost all of `quicklog.js` is DOM, but the two things most worth pinning
// down are not, and both fail silently in ways you would only find in the
// moment you least want to be debugging a dashboard.
//
// The first is the branch. A urine entry that stops to ask for a solidity, or
// a stool entry that skips straight to volume, produces a body the server's
// own validator rejects — *after* four keystrokes have been spent on it, with
// the error arriving as a toast rather than as anything you can correct.
//
// The second is duplicate keys inside a step. Two options sharing a letter is
// a button no key can reach, and it does not look like a bug: the panel draws
// perfectly, the letter answers with the first of the two, and you would
// simply come to believe the second one is not offered.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { STEPS as BATHROOM } from "../../web/app/panels/bathroom.js";
import { STEPS as MEALS } from "../../web/app/panels/meals.js";
import { applicableSteps } from "../../web/app/quicklog.js";

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

describe("every step's keys", () => {
  for (const [panel, steps] of [["bathroom", BATHROOM], ["meals", MEALS]]) {
    it(`are unique within each ${panel} step`, () => {
      for (const step of steps) {
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
        for (const [key] of step.options) {
          assert.ok(!reserved.has(key), `${panel} ▸ ${step.name} binds the hour key ${key}`);
        }
      }
    });
  }
});
