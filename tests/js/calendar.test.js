// Overlap packing and the drawn window.
//
// These are the two pieces of the week grid that are arithmetic rather than
// markup, and they are the two that go wrong invisibly: a packing bug hides an
// event completely (it draws underneath another one) and a window bug puts an
// event outside the box entirely. Neither shows up as an error anywhere.
//
// The rest of the panel builds DOM nodes and there is no document under
// `node --test`, so it is exercised in the browser instead.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { hueMap, outsiders, pack, windowFor } from "../../web/app/panels/calendar.js";

// His actual nine, which is the set the collision behaviour has to hold for.
const REAL = [
  "Special Events", "Piano App Deadlines", "Tests", "Classes", "Hobbies",
  "Homework", "Meals", "Holidays in United States", "Systems Programming FA25",
];

const at = (from, to, title = "") => ({ from, to, title });
const layout = (packed, title) => packed.find((e) => e.title === title);

describe("pack", () => {
  it("gives a lone event the whole column", () => {
    const [only] = pack([at(600, 660)]);
    assert.equal(only.column, 0);
    assert.equal(only.of, 1);
  });

  it("splits two events that overlap", () => {
    // The real collision on this account: a course calendar and a personal one
    // both carrying the 10:30 class.
    const packed = pack([at(630, 680, "lecture"), at(630, 680, "systems")]);
    assert.deepEqual(packed.map((e) => e.column).sort(), [0, 1]);
    assert.ok(packed.every((e) => e.of === 2));
  });

  it("reuses a column once its event has finished", () => {
    // 9–10 and 10–11 do not overlap, so the second takes the first's column
    // and neither is narrowed.
    const packed = pack([at(540, 600, "a"), at(600, 660, "b")]);
    assert.equal(layout(packed, "a").of, 1);
    assert.equal(layout(packed, "b").of, 1);
  });

  it("keeps a crowded morning from narrowing a quiet afternoon", () => {
    // Column counts are per cluster of mutual overlap, not per day: without
    // that, three stacked 9am events would draw the 3pm one at a third width
    // with two empty columns beside it.
    const packed = pack([
      at(540, 600, "a"), at(540, 600, "b"), at(540, 600, "c"),
      at(900, 960, "afternoon"),
    ]);
    assert.equal(layout(packed, "a").of, 3);
    assert.equal(layout(packed, "afternoon").of, 1);
    assert.equal(layout(packed, "afternoon").column, 0);
  });

  it("chains a cluster through a long event that spans two short ones", () => {
    // 9–12 overlaps both 9–10 and 11–12, which do not overlap each other. All
    // three are one cluster, and the long one must not be drawn over.
    const packed = pack([at(540, 720, "long"), at(540, 600, "early"), at(660, 720, "late")]);
    assert.ok(packed.every((e) => e.of === 2));
    assert.notEqual(layout(packed, "long").column, layout(packed, "early").column);
    assert.notEqual(layout(packed, "long").column, layout(packed, "late").column);
    // ...but the two short ones are free to share, since they never coincide.
    assert.equal(layout(packed, "early").column, layout(packed, "late").column);
  });

  it("does not lose an event", () => {
    const input = [at(540, 600, "a"), at(550, 610, "b"), at(1200, 1260, "c")];
    assert.equal(pack(input).length, input.length);
  });
});

// The window used to widen to fit whatever the week contained, which meant one
// 11:30pm event made every day on screen sixteen hours tall. It is clamped now
// and the events outside it go to the two edge strips instead, so these two
// suites are halves of one behaviour: `windowFor` says where the edges are and
// `outsiders` says who is past them. A disagreement between them loses an
// event silently, which is the failure the pair exists to prevent.
describe("windowFor", () => {
  it("is the waking hours, whatever the week contains", () => {
    assert.deepEqual(windowFor([]), [8 * 60, 20 * 60]);
  });

  it("does not open earlier for an early event", () => {
    assert.deepEqual(windowFor([at(6 * 60 + 25, 7 * 60)]), [8 * 60, 20 * 60]);
  });

  it("does not close later for a late one", () => {
    assert.deepEqual(windowFor([at(22 * 60, 23 * 60 + 10)]), [8 * 60, 20 * 60]);
  });
});

