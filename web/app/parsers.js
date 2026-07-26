// Share-text parsers for Wordle and Pokedoku.
//
// Pure: text in, a request body or null out. No DOM, no network, no clock —
// which is the point. Verifying these used to require pasting text into a
// modal and watching what happened.

const WORDLE = /Wordle\s+([\d,.]+)\s+(X|\d)\/6/i;
const WORDLE_TILES = "⬛⬜🟨🟩";
const POKEDOKU_TILES = "✅❌⬜🟩🟥";

/** Rows of exactly `width` tile characters, all drawn from `alphabet`. */
function gridRows(text, width, alphabet) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => {
      const chars = [...line];
      return chars.length === width && chars.every((c) => alphabet.includes(c));
    });
}

/**
 * `{puzzle_no, guesses, won, grid}` or null.
 *
 * puzzle_no is normalised to an integer here rather than shipped as the
 * string "1,861" — state.json stored it that way and it could not be sorted
 * or compared.
 */
export function parseWordle(text) {
  const m = WORDLE.exec(text);
  if (!m) return null;
  const lost = m[2].toUpperCase() === "X";
  return {
    puzzle_no: Number(m[1].replace(/[,.]/g, "")) || null,
    guesses: lost ? null : Number(m[2]),
    won: !lost,
    grid: gridRows(text, 5, WORDLE_TILES).slice(0, 6),
  };
}

/** `{correct, score, grid}` or null. */
export function parsePokedoku(text) {
  if (!/pokedoku/i.test(text)) return null;
  const score = /score[:\s]+(\d+)/i.exec(text);
  return {
    correct: Math.min(9, (text.match(/✅|🟩/gu) || []).length),
    score: score ? Number(score[1]) : null,
    grid: gridRows(text, 3, POKEDOKU_TILES).slice(0, 3),
  };
}

/** Which game a pasted blob belongs to. */
export function parseShare(text) {
  const wordle = parseWordle(text);
  if (wordle) return { game: "wordle", body: wordle };
  const pokedoku = parsePokedoku(text);
  if (pokedoku) return { game: "pokedoku", body: pokedoku };
  return null;
}
