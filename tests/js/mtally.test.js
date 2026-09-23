// The phone's count of which screens get opened — `web/m/tally.js`.
//
// Small, and tested for the two things that would quietly spoil it: counts
// that do not add up per day, and a store that grows for ever. `localStorage`
// is stubbed onto `globalThis` before the import, as in `mheld.test.js`.

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

const store = new Map();
let storageThrows = false;
globalThis.localStorage = {
  getItem: (k) => {
    if (storageThrows) throw new Error("denied");
    return store.has(k) ? store.get(k) : null;
  },
  setItem: (k, v) => {
    if (storageThrows) throw new Error("quota");
    store.set(k, v);
  },
  removeItem: (k) => store.delete(k),
};

const { count, keep, tallies, KEEP_DAYS } = await import("../../web/m/tally.js");

beforeEach(() => {
  store.clear();
  storageThrows = false;
});

describe("the screen tally", () => {
  it("counts each screen per day", () => {
    count("open", "2026-09-22");
    count("eat", "2026-09-22");
    count("eat", "2026-09-22");
    count("eat", "2026-09-23");
    assert.deepEqual(tallies(), {
      "2026-09-22": { open: 1, eat: 2 },
      "2026-09-23": { eat: 1 },
    });
  });

  it("refuses a name the laptop would refuse", () => {
    // The route 422s the whole send over one bad key, which would strand every
    // other count behind it for good.
    count("Eat", "2026-09-23");
    count("eat drinks", "2026-09-23");
    count("eat.drinks", "2026-09-23");
    assert.deepEqual(tallies(), { "2026-09-23": { "eat.drinks": 1 } });
  });

  it("keeps four weeks and no more", () => {
    const all = { "2026-08-26": { open: 1 }, "2026-08-27": { open: 1 }, "2026-09-23": { open: 1 } };
    assert.equal(KEEP_DAYS, 28);
    assert.deepEqual(Object.keys(keep(all, "2026-09-23")), ["2026-08-27", "2026-09-23"]);
  });

  it("shrugs off storage that will not take it", () => {
    storageThrows = true;
    assert.doesNotThrow(() => count("open", "2026-09-23"));
    assert.deepEqual(tallies(), {});
  });

  it("throws away a value it cannot read", () => {
    store.set("dashboard.m.tally", "{half");
    assert.deepEqual(tallies(), {});
    count("open", "2026-09-23");
    assert.deepEqual(tallies(), { "2026-09-23": { open: 1 } });
  });
});
