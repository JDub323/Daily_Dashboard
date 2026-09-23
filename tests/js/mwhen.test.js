// Which day an entry logged on the phone belongs to.
//
// Two lines of arithmetic that decide whether a meal is filed on the day it
// was eaten, and the failure is silent in both directions: the entry lands,
// the queue empties, and nothing on screen is wrong except the date.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

const { dateForHour, localISO } = await import("../../web/m/when.js");

/** A local time, built the way `new Date()` gives one — no UTC anywhere, since
 *  the whole point of this module is that the phone's own clock decides. */
const at = (y, m, d, h, min = 0) => new Date(y, m - 1, d, h, min);

describe("the day an hour belongs to", () => {
  it("is today for the hour it is now", () => {
    assert.equal(dateForHour(15, at(2026, 8, 31, 15, 46)), "2026-08-31");
  });

  it("is today for an hour already past", () => {
    // The case the picker exists for: noticing at three that lunch never got
    // logged, and scrolling back to noon.
    assert.equal(dateForHour(12, at(2026, 8, 31, 15, 46)), "2026-08-31");
  });

  it("is yesterday for an hour still ahead of the clock", () => {
    // Half past midnight, logging the dinner you forgot. Stamped with today's
    // date this landed at 8pm *tonight* — nineteen hours in the future, and
    // absent from the day it happened on, which is a meal you logged and then
    // could not find.
    assert.equal(dateForHour(20, at(2026, 9, 1, 0, 30)), "2026-08-31");
  });

  it("crosses a month end", () => {
    assert.equal(dateForHour(23, at(2026, 9, 1, 1, 5)), "2026-08-31");
  });

  it("crosses a year end", () => {
    assert.equal(dateForHour(22, at(2027, 1, 1, 2, 0)), "2026-12-31");
  });

  it("survives the hour the clocks go back", () => {
    // A local-time day arithmetic on a 25-hour day still has to name a date.
    // `setDate` moves the calendar day, not 24 hours, so this holds wherever
    // the phone is.
    const day = dateForHour(23, at(2026, 11, 1, 1, 30));
    assert.equal(day, "2026-10-31");
  });

  it("agrees with localISO on the day it does not move", () => {
    const now = at(2026, 8, 31, 15, 46);
    assert.equal(dateForHour(0, now), localISO(now));
  });
});

const { dateForTime, entryClock, hhmm, hoursLabel, spanHours } = await import("../../web/m/when.js");

describe("the day a session start belongs to", () => {
  it("is today for a start already past", () => {
    assert.equal(dateForTime("17:30", at(2026, 9, 23, 19, 5)), "2026-09-23");
  });

  it("is today for a start this very minute", () => {
    assert.equal(dateForTime("19:05", at(2026, 9, 23, 19, 5)), "2026-09-23");
  });

  it("is yesterday for a start still ahead of the clock", () => {
    // Began at 11:15pm, logged at 12:40am on the way home.
    assert.equal(dateForTime("23:15", at(2026, 9, 24, 0, 40)), "2026-09-23");
  });

  it("compares minutes, not just hours", () => {
    assert.equal(dateForTime("19:06", at(2026, 9, 23, 19, 5)), "2026-09-22");
  });
});

describe("a night in bed", () => {
  it("crosses midnight", () => {
    assert.equal(spanHours("23:40", "07:10"), 7.5);
  });

  it("does not need to", () => {
    assert.equal(spanHours("00:10", "07:40"), 7.5);
  });

  it("is zero, not a day, for the same time twice — callers refuse it", () => {
    assert.equal(spanHours("07:00", "07:00"), 0);
  });

  it("reads as hours and minutes", () => {
    assert.equal(hoursLabel(7.5), "7h 30m");
    assert.equal(hoursLabel(spanHours("23:05", "06:50")), "7h 45m");
  });
});

describe("a quick-log row's time", () => {
  it("is the hour when the minute is unknown", () => {
    assert.equal(entryClock(20, null), "8pm");
    assert.equal(entryClock(20, undefined), "8pm");
  });

  it("is the minute when it was logged as it happened", () => {
    assert.equal(entryClock(20, 7), "8:07pm");
    assert.equal(entryClock(0, 0), "12:00am");
  });

  it("is written the way a time input writes it", () => {
    assert.equal(hhmm(at(2026, 9, 23, 7, 5)), "07:05");
  });
});
