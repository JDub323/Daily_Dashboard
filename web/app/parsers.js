// Share-text parsers for Wordle and Pokedoku.
//
// Pure: text in, a request body or null out. No DOM, no network, no clock —
// which is the point. Verifying these used to require pasting text into a
// modal and watching what happened.

const WORDLE = /Wordle\s+([\d,.]+)\s+(X|\d)\/6/i;
const WORDLE_TILES = "⬛⬜🟨🟩";

// Pokedoku brands itself "PokéDoku", and the é is not optional in their
// share text — `/pokedoku/i` simply did not match it. Matched with an
// explicit character class rather than by stripping diacritics, because that
// is the entire set of spellings in play.
const POKEDOKU = /pok[eé]doku/i;

// Wider than the three glyphs this started with. A share grid is emoji chosen
// by whoever last touched their front end, and a tile alphabet that is too
// narrow does not fail loudly — `gridRows` just returns nothing and the grid
// is stored as `[]`, which is exactly what happened to every Pokedoku row
// logged before this.
const POKEDOKU_TILES = "✅❌✔️❎⬜⬛🟩🟥🟦🟨";

/** Rows of exactly `width` tile characters, all drawn from `alphabet`. */
function gridRows(text, width, alphabet) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => {
      // Strip variation selectors before measuring: "✔️" is two code points,
      // and counting it as two makes a three-tile row look like four.
      const chars = [...line.replace(/[︎️]/g, "")];
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

/**
 * `{correct, score, grid}` or null.
 *
 * Two numbers, and they are not the same number. `correct` is how many of the
 * nine cells you filled; `score` is Pokedoku's uniqueness rating, which
 * rewards obscure answers and runs to three digits.
 *
 * They were being read as one. The old `/score[:\s]+(\d+)/i` ran against a
 * share line reading "Score: 8/9" and captured the 8, so every logged day
 * stored its correct-count twice and the uniqueness score was never recorded
 * at all. `X/9` is now matched first and explicitly, and the uniqueness score
 * is only taken from a number that is not part of a fraction.
 */
export function parsePokedoku(text) {
  if (!POKEDOKU.test(text)) return null;

  const outOfNine = /(\d)\s*\/\s*9/.exec(text);
  const grid = gridRows(text, 3, POKEDOKU_TILES).slice(0, 3);
  const filled = (text.match(/✅|✔|🟩/gu) || []).length;

  const uniqueness =
    /uniqueness[:\s]+(\d+)/i.exec(text) ?? /score[:\s]+(\d+)(?!\s*\/)/i.exec(text);

  return {
    // The stated fraction wins over the tile count: a grid whose emoji this
    // does not recognise still parses correctly as long as the text says 8/9.
    correct: Math.min(9, outOfNine ? Number(outOfNine[1]) : filled),
    score: uniqueness ? Number(uniqueness[1]) : null,
    grid,
  };
}

/**
 * Which game a pasted blob belongs to.
 *
 * Wordle is tried first, and stays first: its marker is a whole shape
 * ("Wordle 1,861 4/6") while Pokedoku's is one word appearing anywhere, so
 * this is the specific test before the loose one. Reversing it would misfile a
 * Wordle share that happened to have the other game's name somewhere in it.
 */
export function parseShare(text) {
  const wordle = parseWordle(text);
  if (wordle) return { game: "wordle", body: wordle };
  const pokedoku = parsePokedoku(text);
  if (pokedoku) return { game: "pokedoku", body: pokedoku };
  return null;
}
