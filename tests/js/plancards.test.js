// Which cards are showing, and what a card posts.
//
// These two functions decide whether a nudge appears and whether what it sends
// is the right shape, and both fail silently: a card that wrongly counts itself
// done simply does not render, on an evening you have no reason to go looking
// for it, and a mis-shaped body either drops a row or logs one twice. Neither
// announces itself, which is what makes them worth pinning here rather than
// leaving to the browser.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { cards, incomplete, partsFrom } from "../../web/app/panels/plan.js";

const block = (over) => ({
  key: "2026-10-05:X",
  token: "X",
  name: "a thing",
  label: "a thing",
  kind: "strength",
  choose: [],
  asks: null,
  slot: 0,
  optional: false,
  regress_to: null,
  group: null,
  extra: false,
  source: null,
  ...over,
});

// A practice: two prescribed rows in one outing, plus two extras.
const PRACTICE = [
  block({ key: "d:HIIT", token: "HIIT", label: "HIIT", kind: "conditioning", group: "practice" }),
  block({ key: "d:TECH", token: "TECH", label: "technique", kind: "boxing", group: "practice" }),
  block({ key: "d:SPAR", token: "SPAR", label: "sparring", kind: "sparring",
          group: "practice", extra: true }),
  block({ key: "d:N", token: "N", label: "neck", kind: "other", group: "practice", extra: true }),
];

const logged = (key, over = {}) => ({
  id: 1, kind: "conditioning", rpe: 7, minutes: 40, note: null, plan_key: key, ...over,
});

describe("cards", () => {
  it("puts one outing on one card", () => {
    const [card] = cards({ today: PRACTICE }, []);
    assert.equal(card.group, "practice");
    assert.deepEqual(card.rows.map((b) => b.token), ["HIIT", "TECH"]);
    assert.deepEqual(card.extras.map((b) => b.token), ["SPAR", "N"]);
  });

  it("splits separate outings into separate cards", () => {
    const list = cards(
      { today: [block({ key: "d:LFC", slot: 0 }), block({ key: "d:A3", slot: 1 })] },
      [],
    );
    assert.equal(list.length, 2);
  });

  it("does not close a card until every prescribed row is logged", () => {
    const [half] = cards({ today: PRACTICE }, [logged("d:HIIT")]);
    assert.equal(half.done, false);

    const [full] = cards({ today: PRACTICE }, [logged("d:HIIT"), logged("d:TECH")]);
    assert.equal(full.done, true);
  });

  it("does not require the extras to close a card", () => {
    // A practice bundle that only finished on the days you sparred would be
    // unfinishable four days a week.
    const [card] = cards({ today: PRACTICE }, [logged("d:HIIT"), logged("d:TECH")]);
    assert.equal(card.done, true);
    assert.equal(card.extras.length, 2);
  });

  it("ignores freehand sessions entirely", () => {
    // A session logged from the other panel carries no plan_key and answers
    // nothing. Letting it close a card would be the load model guessing.
    const [card] = cards({ today: PRACTICE }, [logged(null), logged(undefined)]);
    assert.equal(card.done, false);
  });

  it("shows nothing when the plan asks for nothing, or is absent", () => {
    assert.deepEqual(cards(null, []), []);
    assert.deepEqual(cards({ today: [] }, []), []);
  });
});

describe("partsFrom", () => {
  const draft = new Map([
    ["d:HIIT", { rpe: 8, minutes: "25" }],
    ["d:TECH", { rpe: 5, minutes: "45" }],
    ["note:0", { text: "  legs felt heavy " }],
  ]);

  it("posts one part per prescribed row, carrying the plan key", () => {
    const [card] = cards({ today: PRACTICE }, []);
    const parts = partsFrom(card, draft, new Set());
    assert.deepEqual(parts.map((p) => p.plan_key), ["d:HIIT", "d:TECH"]);
    assert.deepEqual(parts.map((p) => p.kind), ["conditioning", "boxing"]);
    assert.deepEqual(parts.map((p) => p.minutes), [25, 45]);
  });

  it("includes an extra only once it is switched on", () => {
    const [card] = cards({ today: PRACTICE }, []);
    assert.equal(partsFrom(card, draft, new Set()).length, 2);
    assert.equal(partsFrom(card, draft, new Set(["d:SPAR"])).length, 3);
  });

  it("never re-posts a row that is already logged", () => {
    // The unique index would reject it, but a card that tried is a card that
    // shows an error for a thing you did correctly.
    const [card] = cards({ today: PRACTICE }, [logged("d:HIIT")]);
    assert.deepEqual(partsFrom(card, draft, new Set()).map((p) => p.plan_key), ["d:TECH"]);
  });

  it("trims the note and sends null rather than an empty string", () => {
    const [card] = cards({ today: PRACTICE }, []);
    assert.equal(partsFrom(card, draft, new Set())[0].note, "legs felt heavy");

    const blank = new Map([...draft, ["note:0", { text: "   " }]]);
    assert.equal(partsFrom(card, blank, new Set())[0].note, null);
  });

  it("falls back to the first offered kind when the plan lets you choose", () => {
    const cardio = [block({ key: "d:A3", kind: null, choose: ["roadwork", "boxing"] })];
    const [card] = cards({ today: cardio }, []);
    const untouched = partsFrom(card, new Map([["d:A3", { rpe: 4, minutes: "30" }]]), new Set());
    assert.equal(untouched[0].kind, "roadwork");

    const picked = partsFrom(
      card, new Map([["d:A3", { rpe: 4, minutes: "30", kind: "boxing" }]]), new Set(),
    );
    assert.equal(picked[0].kind, "boxing");
  });
});

describe("incomplete", () => {
  const part = (over) => ({ kind: "strength", rpe: 7, minutes: 40, plan_key: "d:X", ...over });

  it("passes a fully answered card", () => {
    assert.equal(incomplete([part({}), part({ plan_key: "d:Y" })]), null);
  });

  it("catches a missing RPE before a missing minutes", () => {
    assert.equal(incomplete([part({ rpe: 0 })]), "pick an RPE");
  });

  it("catches minutes that were never typed", () => {
    // `Number.parseInt("")` is NaN, which would otherwise post and 422.
    assert.equal(incomplete([part({ minutes: Number.NaN })]), "minutes?");
  });

  it("catches minutes outside what the column will accept", () => {
    assert.equal(incomplete([part({ minutes: 0 })]), "minutes?");
    assert.equal(incomplete([part({ minutes: 601 })]), "minutes?");
  });

  it("catches a half-filled card, not just a wholly empty one", () => {
    assert.equal(incomplete([part({}), part({ rpe: 0, plan_key: "d:Y" })]), "pick an RPE");
  });
});
