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
