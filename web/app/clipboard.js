// Putting a long prompt on the clipboard, with the two fallbacks it needs.
//
// Extracted from the debate panel when the trick panel wanted the same thing.
// It is not the copying that is worth sharing — it is the fallback chain, which
// was arrived at empirically and would have been rediscovered wrong the second
// time.

import { el } from "./dom.js";
import { toast } from "./ui.js";

/**
 * A copy button and the prompt behind a disclosure.
 *
 * Three attempts, in order, because the first one is refused more often than
 * "localhost is a secure context" suggests: the async Clipboard API also needs
 * the clipboard-write permission and a browser is free to withhold it.
 * Verified — the embedded preview browser refuses `writeText` outright and the
 * `execCommand` path succeeds there.
 *
 * The third attempt is not a failure message. It opens the disclosure and
 * selects the text, so there is still a way through that does not require
 * reading an error.
 */
export function promptBlock(text, { summary = "read the prompt", label = "copy the prompt" } = {}) {
  const pre = el("pre.prompt", { text });
  const details = el("details.archive-item", null, el("summary", { text: summary }), pre);

  const done = () => {
    copy.textContent = "copied ✓";
    setTimeout(() => { copy.textContent = label; }, 2000);
  };

  const legacyCopy = () => {
    details.open = true;  // execCommand cannot copy from a display:none subtree
    const selection = getSelection();
    selection?.selectAllChildren(pre);
    const copied = document.execCommand?.("copy");
    selection?.removeAllRanges();
    return copied;
  };

  const copy = el("button.primary", {
    text: label,
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(text);
        done();
        return;
      } catch {
        /* fall through — the permission was refused, not the page */
      }
      if (legacyCopy()) {
        done();
        return;
      }
      getSelection()?.selectAllChildren(pre);
      toast("could not reach the clipboard — the prompt is selected, press Ctrl-C", "info");
    },
  });

  return { button: copy, details, node: el("div", null, el("div.row", null, copy), details) };
}
