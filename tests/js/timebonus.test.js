// The speed-bonus preview.
//
// This module is a deliberate second copy of `scoring.time_multiplier`, and
// the risk it carries is drift: the browser quoting one price and the server
// charging another. So the points pinned here are the *same three literals*
// pinned in tests/test_scoring.py — neutral at 1.5x, the floor of 0.66 reached
// at 3x, and the cap of 1.5. Editing either side alone fails a test on that
// side, which is the most a duplicated constant can be asked to do.
//
// `minutesBox` is not tested here: it builds DOM nodes, and there is no
// document under `node --test`.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { neutralMinutes, priceLabel, timeMultiplier } from "../../web/app/timebonus.js";

const close = (a, b, why) => assert.ok(Math.abs(a - b) < 0.001, `${why}: ${a} vs ${b}`);

describe("timeMultiplier", () => {
  it("pays exactly 1x at one and a half times the reference", () => {
    close(timeMultiplier(30, 20), 1, "20-minute problem, 30 minutes");
    close(timeMultiplier(15, 10), 1, "10-minute problem, 15 minutes");
    close(timeMultiplier(90, 60), 1, "60-minute problem, 90 minutes");
  });

  it("bottoms out at 0.66, and not before three times the reference", () => {
    close(timeMultiplier(60, 20), 0.66, "3x");
    close(timeMultiplier(120, 20), 0.66, "6x is still the floor");
    assert.ok(timeMultiplier(40, 20) > 0.76, "2x must be well above the floor");
  });

  it("caps at 1.5 however fast you are", () => {
    close(timeMultiplier(10, 20), 1.5, "half the reference");
    close(timeMultiplier(1, 20), 1.5, "one minute");
  });

  it("never leaves its bounds", () => {
    for (let m = 1; m < 600; m++) {
      const value = timeMultiplier(m, 20);
      assert.ok(value >= 0.66 && value <= 1.5, `${m} min -> ${value}`);
    }
  });

  it("is monotonic — faster is never worth less", () => {
    let previous = Infinity;
    for (let m = 1; m < 400; m++) {
      const value = timeMultiplier(m, 20);
      assert.ok(value <= previous, `${m} min went back up`);
      previous = value;
    }
  });

  it("is exactly neutral when either number is missing", () => {
    // Blank is a first-class answer, not a skipped field.
    assert.equal(timeMultiplier(null, 20), 1);
    assert.equal(timeMultiplier(12, null), 1);
    assert.equal(timeMultiplier(0, 20), 1);
    assert.equal(timeMultiplier(NaN, 20), 1);
  });
});

describe("neutralMinutes", () => {
  it("is the number to beat, in whole minutes", () => {
    assert.equal(neutralMinutes(20), 30);
    assert.equal(neutralMinutes(45), 68);
  });
});

describe("priceLabel", () => {
  it("names the target before anything has been typed", () => {
    assert.match(priceLabel(null, 20), /^30 min scores ×1\.00/);
  });

  it("reads as a bonus above the neutral point and a decay below it", () => {
    assert.match(priceLabel(12, 20), /faster than 30 min/);
    assert.match(priceLabel(90, 20), /slower than 30 min/);
    assert.match(priceLabel(30, 20), /level with 30 min/);
  });

  it("says nothing at all when the tier has no reference time", () => {
    // An unconfigured tier turns the feature off rather than showing an
    // unpriced box.
    assert.equal(priceLabel(12, null), "");
  });
});
