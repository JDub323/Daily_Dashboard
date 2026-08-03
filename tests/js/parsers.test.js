// Share-text parsers. Run with `npm test` — no npm dependencies to install.
//
// These are pure functions precisely so they can be tested like this.
// Verifying them used to mean pasting text into a modal and watching what
// happened to the trophy grid.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parsePokedoku, parseShare, parseWordle } from "../../web/app/parsers.js";

const WORDLE_SHARE = `Wordle 1,861 4/6

⬛🟨⬛⬛⬛
🟩⬛⬛🟨⬛
🟩🟩⬛⬛🟨
🟩🟩🟩🟩🟩`;

describe("parseWordle", () => {
  it("reads the puzzle number, guess count and grid", () => {
    const result = parseWordle(WORDLE_SHARE);
    assert.equal(result.puzzle_no, 1861);
    assert.equal(result.guesses, 4);
    assert.equal(result.won, true);
    assert.equal(result.grid.length, 4);
    assert.equal(result.grid[3], "🟩🟩🟩🟩🟩");
  });

  it("normalises the puzzle number to an integer", () => {
    // state.json stored this as the string "1,861", which could not be
    // sorted or compared.
    assert.equal(parseWordle("Wordle 1,861 3/6").puzzle_no, 1861);
    assert.equal(parseWordle("Wordle 1.861 3/6").puzzle_no, 1861);
    assert.equal(parseWordle("Wordle 999 3/6").puzzle_no, 999);
  });

  it("treats X/6 as a loss with no guess count", () => {
    const result = parseWordle("Wordle 1,861 X/6\n⬛⬛⬛⬛⬛");
    assert.equal(result.won, false);
    assert.equal(result.guesses, null);
  });

  it("accepts a lowercase x", () => {
    assert.equal(parseWordle("Wordle 1,861 x/6").won, false);
  });

  it("keeps only rows that are five tiles wide", () => {
    const result = parseWordle("Wordle 1 2/6\n🟩🟩🟩🟩🟩\nnot a grid row\n🟨🟨\n⬛⬛⬛⬛⬛");
    assert.deepEqual(result.grid, ["🟩🟩🟩🟩🟩", "⬛⬛⬛⬛⬛"]);
  });

  it("counts tiles by code point, not by UTF-16 length", () => {
    // Each emoji is a surrogate pair, so "🟩🟩🟩🟩🟩".length is 10.
    assert.equal(parseWordle("Wordle 1 1/6\n🟩🟩🟩🟩🟩").grid.length, 1);
  });

  it("caps the grid at six rows", () => {
    const rows = Array(9).fill("⬛⬛⬛⬛⬛").join("\n");
    assert.equal(parseWordle(`Wordle 1 6/6\n${rows}`).grid.length, 6);
  });

  it("tolerates surrounding prose", () => {
    assert.equal(parseWordle("look at this!\nWordle 1,861 4/6\nnice").guesses, 4);
  });

  it("returns null for anything else", () => {
    assert.equal(parseWordle("just some text"), null);
    assert.equal(parseWordle(""), null);
    assert.equal(parseWordle("Wordle 1,861"), null); // no score
  });
});

const POKEDOKU_SHARE = `PokeDoku 2026-07-26
✅✅❌
✅✅✅
❌✅✅
Score: 41`;

