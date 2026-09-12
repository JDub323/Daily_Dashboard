// What the health panel says on a day the plan asks for nothing.
//
// Three empty states arrive from the server as the identical empty list, and
// the panel has to tell them apart from two booleans and a date. That is
// arithmetic rather than markup, which is why it is testable here at all — the
// rest of the camp block builds DOM nodes and there is no document under
// `node --test`, so it is exercised in the browser.
//
// The case worth having a test for is the third one. Weeks 8-18 of the camp are
// deliberately unwritten — VJP runs out on 2026-10-03 and what replaces it is
// not decided — and if those days rendered as "rest day." the panel would be
// prescribing three months off in a tab built to catch exactly that.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { emptyLine } from "../../web/app/panels/health.js";

const plan = (over) => ({
  today: [],
  week_planned: false,
  ends: "2026-10-03",
  ...over,
});

describe("emptyLine", () => {
  it("says nothing when the day actually asks for something", () => {
    assert.equal(emptyLine(plan({ today: [{ token: "LFC" }] }), "2026-08-31"), null);
  });

  it("calls an empty day inside a written week a rest day", () => {
    // Week 7's Saturday: the plan covers the week and left the cell blank,
    // which is the plan working rather than the plan missing.
    assert.equal(emptyLine(plan({ week_planned: true }), "2026-10-03"), "rest day.");
  });

  it("does not call an unwritten week a rest day", () => {
    const line = emptyLine(plan(), "2026-08-26");
    assert.notEqual(line, "rest day.");
    assert.equal(line, "no plan written for this week.");
  });

  it("names the date the plan ran out, once it is behind us", () => {
    assert.equal(
      emptyLine(plan(), "2026-10-05"),
      "the plan runs out on 2026-10-03. nothing written past it yet.",
    );
  });

  it("does not claim to have run out while the end is still ahead", () => {
    // Today's real state: week 2 is unwritten, but VJP has not started yet, so
    // "the plan ran out" would be flatly false.
    assert.equal(emptyLine(plan(), "2026-08-26"), "no plan written for this week.");
  });

  it("copes with a plan that has no blocks at all", () => {
    assert.equal(emptyLine(plan({ ends: null }), "2026-08-26"), "no plan written for this week.");
  });

  // The fourth empty state, and the one that has to be checked before
  // `week_planned` rather than after it. A paused week is still a *written*
  // week — the plan for it exists and you are simply not in it — so the
  // wrong order here renders a fortnight of illness as a fortnight of rest.
  const paused = { since: "2026-09-08", reason: "flu", days: 4, resumes: "2026-09-27" };

  it("says the camp is paused rather than calling it a rest day", () => {
    const line = emptyLine(plan({ week_planned: true, paused }), "2026-09-11");
    assert.notEqual(line, "rest day.");
    assert.equal(line, "camp paused since 2026-09-08 — day 4. flu.");
  });

  it("still says nothing when a paused day somehow asks for something", () => {
    // Belt and braces on the server's own rule: `prescription` returns nothing
    // on a paused day, so this pairing should be unreachable. If it ever
    // arrives, what the day asks for wins — a block on screen with "paused"
    // written under it is the one reading that helps nobody.
    assert.equal(emptyLine(plan({ today: [{ token: "LFC" }], paused }), "2026-09-11"), null);
  });
});
