// Which x-of-the-day cells a boot asks for.
//
// `missing` is the guard between "gather everything at startup" and the two
// endpoints that are not idempotent. `/api/chess/new` upserts the day's
// attempt, so gathering a puzzle that is already open swaps it for a different
// one — and keeps the rating the first was opened against. The theorem draw
// stamps a card as seen and puts it on cooldown, so an unguarded gather burns
// one a page load rather than one a day.
//
// It runs on every boot and every rollover, which makes it exactly the sort of
// thing that is checked here rather than by opening the page and hoping.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { missing } from "../../web/app/daily.js";

/** A boot payload with only the fields `missing` reads. Empty day by default,
 *  with tonight's trick file on disk. */
function state(overrides = {}) {
  return {
    date: "2026-08-15",
    word: null,
    spelling: { enabled: true, words: [] },
    theorems: { deck_size: 12, today: null },
    chess: { puzzle_id: null, summary: { rating: 1467 } },
    tricks: { enabled: true, trick: null, batched: true },
    ...overrides,
  };
}

describe("missing", () => {
  it("asks for everything on an untouched day", () => {
    assert.deepEqual(missing(state()), ["word", "spelling", "theorems", "chess", "tricks"]);
  });

  it("asks for nothing when the day is already gathered", () => {
    // The reload case, and the one that matters: opening a second tab at noon
    // must not reroll the puzzle or redraw the card.
    const gathered = state({
      word: { word: "susurrus" },
      spelling: { enabled: true, words: [{ word: "liaison" }] },
      theorems: { deck_size: 12, today: { id: 3 } },
      chess: { puzzle_id: "abc12", summary: { rating: 1467 } },
      tricks: { enabled: true, trick: { name: "Telescoping" }, batched: true },
    });
    assert.deepEqual(missing(gathered), []);
  });

  it("leaves an open chess puzzle alone", () => {
    const open = state({ chess: { puzzle_id: "abc12", summary: { rating: 1467 } } });
    assert.ok(!missing(open).includes("chess"));
  });

  it("skips the bee when it is not configured", () => {
    // Unconfigured is not a failure to report — the panel says to look at
    // config.toml — so asking anyway would put an error under that line.
    const off = state({ spelling: { enabled: false, words: [] } });
    assert.deepEqual(missing(off), ["word", "theorems", "chess", "tricks"]);
  });

  it("skips the theorem draw when the deck is empty", () => {
    // Same reason: the panel's real answer is "add a theorem below".
    const empty = state({ theorems: { deck_size: 0, today: null } });
    assert.deepEqual(missing(empty), ["word", "spelling", "chess", "tricks"]);
  });

  it("does not ask for a trick the 3am task never wrote", () => {
    // The trick has no pool behind it and nothing paid: with no file there is
    // nothing `/api/trick/new` can do but 502, and a page load should not put
    // that under a panel whose real answer is "the overnight task did not run".
    const missed = state({ tricks: { enabled: true, trick: null, batched: false } });
    assert.ok(!missing(missed).includes("tricks"));
  });

  it("skips the trick when it is not configured", () => {
    const off = state({ tricks: { enabled: false, trick: null, batched: true } });
    assert.ok(!missing(off).includes("tricks"));
  });

  it("still asks for a word whose sentences are unwritten", () => {
    // Gathered means *drawn*, not done. Only the absence of the row is a
    // reason to generate, or a morning's unfinished sentences would be paid
    // for twice.
    const drawn = state({ word: { word: "susurrus", sentences: [], grade: null } });
    assert.ok(!missing(drawn).includes("word"));
  });
});
