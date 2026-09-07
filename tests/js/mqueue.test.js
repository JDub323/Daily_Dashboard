// The phone log's write queue.
//
// The one piece of `/m` that is arithmetic rather than markup, and the piece
// whose failures are silent: a queue that drops a write loses a meal you
// remember logging, and a queue that keeps a doomed one at its head blocks
// everything logged after it. Neither throws.
//
// `localStorage` and `fetch` are both stubbed onto `globalThis` before the
// module is imported, because it reads storage at call time but binds `fetch`
// per call — so the stub has to be in place before the first `flush`.

import assert from "node:assert/strict";
import { beforeEach, describe, it, mock } from "node:test";

// A localStorage that behaves like the real one, including throwing on demand.
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

const { enqueue, flush, pending, pendingOn } = await import("../../web/m/queue.js");

/** A reply from the dashboard. The body has to carry a panel key: `flush`
 *  treats a 2xx that is not the panel as somebody else answering. */
const reply = (status, body = { meals_today: [] }) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

beforeEach(() => {
  store.clear();
  storageThrows = false;
});

describe("the write queue", () => {
  it("keeps what it is given, in order", () => {
    enqueue("/api/meal", { date: "2026-08-28", hour: 9 }, "meal");
    enqueue("/api/drink", { date: "2026-08-28", hour: 10 }, "drink");
    assert.deepEqual(pending().map((e) => e.path), ["/api/meal", "/api/drink"]);
  });

  it("carries the hour the entry was logged at, not the hour it is sent at", async () => {
    // The whole feature. A meal logged at 13:00 and flushed at 21:00 lands at
    // 13:00, because the body says so and the server never asks its own clock.
    enqueue("/api/meal", { date: "2026-08-28", hour: 13 }, "meal");
    const sent = [];
    globalThis.fetch = mock.fn(async (path, init) => {
      sent.push(JSON.parse(init.body));
      return reply(200);
    });
    await flush();
    assert.equal(sent[0].hour, 13);
    assert.equal(sent[0].date, "2026-08-28");
  });

  it("drains oldest first and empties on success", async () => {
    enqueue("/api/meal", { date: "d", hour: 1 }, "a");
    enqueue("/api/meal", { date: "d", hour: 2 }, "b");
    const hours = [];
    globalThis.fetch = mock.fn(async (_p, init) => {
      hours.push(JSON.parse(init.body).hour);
      return reply(200);
    });
    await flush();
    assert.deepEqual(hours, [1, 2]);
    assert.deepEqual(pending(), []);
  });

  it("stops at the first unreachable write and keeps the rest", async () => {
    // Two meals and a dead server. Draining the others just to watch them fail
    // turns one dead connection into three, and loses the ordering besides.
    enqueue("/api/meal", { date: "d", hour: 1 }, "a");
    enqueue("/api/meal", { date: "d", hour: 2 }, "b");
    let calls = 0;
    globalThis.fetch = mock.fn(async () => {
      calls += 1;
      throw new TypeError("offline");
    });
    await flush();
    assert.equal(calls, 1);
    assert.equal(pending().length, 2);
  });

  it("drops a write the server has refused, and says so", async () => {
    // A 4xx will be a 4xx in an hour too, and an entry that can never be sent
    // sits at the head of the queue blocking everything behind it.
    enqueue("/api/meal", { date: "d", hour: 1, quality: 99 }, "meal");
    enqueue("/api/meal", { date: "d", hour: 2 }, "good meal");
    globalThis.fetch = mock.fn(async (_p, init) =>
      reply(JSON.parse(init.body).quality === 99 ? 422 : 200));

    const refused = [];
    await flush({ onRefused: (entry, status) => refused.push([entry.label, status]) });

    assert.deepEqual(refused, [["meal", 422]]);
    assert.deepEqual(pending(), []);
  });

  it("keeps a write the server merely fumbled", async () => {
    // A 500 is the server having a bad moment, not a bad body.
    enqueue("/api/meal", { date: "d", hour: 1 }, "meal");
    globalThis.fetch = mock.fn(async () => reply(500));
    await flush();
    assert.equal(pending().length, 1);
  });

  it("hands back the last panel the server sent", async () => {
    enqueue("/api/meal", { date: "d", hour: 1 }, "a");
    enqueue("/api/meal", { date: "d", hour: 2 }, "b");
    let n = 0;
    globalThis.fetch = mock.fn(async () => reply(200, { meals_today: [++n] }));
    assert.deepEqual((await flush()).panel, { meals_today: [2] });
  });

  it("filters to one day", () => {
    enqueue("/api/meal", { date: "2026-08-28", hour: 1 }, "today");
    enqueue("/api/meal", { date: "2026-08-27", hour: 1 }, "yesterday");
    assert.deepEqual(pendingOn("2026-08-28").map((e) => e.label), ["today"]);
  });

  it("treats unreadable storage as an empty queue rather than throwing", () => {
    // A queue that throws on read is a log that will not open, and the writes
    // it was protecting are already lost either way.
    storageThrows = true;
    assert.deepEqual(pending(), []);
  });

  it("survives storage it cannot write to, and says the write did not stick", () => {
    // It used to return the entry regardless, on a comment claiming the caller
    // still held it. Nothing holds it — every read goes back to storage — so
    // the entry was gone and the screen drew a log that had lost a row.
    storageThrows = true;
    assert.doesNotThrow(() => enqueue("/api/meal", { date: "d", hour: 1 }, "meal"));
    storageThrows = true;
    assert.equal(enqueue("/api/meal", { date: "d", hour: 1 }, "meal"), null);
  });

  // --- 200 is not delivery --------------------------------------------------
  //
  // The bug that cost a day of meals. A captive portal answers every request on
  // the network with 200 and a login page; `flush` read that as the write
  // having landed, dropped the entry, and the button said "sent".

  it("keeps the writes when something that is not the dashboard answers 200", async () => {
    enqueue("/api/meal", { date: "d", hour: 8 }, "meal");
    enqueue("/api/meal", { date: "d", hour: 12 }, "meal");
    globalThis.fetch = mock.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError("Unexpected token '<'"); },
    }));

    const result = await flush();

    assert.equal(result.sent, 0);
    assert.equal(result.stopped, "intercepted");
    assert.equal(pending().length, 2, "both meals are still owed");
  });

  it("is not fooled by a portal that answers 200 with json of its own", async () => {
    enqueue("/api/meal", { date: "d", hour: 8 }, "meal");
    globalThis.fetch = mock.fn(async () => reply(200, { status: "ok", redirect: "/login" }));

    const result = await flush();

    assert.equal(result.sent, 0);
    assert.equal(result.stopped, "intercepted");
    assert.equal(pending().length, 1);
  });

  it("stops at the interception rather than offering the rest of the queue to it", async () => {
    enqueue("/api/meal", { date: "d", hour: 1 }, "a");
    enqueue("/api/meal", { date: "d", hour: 2 }, "b");
    enqueue("/api/meal", { date: "d", hour: 3 }, "c");
    let calls = 0;
    globalThis.fetch = mock.fn(async () => {
      calls += 1;
      return { ok: true, status: 200, json: async () => "<html>" };
    });
    await flush();
    assert.equal(calls, 1);
    assert.equal(pending().length, 3);
  });

  // --- counting, rather than subtracting ------------------------------------

  it("counts what it sent, so a write logged mid-flush cannot make it read zero", async () => {
    // The screen used to report `pending()` before minus after. A meal logged
    // while the flush was in flight -- normal on a slow tailnet -- made that
    // difference zero, and a successful send reported itself as "nothing sent".
    enqueue("/api/meal", { date: "d", hour: 1 }, "a");
    globalThis.fetch = mock.fn(async () => {
      enqueue("/api/meal", { date: "d", hour: 2 }, "logged while sending");
      return reply(200);
    });

    const result = await flush();

    assert.equal(result.sent, 1);
    assert.equal(pending().length, 1, "the one logged mid-flush is still owed");
    assert.equal(pending()[0].label, "logged while sending");
  });

  it("says which day the panel it hands back describes", async () => {
    // A flush on Wednesday can end on Tuesday's last meal, and the panel that
    // comes back is Tuesday's. Drawing it under today's heading is yesterday's
    // rows presented as today's.
    enqueue("/api/meal", { date: "2026-08-30", hour: 20 }, "yesterday");
    globalThis.fetch = mock.fn(async () => reply(200));
    assert.equal((await flush()).panelDate, "2026-08-30");
  });

  it("names why it stopped", async () => {
    enqueue("/api/meal", { date: "d", hour: 1 }, "meal");
    globalThis.fetch = mock.fn(async () => { throw new TypeError("offline"); });
    assert.equal((await flush()).stopped, "unreachable");

    globalThis.fetch = mock.fn(async () => reply(500));
    assert.equal((await flush()).stopped, "server");

    globalThis.fetch = mock.fn(async () => reply(200));
    assert.equal((await flush()).stopped, null);
  });
});

