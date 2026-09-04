// The phone's memory of what the laptop last said.
//
// The bug this covers cannot be seen from inside the app on wifi, which is why
// it survived: log a meal, send it, and the row is on screen either way — from
// the queue before the send and from the server's answer after it. It is only
// on the *third* state, the one the laptop is not around for, that the two come
// apart, and until `held.js` the row simply vanished there. Sending a day's log
// and then walking away from the laptop emptied the only screen that could
// answer "what have I already logged?".
//
// So these are tests about a day's rows surviving three things: the send, the
// app being closed, and midnight.
//
// `localStorage` is stubbed onto `globalThis` before the module is imported,
// the same way `mqueue.test.js` does it and for the same reason — storage is
// read at call time, so the stub only has to be in place first.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

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
  removeItem: (k) => {
    if (storageThrows) throw new Error("denied");
    store.delete(k);
  },
};

const { forget, recall, remember } = await import("../../web/m/held.js");

const KEY = "dashboard.m.panel";

/** A panel, as thin as `isPanel` will accept — the key is what makes it the
 *  dashboard's answer rather than something else wearing its status code. */
const panel = (meals = []) => ({ meals_today: meals, drinks_today: [] });

const meal = { id: 4, hour: 13, size: "large", quality: 4 };

beforeEach(() => {
  store.clear();
  storageThrows = false;
});

describe("the laptop's last answer", () => {
  it("hands back the day it was given", () => {
    remember(panel([meal]), "2026-09-04");
    const back = recall("2026-09-04");
    assert.deepEqual(back.panel.meals_today, [meal]);
  });

  it("says when it was heard, because the rows in it can be stale", () => {
    // The far half of the list — anything logged or deleted on the laptop —
    // stops being true the moment the two stop speaking, and this stamp is the
    // only thing on the screen that can say how long ago that was.
    remember(panel(), "2026-09-04");
    const at = recall("2026-09-04").at;
    assert.equal(typeof at, "string");
    assert.ok(Math.abs(Date.now() - Date.parse(at)) < 5000, `stamp is not now: ${at}`);
  });

  it("survives the send", () => {
    // The actual regression. The queue is empty after a flush, so from this
    // point the screen has no other source for the meal: if the panel is not
    // kept, walking away from the laptop is what deletes the day from view.
    remember(panel([meal]), "2026-09-04");
    assert.deepEqual(recall("2026-09-04").panel.meals_today, [meal]);
  });

  it("will not hand yesterday's rows to today, and throws them away", () => {
    // Worse than showing nothing. Every row on this screen has a delete button
    // beside it, so last night's dinner under today's heading is a tap away
    // from being deleted out of the day it actually happened on.
    remember(panel([meal]), "2026-09-03");
    assert.equal(recall("2026-09-04"), null);
    assert.equal(store.has(KEY), false, "the stale day was left in storage");
  });

  it("refuses a body that is not the dashboard's, the way the queue does", () => {
    // A captive portal answers everything on the network with a 200 and a page
    // of its own. `load` already checks the shape before adopting one; this is
    // the second gate, for a value that got into storage some other way — and
    // adopting it would draw "nothing eaten yet today" over a day of meals.
    store.set(KEY, JSON.stringify({ date: "2026-09-04", at: null, panel: { login: true } }));
    assert.equal(recall("2026-09-04"), null);
    assert.equal(store.has(KEY), false);
  });

  it("throws away what it cannot read", () => {
    store.set(KEY, "{ this is not json");
    assert.equal(recall("2026-09-04"), null);
  });

  it("shrugs when storage will not take it", () => {
    // The one place this file differs from `queue.js` on purpose. There, a
    // failed write is a lost meal and is reported to the screen. Here it is a
    // lost *cache* of rows the database still holds, so it is a false return
    // and nothing else — the app carries on with the panel in memory.
    storageThrows = true;
    assert.equal(remember(panel([meal]), "2026-09-04"), false);
    storageThrows = false;
    assert.equal(recall("2026-09-04"), null);
  });

  it("keeps one day and not a history", () => {
    remember(panel([meal]), "2026-09-03");
    remember(panel(), "2026-09-04");
    assert.equal(store.size, 1);
  });

  it("forgets on demand", () => {
    remember(panel([meal]), "2026-09-04");
    forget();
    assert.equal(recall("2026-09-04"), null);
  });
});

// --- and that the app actually uses it ---------------------------------------
//
// A source-level check, in the spirit of `shell.test.js`: the module above can
// be perfect and the screen still empty, because the failure is not in the
// storage, it is in `app.js` holding the panel in a `let` and nowhere else.
// That is what it did for as long as the app existed, and nothing about it
// looks wrong when you read it.

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const app = fs.readFileSync(path.join(ROOT, "web", "m", "app.js"), "utf8");

describe("the phone's day", () => {
  it("is on screen before the network is", () => {
    assert.match(
      app,
      /let panel = remembered\?\.panel \?\? null;/,
      "app.js no longer seeds the panel from held.js, so the day is gone again "
        + "the moment it is sent",
    );
  });

  it("routes every fresh panel through the one funnel", () => {
    // `adopt` is what keeps the stored copy, the stamp and the screen in step.
    // An assignment that goes around it puts rows on screen that will not be
    // there after a reload, with a timestamp that describes something else.
    const assignments = [...app.matchAll(/^\s*(?:if \([^)]*\) )?(?:let )?panel = (.+?);/gm)]
      .map((m) => m[1].trim());
    assert.deepEqual(
      assignments.sort(),
      ["fresh", "null", "remembered?.panel ?? null"],
      "something assigns `panel` outside `adopt` — see held.js",
    );
  });
});
