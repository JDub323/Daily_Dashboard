// The speed bonus: the curve, and the box you type a solve time into.
//
// Both live here because both the live panel and the archive's late-submission
// box offer the same deal, and an offer that reads differently in two places is
// two different offers. The alternative — the box in panels/math.js, imported
// by modals/math.js — is a genuine import cycle, since the panel already
// imports the archive modal.
//
// This is a mirror of `scoring.time_multiplier` in dashboard/scoring.py, and
// the Python is the authority: the multiplier that actually reaches your
// rating is computed server-side inside the same transaction as the write, and
// comes back in the response. Nothing here is ever sent — the browser posts raw
// minutes and is told what they were worth.
//
// So the cost of the two copies drifting is a preview that reads slightly wrong
// for a while, not a rating that is wrong. That is the trade being made: the
// alternative is a round trip per keystroke to price a box you are still typing
// in, and an offer of a bonus that will not say what it is worth is an offer
// worth declining.
//
// If you edit the constants in scoring.py, edit them here. There are four.

import { el } from "./dom.js";

const NEUTRAL = 1.5;
const FLOOR = 0.66;
const FLOOR_AT = 3.0;
const CAP = 1.5;

const EXPONENT = Math.log(FLOOR) / Math.log(NEUTRAL / FLOOR_AT);

/** What a solve in `minutes` is worth, given the tier's reference time. */
export function timeMultiplier(minutes, parMinutes) {
  if (!minutes || !parMinutes) return 1;
  const ratio = minutes / parMinutes;
  return Math.min(CAP, Math.max(FLOOR, (NEUTRAL / ratio) ** EXPONENT));
}

/** The minutes at which the multiplier is exactly 1x — the number to beat. */
export function neutralMinutes(parMinutes) {
  return Math.round(NEUTRAL * parMinutes);
}

/**
 * `×1.28 faster than 30 min` — the whole preview, as one line of text.
 *
 * Returns the neutral point rather than a multiplier when the box is empty,
 * because that is the more useful thing to know before you have typed
 * anything: not "this is worth 1x" but "here is the time that is worth 1x".
 */
export function priceLabel(minutes, parMinutes) {
  if (!parMinutes) return "";
  const neutral = neutralMinutes(parMinutes);
  if (!minutes) return `${neutral} min scores ×1.00 — under that pays, over it decays`;

  const m = timeMultiplier(minutes, parMinutes);
  const shape = m > 1.001 ? "faster than" : m < 0.999 ? "slower than" : "level with";
  return `×${m.toFixed(2)} — ${shape} ${neutral} min`;
}

/** Which way the preview reads, for colouring it. */
function priceClass(minutes, parMinutes) {
  if (!minutes || !parMinutes) return "faint";
  const m = timeMultiplier(minutes, parMinutes);
  return m > 1.001 ? "ok" : m < 0.999 ? "bad" : "faint";
}

/**
 * The "how long did it take" box, plus the live price of what is in it.
 *
 * Deliberately not a timer. There is no start button anywhere in this app: a
 * stopwatch running in a tab is wrong the moment you get up, and the honest
 * version of "how long did that take" is the one you answer from a clock you
 * were actually watching.
 *
 * Blank is a first-class answer, not a skipped field — an untimed solve scores
 * exactly what it scored before any of this existed. That is why there is no
 * validation message and nothing goes red when it is empty: nothing is wrong.
 *
 * `node` is null when the tier has no configured reference time, so turning the
 * feature off is emptying `[math.par_minutes]` and the box simply is not there.
 */
export function minutesBox(parMinutes) {
  const price = el("span.faint.small", { text: priceLabel(null, parMinutes) });
  const input = el("input.minutes", {
    type: "number",
    min: 1,
    max: 1440,
    step: 1,
    placeholder: "min",
    "aria-label": "minutes spent",
  });

  input.addEventListener("input", () => {
    const value = Number(input.value);
    price.textContent = priceLabel(value, parMinutes);
    price.className = `${priceClass(value, parMinutes)} small`;
  });

  return {
    /** null when blank or out of range — the server reads that as untimed. */
    value: () => {
      const n = Math.round(Number(input.value));
      return Number.isFinite(n) && n >= 1 && n <= 1440 ? n : null;
    },
    node: parMinutes
      ? el("div.row.timebox", null, el("span.faint.small", { text: "took" }), input, price)
      : null,
  };
}
