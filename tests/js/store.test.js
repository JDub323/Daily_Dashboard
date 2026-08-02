// The two decisions the store makes about *which day* the cache describes.
//
// These are the guards on the midnight bug: a tab left open past midnight kept
// serving the boot snapshot, and the chips then wrote yesterday's finished
// count against today's date. `perfect_days` counts any row with done = total,
// so that write recorded an untouched day as perfect and grew the streak on
// its own. Both functions are pure so this can be checked here rather than by
// leaving a tab open overnight and reading the table in the morning.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { today } from "../../web/app/api.js";
import { completionWrite, dayHasTurned, store } from "../../web/app/store.js";

const YESTERDAY = "2026-08-01";

/** A boot payload with only the fields these two functions read. */
const state = (date, completion = []) => ({ date, completion });

describe("dayHasTurned", () => {
  it("is false while the cached state is for the current local date", () => {
    store.state = state(today());
    assert.equal(dayHasTurned(), false);
  });

  it("is true once the local date has moved past the cached one", () => {
    store.state = state(YESTERDAY);
    assert.equal(dayHasTurned(), true);
  });

  it("is false before the first load — there is no day to have turned from", () => {
    // Boot calls this via the interval, which can fire before loadState()
    // resolves on a slow first paint. Reading `.date` off null would take the
    // whole page down.
    store.state = null;
    assert.equal(dayHasTurned(), false);
  });
});

describe("completionWrite", () => {
  it("addresses the write to the day the counts were read off, not to today", () => {
    // The bug, stated as a test: the state is yesterday's, so the row is
    // yesterday's, even though the clock now says otherwise.
    const write = completionWrite(state(YESTERDAY), 8, 8);
    assert.deepEqual(write, { date: YESTERDAY, done: 8, total: 8 });
    assert.notEqual(write.date, today());
  });

  it("returns null when the stored row already matches", () => {
    // The old client recomputed a float pct and compared it against a stored
    // float, which differed often enough to fire a write on every page load.
    const current = state(YESTERDAY, [{ date: YESTERDAY, done: 3, total: 8 }]);
    assert.equal(completionWrite(current, 3, 8), null);
  });

  it("writes when the count changed", () => {
    const current = state(YESTERDAY, [{ date: YESTERDAY, done: 3, total: 8 }]);
    assert.deepEqual(completionWrite(current, 4, 8), { date: YESTERDAY, done: 4, total: 8 });
  });

  it("writes when the total changed but the done count did not", () => {
    // A chip added or removed changes what "perfect" means for the day, so
    // comparing `done` alone would leave a stale total behind.
    const current = state(YESTERDAY, [{ date: YESTERDAY, done: 8, total: 8 }]);
    assert.deepEqual(completionWrite(current, 8, 9), { date: YESTERDAY, done: 8, total: 9 });
  });

  it("ignores rows for other days", () => {
    // The completion list is the whole history, not just today.
    const current = state(YESTERDAY, [
      { date: "2026-07-30", done: 8, total: 8 },
      { date: today(), done: 8, total: 8 },
    ]);
    assert.deepEqual(completionWrite(current, 8, 8), { date: YESTERDAY, done: 8, total: 8 });
  });
});
