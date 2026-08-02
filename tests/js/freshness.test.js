// When a live panel asks for its feed again.
//
// `refreshAfter` is pure so the schedule can be checked here rather than by
// leaving the dashboard open and watching the network tab for ten minutes.
// (`feedStamp` is not tested here — it builds DOM nodes, and there is no
// document under `node --test`.)

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { refreshAfter } from "../../web/app/freshness.js";

const NOW = Date.parse("2026-08-02T12:00:00Z");
const feed = (secondsAgo, ttl_seconds = 600) => ({
  fetched_at: new Date(NOW - secondsAgo * 1000).toISOString(),
  ttl_seconds,
  stale: false,
});

describe("refreshAfter", () => {
  it("waits out the rest of the server's TTL, plus a little", () => {
    // Fetched 100s into a 600s window: the useful moment is 500s away, not a
    // further 600. Polling before the server's copy expires would only get
    // the same cached answer handed back.
    assert.equal(refreshAfter(feed(100), NOW), 505_000);
  });

  it("schedules from the data's age, not from when the panel mounted", () => {
    // The bug this shape avoids: a panel mounted at the end of a TTL window
    // would sit on data that is already expired for another full window.
    const late = refreshAfter(feed(590), NOW);
    const early = refreshAfter(feed(10), NOW);
    assert.ok(late < early, `${late} should be sooner than ${early}`);
    assert.equal(late, 60_000); // the floor, not 15_000
  });

  it("never polls tighter than a minute", () => {
    // A long-dead source: the age exceeds the TTL, so the arithmetic goes
    // negative and the floor is what stops a retry loop.
    assert.equal(refreshAfter(feed(86_400), NOW), 60_000);
  });

  it("falls back to a sane interval when the response carries no feed block", () => {
    assert.equal(refreshAfter(undefined, NOW), 305_000);
    assert.equal(refreshAfter(null, NOW), 305_000);
  });

  it("honours a short TTL from config without going under the floor", () => {
    // config.toml owns the cache windows; this is the only place the client
    // has an opinion, and it is a floor rather than a number of its own.
    assert.equal(refreshAfter(feed(0, 30), NOW), 60_000);
    assert.equal(refreshAfter(feed(0, 900), NOW), 905_000);
  });
});
