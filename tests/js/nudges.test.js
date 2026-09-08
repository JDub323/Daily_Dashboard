// What the reminder shelf does on a pass, and in particular what it takes back.
//
// The rules were only ever read at the moment of raising, which was fine while
// the laptop was the thing you did the day on: whatever it was reminding you
// about, you finished it here, and the write that finished it was the one that
// redrew the screen. The logging then moved to the phone, and the card stopped
// being merely stale — it became a sentence that was very often already false
// when it appeared and had no way of ever finding out. "lunch is not logged" is
// the one that gets read, at three in the afternoon, an hour after lunch was
// logged standing in a kitchen.
//
// So the interesting cases are all ones a browser will not produce on demand,
// which is why the decision is a pure function and lives here rather than in
// the shelf. `localStorage` is stubbed before the import, the same way the
// phone queue's test does it, because the repeat clock lives there.

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

const store_ = new Map();
globalThis.localStorage = {
  getItem: (k) => (store_.has(k) ? store_.get(k) : null),
  setItem: (k, v) => store_.set(k, v),
  removeItem: (k) => store_.delete(k),
};

const { verdicts, wantsTodo } = await import("../../web/app/nudges.js");
const { store } = await import("../../web/app/store.js");
// The same "today" the module under test reads, rather than a second spelling
// of it — see the note in the `wantsTodo` case below, which is what a second
// spelling cost.
const api = await import("../../web/app/api.js");

/** The day, as the rules read it. `tasks` is `chipTasks()`'s shape — id, label,
 *  `points` for the optional layer, `done`. */
function ctx({ hour = 15, meals = 2, tasks = [], todo = null } = {}) {
  store.state = { health: { meals_today: Array.from({ length: meals }, (_, i) => ({ id: i })) } };
  return { now: new Date(), hour, tasks, todo };
}

const done = (id) => ({ id, label: id, done: true });
const owed = (id) => ({ id, label: id, done: false });

const on = (list, id) => list.find((v) => v.id === id);

beforeEach(() => store_.clear());

describe("verdicts", () => {
  it("raises the meal that is missing, by name", () => {
    // Two meals in and it is three in the afternoon: breakfast and lunch are
    // accounted for, dinner is not late yet.
    const out = verdicts(ctx({ hour: 15, meals: 1 }));
    assert.equal(on(out, "meal-lunch")?.text, "lunch is not logged");
    assert.equal(on(out, "meal-breakfast"), undefined);
    assert.equal(on(out, "meal-dinner"), undefined);
  });

  it("says nothing at all when the meal is logged", () => {
    assert.equal(on(verdicts(ctx({ hour: 15, meals: 2 })), "meal-lunch"), undefined);
  });

  it("takes back a card the phone has answered", () => {
    // The whole feature. The card went up at two with one meal logged; the
    // second arrived from the phone at half past. Nothing about the clock has
    // changed and the rule's repeat interval is 24 hours, so the only way this
    // is ever looked at again is because a card is up.
    const out = verdicts(ctx({ hour: 15, meals: 2 }), ["meal-lunch"]);
    assert.deepEqual(on(out, "meal-lunch"), { id: "meal-lunch", do: "settle" });
  });

  it("leaves a card up while it is still true", () => {
    const out = verdicts(ctx({ hour: 15, meals: 1 }), ["meal-lunch"]);
    assert.equal(on(out, "meal-lunch").do, "update");
  });

  it("does not settle a card the clock would not have raised", () => {
    // A card up for a rule whose hour has not come yet is still evaluated —
    // that is the point — but a rule with no card and nothing to say produces
    // no verdict at all, rather than a settle for a card that is not there.
    const out = verdicts(ctx({ hour: 15, meals: 3 }));
    assert.equal(out.some((v) => v.do === "settle"), false);
  });

  it("re-reads a rule with a card up whatever its repeat clock says", () => {
    // `every` is what stops a reminder being repeated. It must not stop one
    // being withdrawn: the check-in rule may only speak every two hours, and a
    // check-in answered on the phone one minute after the card went up should
    // strike it out then and not in two hours.
    const undone = verdicts(ctx({ hour: 11, tasks: [owed("checkin")] }), ["checkin"]);
    assert.equal(on(undone, "checkin").do, "update");
    const answered = verdicts(ctx({ hour: 11, tasks: [done("checkin")] }), ["checkin"]);
    assert.equal(on(answered, "checkin").do, "settle");
  });

  it("moves the count on a card rather than raising a second one", () => {
    const three = [owed("a"), owed("b"), owed("c")];
    const first = verdicts(ctx({ hour: 21, tasks: three }));
    assert.equal(on(first, "required").do, "raise");
    assert.match(on(first, "required").text, /^3 required left/);

    const two = [done("a"), owed("b"), owed("c")];
    const next = verdicts(ctx({ hour: 21, tasks: two }), ["required"]);
    assert.equal(on(next, "required").do, "update");
    assert.match(on(next, "required").text, /^2 required left/);
  });

  it("settles the required card when the day is finished", () => {
    const out = verdicts(ctx({ hour: 21, tasks: [done("a"), done("b")] }), ["required"]);
    assert.equal(on(out, "required").do, "settle");
  });

  it("says nothing about the due list it could not fetch", () => {
    // `null` is a failed fetch and `[]` is an empty day, and they were the same
    // value until a card could be taken down. Striking out "2 overdue" because
    // the server was busy is the one direction this must never fail in.
    const late = [{ title: "taxes", done: false, due_date: "2000-01-01" }];
    assert.equal(on(verdicts(ctx({ hour: 9, todo: late }), []), "overdue").do, "raise");
    assert.equal(on(verdicts(ctx({ hour: 9, todo: null }), ["overdue"]), "overdue"), undefined);
    assert.equal(on(verdicts(ctx({ hour: 9, todo: [] }), ["overdue"]), "overdue").do, "settle");
  });
});

describe("wantsTodo", () => {
  it("does not fetch the due list before the rules that need it", () => {
    assert.equal(wantsTodo({ hour: 6 }, []), false);
  });

  it("fetches it once a rule could fire", () => {
    assert.equal(wantsTodo({ hour: 9 }, []), true);
  });

  it("fetches it for a card that is up, however quiet the rule has gone", () => {
    // The retraction path needs the same input the raise did. Without this the
    // due-today card — which speaks once a day — could never be taken down.
    //
    // `api.today()` and not `toISOString().slice(0, 10)`, which is what this
    // was and is a UTC date. `memory()` in nudges.js keys on the *local* one
    // and throws away a record dated anything else, so between 8pm and
    // midnight Eastern the seeded timings were silently discarded, every rule
    // read as never-shown, and this assertion — that a rule which has already
    // spoken is quiet — failed. The window it failed in is the window the app
    // is used in, which is the only reason it was ever seen.
    //
    // [Invariant 1]: the browser owns "today" and it is local. A test that
    // spells that itself is a test free to spell it differently.
    store_.set("nudges", JSON.stringify({
      date: api.today(),
      at: { "due-today": Date.now(), overdue: Date.now() },
    }));
    assert.equal(wantsTodo({ hour: 9 }, []), false);
    assert.equal(wantsTodo({ hour: 9 }, ["due-today"]), true);
  });
});