describe("outsiders", () => {
  const WIN = [8 * 60, 20 * 60];

  it("keeps an ordinary daytime event in the grid", () => {
    const { before, after, inside } = outsiders([at(600, 660, "class")], ...WIN);
    assert.equal(inside.length, 1);
    assert.equal(before.length + after.length, 0);
  });

  it("sends a wholly-early event to the strip above", () => {
    const { before, inside } = outsiders([at(6 * 60, 7 * 60, "gym")], ...WIN);
    assert.deepEqual(before.map((e) => e.title), ["gym"]);
    assert.equal(inside.length, 0);
  });

  it("sends a wholly-late event to the strip below", () => {
    const { after, inside } = outsiders([at(21 * 60, 22 * 60, "film")], ...WIN);
    assert.deepEqual(after.map((e) => e.title), ["film"]);
    assert.equal(inside.length, 0);
  });

  it("keeps an event that straddles an edge in the grid", () => {
    // Mostly on screen, so it is clipped by the column rather than exiled to a
    // strip. Both edges, because the two are separate comparisons and getting
    // one of them backwards is exactly the bug this catches.
    const { before, after, inside } = outsiders(
      [at(7 * 60 + 30, 8 * 60 + 30, "early"), at(19 * 60, 21 * 60, "late")], ...WIN);
    assert.deepEqual(inside.map((e) => e.title), ["early", "late"]);
    assert.equal(before.length + after.length, 0);
  });

  it("treats an event ending exactly on the open edge as outside", () => {
    // It occupies no drawable height, so a block for it would be a zero-pixel
    // sliver at the very top of the column — visible enough to be confusing
    // and too small to read.
    const { before, inside } = outsiders([at(7 * 60, 8 * 60, "dawn")], ...WIN);
    assert.equal(before.length, 1);
    assert.equal(inside.length, 0);
  });

  it("loses nothing", () => {
    const input = [
      at(6 * 60, 7 * 60, "a"), at(600, 660, "b"),
      at(22 * 60, 23 * 60, "c"), at(19 * 60, 21 * 60, "d"),
    ];
    const { before, after, inside } = outsiders(input, ...WIN);
    assert.equal(before.length + after.length + inside.length, input.length);
  });

  it("orders each strip by start time", () => {
    const { after } = outsiders(
      [at(23 * 60, 23 * 60 + 30, "late"), at(21 * 60, 22 * 60, "early")], ...WIN);
    assert.deepEqual(after.map((e) => e.title), ["early", "late"]);
  });
});

describe("hueMap", () => {
  it("gives every calendar on this account its own hue", () => {
    const hues = hueMap(REAL);
    assert.equal(new Set(hues.values()).size, REAL.length);
  });

  it("does not depend on the order gcalcli listed them in", () => {
    const forward = hueMap(REAL);
    const backward = hueMap([...REAL].reverse());
    for (const name of REAL) assert.equal(forward.get(name), backward.get(name));
  });

  // Perfect stability and guaranteed distinctness cannot both hold. A colour
  // that never depends on the rest of the set is a pure hash, and a pure hash
  // collides — it did, twice, on these nine names. Distinctness wins, because a
  // repeated colour stops the legend answering the one question it exists for,
  // so what is pinned here is that the *churn* is tiny rather than absent. The
  // scheme this replaced assigned by list position and moved every calendar
  // after the one that changed.
  const churn = (before, after, names) =>
    names.filter((n) => after.has(n) && after.get(n) !== before.get(n));

  it("moves at most one other calendar when any one is deleted", () => {
    const before = hueMap(REAL);
    for (const dropped of REAL) {
      const after = hueMap(REAL.filter((n) => n !== dropped));
      const moved = churn(before, after, REAL);
      assert.ok(moved.length <= 1, `deleting ${dropped} moved ${moved.length}: ${moved}`);
      assert.equal(new Set(after.values()).size, REAL.length - 1);
    }
  });

  it("moves few or none when one is added", () => {
    const before = hueMap(REAL);
    for (const added of ["Thesis", "Research", "Travel", "Gym", "Zzz"]) {
      const after = hueMap([...REAL, added]);
      const moved = churn(before, after, REAL);
      assert.ok(moved.length <= 2, `adding ${added} moved ${moved.length}: ${moved}`);
      assert.equal(new Set(after.values()).size, REAL.length + 1);
    }
  });

  it("barely disturbs the others when one is renamed", () => {
    // A rename is a delete and an add at once, so it carries both bounds --
    // measured at 0, 1 and 2 depending on the new name.
    const before = hueMap(REAL);
    for (const to of ["Deep Work", "Food", "Dining", "Zebra"]) {
      const after = hueMap(REAL.map((n) => (n === "Meals" ? to : n)));
      const moved = churn(before, after, REAL);
      assert.ok(moved.length <= 2, `renaming to ${to} moved ${moved.length}: ${moved}`);
      assert.equal(new Set(after.values()).size, REAL.length);
    }
  });

  it("still answers when there are more calendars than hues", () => {
    const many = [...Array(20)].map((_, i) => `Calendar ${i}`);
    const hues = hueMap(many);
    assert.equal(hues.size, 20);
    assert.ok([...hues.values()].every((h) => Number.isFinite(h)));
  });

  it("never assigns gold, which means done everywhere else", () => {
    for (const hue of hueMap(REAL).values()) assert.ok(hue < 38 || hue > 58, `hue ${hue}`);
  });
});
