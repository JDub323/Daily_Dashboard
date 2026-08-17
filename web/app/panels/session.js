// Post-session: how hard, and how long.
//
// Two numbers, logged from the gym floor before the walk home, because RPE
// answered three hours later is a memory of a session rather than a report of
// one. Everything the load model knows comes from this panel.
//
// Sparring is its own kind rather than a note on `boxing`: at equal RPE it is
// the session that costs the most to recover from, and how much of it a camp
// contained is a question worth being able to answer later.
//
// The watch will eventually fill `minutes` in by itself, and the RPE too if
// Self Evaluation is switched on. It will not replace this panel — a Garmin
// activity is a thing that got recorded, and a session is a thing you decided
// to do. Hand-logging stays the primary path.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";

const KINDS = ["boxing", "sparring", "roadwork", "strength", "conditioning", "other"];

/** The anchors that stop RPE 7 drifting into RPE 5 over a long camp. */
const RPE_ANCHOR = {
  1: "nothing", 2: "very easy", 3: "easy", 4: "comfortable", 5: "moderate",
  6: "working", 7: "hard", 8: "very hard", 9: "near maximal", 10: "everything",
};

export default {
  id: "session",
  title: "log a session",
  tab: "health",
  slot: "grid",
  span: 3,

  async render(node) {
    let kind = "boxing";
    let rpe = 0;
    const message = el("span.faint");

    const minutes = el("input", {
      type: "number",
      min: "1",
      step: "1",
      inputmode: "numeric",
      placeholder: "minutes",
    });
    const note = el("input", { placeholder: "note (optional)", maxlength: 200 });
    const anchor = el("span.faint");

    const kindRow = el("div.scale", null,
      ...KINDS.map((k) =>
        el("button.tiny", {
          text: k,
          class: k === kind ? "on" : "",
          onclick: (e) => {
            kind = k;
            for (const b of kindRow.children) b.classList.toggle("on", b.textContent === k);
            e.currentTarget.blur();
          },
        }),
      ),
    );

    const rpeRow = el("div.scale", null,
      ...Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
        el("button.tiny", {
          text: String(n),
          onclick: () => {
            rpe = n;
            for (const b of rpeRow.children) b.classList.toggle("on", b.textContent === String(n));
            anchor.textContent = RPE_ANCHOR[n];
          },
        }),
      ),
    );

    const log = el("button.primary", {
      text: "log session",
      onclick: async () => {
        const mins = Number.parseInt(minutes.value, 10);
        if (!rpe) return void (message.textContent = "pick an RPE");
        if (!Number.isInteger(mins) || mins < 1) return void (message.textContent = "minutes?");
        try {
          // `put` notifies and this panel watches the slice, so the redraw —
          // here and on the hero — happens once, from one place.
          put("health", await api.post("/api/session", {
            kind, rpe, minutes: mins, note: note.value.trim() || null,
          }));
        } catch (error) {
          message.textContent = `failed: ${error.message}`;
        }
      },
    });

    const today = store.state.health.sessions_today;
    const logged = today.length
      ? el("div.divider", null,
          ...today.map((s) =>
            el("div.row.spread", null,
              el("span", null,
                el("span.value", { text: s.kind }),
                el("span.faint", { text: ` rpe ${s.rpe} × ${s.minutes}m = ${s.load}` }),
              ),
              el("button.tiny", {
                text: "×",
                title: "delete",
                onclick: async () => {
                  put("health", await api.post("/api/session/delete", { session_id: s.id }));
                },
              }),
            ),
          ),
          // The day's total, said once. Two-a-days are the reason this line
          // exists — the sum is the number the load model actually reads.
          today.length > 1
            ? el("div.row.spread", null,
                el("span.faint", { text: "today" }),
                el("span.value", { text: String(today.reduce((n, s) => n + s.load, 0)) }),
              )
            : null,
        )
      : el("div.faint.divider", { text: "nothing logged today" });

    mount(node,
      kindRow,
      el("div.row.spread.gap", null,
        el("span.faint", { text: "RPE" }),
        anchor,
      ),
      rpeRow,
      el("div.row", null, minutes, el("span.faint", { text: "min" })),
      el("div.row", null, note),
      el("div.row", null, log, message),
      logged,
    );
  },
};
