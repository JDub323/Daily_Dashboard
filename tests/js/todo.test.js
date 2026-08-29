// How the due list buckets what it is given.
//
// The one piece of this panel that is arithmetic rather than markup, and it is
// the piece that goes wrong invisibly: a grouping bug does not throw, it just
// quietly files a late reading under a future day where it will never be
// looked at again. The rest builds DOM nodes and there is no document under
// `node --test`, so it is exercised in the browser instead.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { groupByDay, shorten, undatedItems } from "../../web/app/panels/todo.js";

const TODAY = "2026-08-27";

let seq = 0;
const item = (due, extra = {}) => ({
  id: ++seq,
  title: `t${seq}`,
  due_date: due,
  done: false,
  ...extra,
});

describe("groupByDay", () => {
  it("puts everything late into one bucket at the front", () => {
    const groups = groupByDay(
      [item("2026-08-20"), item("2026-08-28"), item("2026-08-25")],
      TODAY,
    );
    assert.equal(groups[0].overdue, true);
    assert.equal(groups[0].date, null);
    // Two separate late days collapse together rather than keeping a heading
    // each — a backlog of work, not a backlog of dates.
    assert.deepEqual(groups[0].items.map((i) => i.due_date), ["2026-08-20", "2026-08-25"]);
    assert.equal(groups.length, 2);
  });

  it("counts today as due, not as late", () => {
    const [group] = groupByDay([item(TODAY)], TODAY);
    assert.equal(group.overdue, false);
    assert.equal(group.date, TODAY);
  });

  it("orders the future forwards", () => {
    const groups = groupByDay(
      [item("2026-09-04"), item("2026-08-28"), item("2026-08-31")],
      TODAY,
    );
    assert.deepEqual(groups.map((g) => g.date), ["2026-08-28", "2026-08-31", "2026-09-04"]);
  });

  it("keeps several items due the same day in one group", () => {
    const groups = groupByDay([item("2026-08-28"), item("2026-08-28")], TODAY);
    assert.equal(groups.length, 1);
    assert.equal(groups[0].items.length, 2);
  });

  it("leaves finished work out of the day groups entirely", () => {
    // It is still in the payload — the panel shows it behind a disclosure —
    // but a ticked item must not keep occupying a day, least of all the
    // overdue bucket, which is the one place that has to stay honest.
    const groups = groupByDay(
      [item("2026-08-20", { done: true }), item("2026-08-28", { done: true })],
      TODAY,
    );
    assert.deepEqual(groups, []);
  });

  it("returns nothing rather than an empty overdue bucket", () => {
    assert.deepEqual(groupByDay([], TODAY), []);
  });
});

describe("undatedItems", () => {
  it("picks out the rows with no day at all", () => {
    const items = [item("2026-08-28"), item(null), item(null)];
    assert.deepEqual(undatedItems(items).map((i) => i.due_date), [null, null]);
  });

  it("leaves undated work out of the day groups", () => {
    // The two lists are drawn from one payload, so an undated row appearing in
    // both would be the same item shown twice — and `null < "2026-08-27"` is
    // false, so without the explicit skip it becomes a day group headed "null".
    const groups = groupByDay([item(null), item("2026-08-28")], TODAY);
    assert.deepEqual(groups.map((g) => g.date), ["2026-08-28"]);
  });

  it("drops undated work that is already done", () => {
    assert.deepEqual(undatedItems([item(null, { done: true })]), []);
  });
});

describe("shorten", () => {
  const FPM = "US National Security Policy Making";

  it("takes the subject off the front of its own title", () => {
    // The alias expands before the title is stored, so the subject *is* the
    // first words of nearly every row — and the tag beside it said them again.
    assert.equal(shorten(`${FPM}: quiz on the Civil War`, FPM), "Quiz on the Civil War");
  });

  it("eats the separator, whatever it is", () => {
    for (const sep of [": ", " ", " — ", " - "]) {
      assert.equal(shorten(`${FPM}${sep}Thornhill Chapter 4`, FPM), "Thornhill Chapter 4");
    }
  });

  it("leaves a title that is only its subject alone", () => {
    // An empty row is worse than a repeated one.
    assert.equal(shorten(FPM, FPM), FPM);
  });

  it("only ever strips a prefix", () => {
    assert.equal(shorten(`Notes on ${FPM}`, FPM), `Notes on ${FPM}`);
  });

  it("does nothing without a subject", () => {
    assert.equal(shorten("Fix the coffee table", null), "Fix the coffee table");
  });
});