// --- the day screen's two writes ---------------------------------------------
//
// `/api/todo/done` and `/api/calendar/add` go through the same queue the five
// logs do and answer with entirely different bodies -- a `TodoPanel` and a
// `CalendarWrite`. Before this, `isPanel` was the only test of delivery, so
// both would have been read as a portal answering and the queue would have
// stuck at the first one forever.
//
// The tests below are about the *narrowness* of the new check, which is the
// only property that matters here: the reason a 200 is not delivery is that a
// captive portal produces one, and a check that accepts three shapes is three
// times as easy to satisfy by accident.

const TODO_PANEL = { items: [], aliases: [], default_calendar: "Homework", todo_calendars: [] };
const CAL_WRITE = { written: [], failed: [], events: 0 };

describe("what proves a day-screen write was delivered", () => {
  it("takes the todo panel for a tick, and the calendar write for an add", async () => {
    enqueue("/api/todo/done", { today: "2026-09-07", todo_id: 4, done: true }, "tick");
    enqueue("/api/calendar/add", { today: "2026-09-07", text: "todo laundry" }, "todo");
    globalThis.fetch = mock.fn(async (path) =>
      reply(200, path === "/api/todo/done" ? TODO_PANEL : CAL_WRITE));

    const out = await flush();
    assert.equal(out.sent, 2);
    assert.equal(pending().length, 0);
    // Neither is a day's rows, so neither is adopted as one. A `TodoPanel` read
    // as a health panel draws an empty meal list over a day of meals.
    assert.equal(out.panel, null);
    assert.equal(out.panelDate, null);
  });

  it("refuses a health panel offered as proof of a tick", async () => {
    // The interesting direction. Every one of these paths is on the same host
    // and a body that satisfies *some* endpoint is not a body that satisfies
    // this one -- the check is per path, not "any shape the app knows".
    enqueue("/api/todo/done", { today: "2026-09-07", todo_id: 4, done: true }, "tick");
    globalThis.fetch = mock.fn(async () => reply(200, { meals_today: [] }));

    const out = await flush();
    assert.equal(out.sent, 0);
    assert.equal(out.stopped, "intercepted");
    assert.equal(pending().length, 1, "the tick is kept, not destroyed");
  });

  it("wants every key of a todo panel, not any one of them", async () => {
    // `{ items: [...] }` alone is a shape a portal can produce by coincidence.
    // The alias table and the calendar defaults beside it are the dashboard.
    enqueue("/api/todo/done", { today: "2026-09-07", todo_id: 4, done: true }, "tick");
    globalThis.fetch = mock.fn(async () => reply(200, { items: [] }));
    assert.equal((await flush()).stopped, "intercepted");

    globalThis.fetch = mock.fn(async () => reply(200, TODO_PANEL));
    assert.equal((await flush()).sent, 1);
  });

  it("keeps a tick out of the log screens' view of the day", () => {
    // `pendingOn` is keyed on `body.date`, and these carry `today` instead --
    // the request models forbid unknown fields. A ticked todo is not a row on
    // any log and has no business in a meal list.
    enqueue("/api/meal", { date: "2026-09-07", hour: 9 }, "meal");
    enqueue("/api/todo/done", { today: "2026-09-07", todo_id: 4, done: true }, "tick");
    assert.deepEqual(pendingOn("2026-09-07").map((e) => e.path), ["/api/meal"]);
    assert.equal(pending().length, 2, "it is still owed to the server");
  });

  it("falls back to the strictest proof for a path it does not know", async () => {
    // An entry queued by an older build, or a path added without a shape. The
    // safe reading of "I cannot name what proves this" is the narrowest one.
    enqueue("/api/something/new", { date: "2026-09-07" }, "?");
    globalThis.fetch = mock.fn(async () => reply(200, CAL_WRITE));
    assert.equal((await flush()).stopped, "intercepted");
  });
});
