// What the poll asks for, and which word it puts on the request.
//
// The word is the whole of this file. `beat` used to take no arguments, so
// `window.addEventListener("focus", beat)` was safe; it takes one now, and the
// listener is handed an Event. Wired the old way the focus poll would send
// `beat=[object Event]`, the server would refuse it 422, and the symptom is a
// dashboard that goes on polling and silently never fetches the watch again —
// which looks exactly like the bug the parameter was added to fix.
//
// So: no DOM here, only the three globals `pulse.js` touches, stubbed. The
// point is what comes out the other end as a URL.

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

const asked = [];

globalThis.document = {
  hidden: false,
  listeners: {},
  addEventListener(name, fn) {
    this.listeners[name] = fn;
  },
};
globalThis.window = {
  listeners: {},
  addEventListener(name, fn) {
    this.listeners[name] = fn;
  },
};
globalThis.fetch = async (path) => {
  asked.push(path);
  return { ok: true, json: async () => ({ meals_today: [] }) };
};

// The backstop timer, held rather than started. A live thirty-second interval
// keeps node's loop open and the test run never exits.
let ticker = null;
globalThis.setInterval = (fn) => {
  ticker = fn;
  return 1;
};
globalThis.clearInterval = () => {
  ticker = null;
};

/** Let a listener's un-awaited `beat` finish. The handlers are `void`-shaped —
 *  that is what an event listener is — so awaiting the call awaits nothing, and
 *  `beating` would swallow the next one as a re-entrant poll. */
const settle = () => new Promise((r) => setTimeout(r, 0));

const { beat, initPulse } = await import("../../web/app/pulse.js");
const { store } = await import("../../web/app/store.js");

/** The date the store is on, which `beat` refuses to poll across. */
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
    + `-${String(d.getDate()).padStart(2, "0")}`;
};

const reasonOf = (url) => new URL(url, "http://x").searchParams.get("beat");

beforeEach(() => {
  asked.length = 0;
  document.hidden = false;
  store.state = { date: today(), health: null };
});

describe("beat", () => {
  it("names itself as the unattended poll by default", async () => {
    await beat();
    assert.equal(asked.length, 1);
    assert.equal(reasonOf(asked[0]), "beat");
  });

  it("carries the reason it was given", async () => {
    await beat("focus");
    assert.equal(reasonOf(asked[0]), "focus");
  });

  it("still sends the browser's own date", async () => {
    await beat();
    assert.equal(new URL(asked[0], "http://x").searchParams.get("date"), today());
  });

  it("asks nothing while the tab is hidden", async () => {
    document.hidden = true;
    await beat();
    assert.equal(asked.length, 0);
  });

  it("asks nothing before the store has a day", async () => {
    store.state = null;
    await beat();
    assert.equal(asked.length, 0);
  });

  it("does not poll across the midnight rollover", async () => {
    store.state = { date: "2000-01-01", health: null };
    await beat();
    assert.equal(asked.length, 0);
  });
});

describe("initPulse", () => {
  it("says focus, not the event object, on both ways back to the window", async () => {
    // The regression this file exists for. Both listeners, because they are
    // not the same event: `visibilitychange` is a tab switch, `focus` is an
    // alt-tab between two windows — and the second is the common one here.
    initPulse();
    document.listeners.visibilitychange({ type: "visibilitychange" });
    await settle();
    window.listeners.focus({ type: "focus" });
    await settle();
    assert.deepEqual(asked.map(reasonOf), ["focus", "focus"]);
  });

  it("leaves the unattended beat saying beat", async () => {
    initPulse();
    ticker();
    await settle();
    assert.deepEqual(asked.map(reasonOf), ["beat"]);
  });
});
