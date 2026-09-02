// Which way the check-in's numbers run.
//
// The panel shows every item counting up in the thing it names — 5 fatigue is
// wiped out — while the table keeps storing 1 = worst, 5 = best for all five so
// that `score` stays a plain sum and the rows logged before the change are
// still comparable with the ones after it. Two directions, one pair of
// functions between them, and a mistake in either is silent: a morning answered
// honestly would be recorded as its opposite and every windowed mean built on
// it would be wrong in a way nothing on screen would show.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { toShown, toStored } from "../../web/app/logs.js";

const INVERTED = ["fatigue", "soreness", "stress"];
const PLAIN = ["sleep_quality", "motivation"];

describe("the three inverted items", () => {
  it("stores a tap on 1 as the old 5", () => {
    // The mapping John asked for, stated: "1/5 fatigue" — barely any — is the
    // best morning, and the table's best is 5.
    for (const key of INVERTED) assert.equal(toStored(key, 1), 5);
  });

  it("stores a tap on 5 as the old 1", () => {
    for (const key of INVERTED) assert.equal(toStored(key, 5), 1);
  });

  it("leaves the middle alone", () => {
    for (const key of INVERTED) assert.equal(toStored(key, 3), 3);
  });

  it("shows a stored value back the way it was tapped", () => {
    for (const key of INVERTED) {
      for (const shown of [1, 2, 3, 4, 5]) {
        assert.equal(toShown(key, toStored(key, shown)), shown);
      }
    }
  });
});

describe("the two that were already the right way up", () => {
  it("passes them through untouched, in both directions", () => {
    for (const key of PLAIN) {
      for (const n of [1, 2, 3, 4, 5]) {
        assert.equal(toStored(key, n), n);
        assert.equal(toShown(key, n), n);
      }
    }
  });
});

describe("the score the server computes", () => {
  it("still reads 25 for the best morning you can have", () => {
    // Best is now 5 on the two and 1 on the three, which is the whole point of
    // the change — and it has to still sum to the ceiling, because `score` is
    // compared against a baseline built from rows written under the old panel.
    const best = [...PLAIN.map((k) => toStored(k, 5)), ...INVERTED.map((k) => toStored(k, 1))];
    assert.equal(best.reduce((a, b) => a + b, 0), 25);
  });

  it("still reads 5 for the worst", () => {
    const worst = [...PLAIN.map((k) => toStored(k, 1)), ...INVERTED.map((k) => toStored(k, 5))];
    assert.equal(worst.reduce((a, b) => a + b, 0), 5);
  });

  it("keeps every stored value inside the table's 1–5 CHECK", () => {
    for (const key of [...PLAIN, ...INVERTED]) {
      for (const shown of [1, 2, 3, 4, 5]) {
        const stored = toStored(key, shown);
        assert.ok(stored >= 1 && stored <= 5, `${key} ${shown} → ${stored}`);
      }
    }
  });
});