describe("parsePokedoku", () => {
  it("counts the correct cells and reads the score", () => {
    const result = parsePokedoku(POKEDOKU_SHARE);
    assert.equal(result.correct, 7);
    assert.equal(result.score, 41);
    assert.equal(result.grid.length, 3);
  });

  it("accepts green squares as correct", () => {
    assert.equal(parsePokedoku("pokedoku\n🟩🟩🟥").correct, 2);
  });

  it("leaves the score null when it is absent", () => {
    assert.equal(parsePokedoku("pokedoku\n✅✅❌").score, null);
  });

  it("never reports more than nine correct", () => {
    const many = `pokedoku\n${"✅".repeat(30)}`;
    assert.equal(parsePokedoku(many).correct, 9);
  });

  it("keeps only rows that are three tiles wide", () => {
    const result = parsePokedoku("pokedoku\n✅✅❌\nheader line\n✅✅");
    assert.deepEqual(result.grid, ["✅✅❌"]);
  });

  it("returns null without the game name", () => {
    assert.equal(parsePokedoku("✅✅❌\n✅✅✅\n❌✅✅"), null);
  });

  // --- the é, and the two numbers that were one number ---------------------

  it("matches the branding, which spells it PokéDoku", () => {
    // `/pokedoku/i` does not match "PokéDoku", so a paste of the real share
    // text parsed as nothing at all.
    assert.notEqual(parsePokedoku("PokéDoku\n✅✅❌"), null);
    assert.notEqual(parsePokedoku("pokédoku\n✅✅❌"), null);
  });

  it("reads the correct count from X/9 rather than counting tiles", () => {
    const share = "PokéDoku\nScore: 8/9\nUniqueness: 137\n✅✅✅\n✅✅✅\n✅✅❌";
    assert.equal(parsePokedoku(share).correct, 8);
  });

  it("does not mistake the 8 in 'Score: 8/9' for the uniqueness score", () => {
    // This is what put correct == score on every stored row: the old
    // `/score[:\s]+(\d+)/i` captured the numerator and the real uniqueness
    // rating was never recorded at all.
    const share = "PokéDoku\nScore: 8/9\nUniqueness: 137\n✅✅✅";
    const result = parsePokedoku(share);
    assert.equal(result.correct, 8);
    assert.equal(result.score, 137);
  });

  it("leaves the score null rather than storing the correct count twice", () => {
    assert.equal(parsePokedoku("PokéDoku\nScore: 8/9\n✅✅❌").score, null);
  });

  it("keeps a bare 'Score: 41' as the uniqueness score", () => {
    // The older share format had no fraction, and it still parses.
    assert.equal(parsePokedoku("pokedoku\n✅✅❌\nScore: 41").score, 41);
  });

  it("parses a grid of tiles it has never seen before", () => {
    // The alphabet was three glyphs wide, so any other tile silently produced
    // `grid: []` -- which is exactly what every logged row contains.
    assert.deepEqual(parsePokedoku("pokedoku\n🟩🟥🟩").grid, ["🟩🟥🟩"]);
    assert.deepEqual(parsePokedoku("pokedoku\n⬜⬜⬛").grid, ["⬜⬜⬛"]);
  });

  it("counts a variation-selector tile as one tile", () => {
    // "✔️" is two code points; measuring it as two makes a three-tile row
    // look four wide and drops the row.
    assert.equal(parsePokedoku("pokedoku\n✔️✔️❌").grid.length, 1);
  });
});

// The real thing, pasted from the app's own share button. Every earlier
// Pokedoku test in this file was written against a guess at the format, and
// every one of them passed while the actual share text produced `grid: []` —
// which is what the database has been storing all along.
const REAL_POKEDOKU_SHARE = `🔴 PokeDoku Summary ⚪️
By: Kwikpanik

Score: 7/9
Uniqueness: 275 (best possible: 93)
Better than 38.0% of players today

✅ ✅ 🟥
✅ ✅ ✅
✅ ✅ 🟥


Play at: https://pokedoku.com/share/2eGbxgnRwUAd`;

describe("the real Pokedoku share text", () => {
  it("parses the grid, which space-separated tiles used to defeat", () => {
    // `.trim()` leaves "✅ ✅ 🟥" at five characters against a width of three,
    // so no row ever matched and the failure was completely silent.
    assert.deepEqual(parsePokedoku(REAL_POKEDOKU_SHARE).grid, ["✅✅🟥", "✅✅✅", "✅✅🟥"]);
  });

  it("reads 7 correct from the fraction, not from counting ticks", () => {
    assert.equal(parsePokedoku(REAL_POKEDOKU_SHARE).correct, 7);
  });

  it("reads 275 as the uniqueness score, not the 7 from 'Score: 7/9'", () => {
    assert.equal(parsePokedoku(REAL_POKEDOKU_SHARE).score, 275);
  });

  it("is not confused by the '(best possible: 93)' that follows it", () => {
    assert.notEqual(parsePokedoku(REAL_POKEDOKU_SHARE).score, 93);
  });

  it("is not confused by 'Better than 38.0% of players today'", () => {
    assert.notEqual(parsePokedoku(REAL_POKEDOKU_SHARE).score, 38);
  });

  it("routes to pokedoku and carries no keys the server forbids", () => {
    const parsed = parseShare(REAL_POKEDOKU_SHARE);
    assert.equal(parsed.game, "pokedoku");
    assert.deepEqual(Object.keys(parsed.body).sort(), ["correct", "grid", "score"]);
  });
});

describe("parseShare", () => {
  it("routes a wordle share to the wordle endpoint", () => {
    const parsed = parseShare(WORDLE_SHARE);
    assert.equal(parsed.game, "wordle");
    assert.equal(parsed.body.guesses, 4);
  });

  it("routes a pokedoku share to the pokedoku endpoint", () => {
    const parsed = parseShare(POKEDOKU_SHARE);
    assert.equal(parsed.game, "pokedoku");
    assert.equal(parsed.body.correct, 7);
  });

  it("returns null for an unrecognised paste", () => {
    assert.equal(parseShare("hello"), null);
  });

  it("prefers wordle when a paste somehow mentions both", () => {
    assert.equal(parseShare(`${WORDLE_SHARE}\npokedoku`).game, "wordle");
  });

  it("produces a body with no extra keys, since the server forbids them", () => {
    assert.deepEqual(Object.keys(parseShare(WORDLE_SHARE).body).sort(), [
      "grid",
      "guesses",
      "puzzle_no",
      "won",
    ]);
    assert.deepEqual(Object.keys(parseShare(POKEDOKU_SHARE).body).sort(), [
      "correct",
      "grid",
      "score",
    ]);
  });
});
