// The one gate that decides whether a key belongs to the app or to vim.
//
// keys.js turns every binding off inside a text box already, so the embedded
// editor gets the whole alphabet for free. Esc and Tab are the two exceptions
// to that rule — they survive the typing gate so you can always get out of a
// field — and they are also the two keys a modal editor cannot do without. So
// this predicate is the entire difference between "an editor" and "an editor
// you can type into and never leave".
//
// Tested here rather than in a browser because the interesting cases are the
// ones a browser will not produce on demand: `e.target` is not always an
// Element, and a TypeError raised in this function takes Esc down everywhere,
// not just in the panel.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { inEditor } from "../../web/app/keys.js";
import { HINT_KEYS } from "../../web/app/nav.js";
import { ZOOMS } from "../../web/app/panels/health.js";

/** An element that knows which selectors it matches an ancestor for. */
const node = (...matches) => ({
  closest: (selector) => (matches.includes(selector) ? {} : null),
});

describe("inEditor", () => {
  it("claims anything inside the terminal", () => {
    assert.equal(inEditor(node(".term")), true);
  });

  it("does not claim an ordinary text box", () => {
    // The paste box on the same panel, a few nodes away from the terminal.
    assert.equal(inEditor(node(".panel")), false);
  });

  it("survives a target that is not an element at all", () => {
    // `document` and `window` both arrive here for synthetic events and
    // neither has `closest`. Throwing would break Esc for the whole app.
    assert.equal(inEditor({}), false);
    assert.equal(inEditor(null), false);
    assert.equal(inEditor(undefined), false);
  });
});

// `f` dispatches two alphabets at once: a letter painted on a link, and a
// letter that expands one of the health rail's four scores. They must not
// overlap, and nothing at runtime notices when they do — keys.js checks
// `ZOOMS` first, so a letter in both lists paints a hint on a link that can
// never be reached and the panel looks perfectly fine.
//
// The third case below is the one that decided which letters the zooms got.
// The obvious choice was mnemonic — r, o, u, y for recovery, lOad, fUel,
// hYdration — and all four of those are currently *in* `HINT_KEYS`, so taking
// them would have left seventeen. The zooms took the deleted logging
// carousel's letters instead, which were already withheld, and this alphabet
// did not have to move.
describe("the two alphabets under f", () => {
  it("never spend the same letter twice", () => {
    for (const key of Object.keys(ZOOMS)) {
      assert.ok(
        !HINT_KEYS.includes(key),
        `${key} expands ${ZOOMS[key]} and is also painted on links`,
      );
    }
  });

  it("gives every zoom a single lower-case letter", () => {
    // Upper case would need Shift, which `panelKey` and the hint layer both
    // decline, and a digit is already "open that numbered notification" — which
    // is the whole reason these are letters and not the 1–4 the rail would
    // otherwise have wanted.
    for (const key of Object.keys(ZOOMS)) {
      assert.match(key, /^[a-z]$/, `${key} is not a letter f can dispatch`);
    }
  });

  it("leaves enough letters to hint a headline list", () => {
    // The news panels run to twenty links. A hint alphabet shorter than that
    // silently truncates the list rather than failing.
    assert.ok(HINT_KEYS.length >= 20, `only ${HINT_KEYS.length} hint letters left`);
  });
});
