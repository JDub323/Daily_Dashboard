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

const reply = (status, body = {}) => ({
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
    assert.deepEqual(await flush(), { meals_today: [2] });
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

  it("survives storage it cannot write to", () => {
    storageThrows = true;
    assert.doesNotThrow(() => enqueue("/api/meal", { date: "d", hour: 1 }, "meal"));
  });
});
