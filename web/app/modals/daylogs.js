// Every day log you have written, behind a button on the one you are writing.
//
// This was a panel on the home tab, and it was the tallest card there — a
// scrolling list of every entry, on a page you open to see whether you kept the
// day. Reading back through months of one-line entries is a thing you do
// occasionally and on purpose; it does not need to be on screen while you type
// today's. So it is a modal now, opened from the day-log cell itself, which is
// where you are standing when the question "what did I write last time" occurs
// to you.
//
// The `/api/history` fetch lives here rather than in the panel because it is
// cached for the session and dropped on a day rollover — see
// `invalidateHistory`, which main.js calls from the same place it drops the
// other archives.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { openModal } from "../ui.js";

let cache = null;
let inflight = null;

function history() {
  if (cache) return Promise.resolve(cache);
  inflight ??= api
    .get("/api/history")
    .then((data) => (cache = data))
    .catch((error) => (cache = { daylogs: [], error: error.message }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Dropped when the day turns, or when a log is written — so today's entry is
 *  in the list the next time it is opened rather than a fetch behind. */
export function invalidateHistory() {
  cache = null;
}

export function openDaylogArchive() {
  openModal("past logs", async (body) => {
    if (!cache) {
      mount(body, el("div.row", null, el("span.spinner"), el("span.dim", { text: "loading…" })));
    }
    const data = await history();

    if (data.error) {
      mount(body, el("div.err", { text: data.error }));
      return;
    }
    if (!data.daylogs.length) {
      mount(body, el("div.faint", { text: "no day logs yet" }));
      return;
    }

    mount(body,
      el("div.faint.small", { text: `${data.daylogs.length} entries, newest first` }),
      el("div.archive", null,
        ...data.daylogs.map((entry) =>
          el("div.archive-item", null,
            el("div.value.small", { text: entry.date }),
            el("div.daylog-text", { text: entry.text }),
          ),
        ),
      ),
    );
  });
}
