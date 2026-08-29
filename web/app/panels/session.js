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
//
// --- logging one without the mouse ------------------------------------------
//
// Enter takes the keyboard and walks the four fields in the order you know
// them: a letter picks the kind, a digit picks the RPE, and the caret lands in
// the minutes box. Enter there goes to the note, Enter there logs it. Five or
// six keystrokes for a session, which is about what it should cost when you are
// standing in a gym doorway.
//
// `0` is RPE 10 — it is the key next to 9 and there is no RPE 0, so the row
// reads 1…9,0 the way a phone keypad does. Kinds go by first letter, except
// that sparring took `s` first, so strength is `t`. The letter is printed on
// each button rather than left in the cheatsheet, for the same reason the tabs
// print their digit.

import * as api from "../api.js";
import { beginCapture, captureLabel, endCapture } from "../capture.js";
import { el, mount } from "../dom.js";
import { RPE_ANCHOR, SESSION_KINDS } from "../logs.js";
import { leaveCell } from "../nav.js";
import { put, store } from "../store.js";

// The six kinds and the ten RPE anchors live in `logs.js`, because the phone
// at `/m` offers the same six and prints the same ten.
const KINDS = SESSION_KINDS;

const BY_KEY = new Map(Object.entries(KINDS).map(([kind, key]) => [key, kind]));

const OWNER = "session";

// --- state that outlives a render -------------------------------------------
//
// A session posts on the last keystroke and the store notifies, which rebuilds
// this body — so the half-filled form cannot live in a render closure. It also
// should not: deleting one of today's sessions redraws the panel too, and
// losing a kind and an RPE to that would be its own small annoyance.

/** `null`, `"kind"` or `"rpe"` — which field the keyboard is on. */
let stage = null;
let kind = "boxing";
let rpe = 0;
/** The current render's key handler, highlighter and end-of-capture paint.
 *  Rebound on every draw, because all three close over nodes the draw has just
 *  replaced. */
let handlers = null;

const label = () => `session ▸ ${stage}`;

/** (Re)take the keyboard for whatever stage we are on. */
function take() {
  if (!handlers) return;
  beginCapture({
    owner: OWNER,
    label: label(),
    onKey: handlers.onKey,
    onEnd: handlers.onEnd,
  });
  handlers.paintStage();
}

export default {
  id: OWNER,
  title: "log a session",
  host: "cyc",
  tab: "health",
  slot: "grid",
  span: 3,

  onEnter() {
    stage = "kind";
    take();
    return true;
  },

  /**
   * A kind letter, pressed at the card rather than inside its ladder.
   *
   * The six letters were already the way to pick a kind; they simply needed
   * an Enter first to open the ladder they belonged to, which is a keystroke
   * spent saying "yes, this panel" to a panel the ring was already on. Now
   * the letter does both, and lands on the RPE row exactly as it would have.
   *
   * None of the six is bound anywhere else — `b` was the bathroom log and is
   * not any more, which is what made this unambiguous enough to do.
   */
  onKey(e) {
    if (stage !== null || !BY_KEY.has(e.key)) return false;
    stage = "kind";
    take();
    return Boolean(handlers?.onKey(e));
  },

  async render(node) {
    const message = el("span.faint");

    const minutes = el("input", {
      type: "number",
      min: "1",
      step: "1",
      inputmode: "numeric",
      placeholder: "minutes",
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        // Straight on to the note rather than logging here. The note is the
        // field you skip, so it is the one that should cost a keystroke to
        // skip rather than a keystroke to reach.
        note.focus();
        note.select();
      },
    });

    const note = el("input", {
      placeholder: "note (optional)",
      maxlength: 200,
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        log();
      },
    });

    const anchor = el("span.faint", { text: rpe ? RPE_ANCHOR[rpe] : "" });

    const kindRow = el("div.scale", null,
      ...Object.entries(KINDS).map(([k, key]) =>
        el("button.tiny.keyed", {
          text: k,
          dataset: { key },
          class: k === kind ? "on" : "",
          onclick: (e) => {
            pickKind(k);
            e.currentTarget.blur();
          },
        }),
      ),
    );

    const rpeRow = el("div.scale", null,
      ...Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
        el("button.tiny", {
          text: String(n),
          class: n === rpe ? "on" : "",
          onclick: () => pickRpe(n),
        }),
      ),
    );

    /** Paint a kind choice, from a click or from its letter. */
    function pickKind(k) {
      kind = k;
      for (const b of kindRow.children) b.classList.toggle("on", b.textContent === k);
    }

    function pickRpe(n) {
      rpe = n;
      for (const b of rpeRow.children) b.classList.toggle("on", b.textContent === String(n));
      anchor.textContent = RPE_ANCHOR[n];
    }

    async function log() {
      const mins = Number.parseInt(minutes.value, 10);
      if (!rpe) return void (message.textContent = "pick an RPE");
      if (!Number.isInteger(mins) || mins < 1) return void (message.textContent = "minutes?");
      try {
        // `put` notifies and this panel watches the slice, so the redraw —
        // here and on the hero — happens once, from one place.
        put("health", await api.post("/api/session", {
          kind, rpe, minutes: mins, note: note.value.trim() || null,
        }));
        // A logged session is finished with: the next one is a fresh form
        // rather than the last one's numbers waiting to be logged twice.
        rpe = 0;
        stage = null;
        endCapture(OWNER);
        // Out of the boxes as well as out of the ladder. The note field still
        // has the caret at this point — it is where Enter was pressed — and a
        // finished log that leaves you in insert mode costs an Esc before the
        // next key does anything. Nothing is being typed any more, so nothing
        // should still be holding the keyboard.
        leaveCell();
      } catch (error) {
        message.textContent = `failed: ${error.message}`;
      }
    }

    const keyHint = el("span.faint", {
      text: stage === null ? "⏎ to log one" : "letter · digit · ⏎",
    });

    /** Highlight whichever field the keyboard is on. */
    function paintStage() {
      kindRow.classList.toggle("on-row", stage === "kind");
      rpeRow.classList.toggle("on-row", stage === "rpe");
      keyHint.textContent = stage === null ? "⏎ to log one" : "letter · digit · ⏎";
    }

    /** Esc, a tab switch, or a key the form does not use. Also the ordinary
     *  hand-off to the minutes box — the caret is a better cursor than a
     *  highlighted row, so there is nothing left to show. */
    function onEnd() {
      stage = null;
      paintStage();
    }

    /** Move the keyboard to the RPE row. */
    function toRpe() {
      stage = "rpe";
      paintStage();
      captureLabel(label());
    }

    /** Off the buttons and into the boxes — the caret takes over from here. */
    function toMinutes() {
      stage = null;
      paintStage();
      endCapture(OWNER);
      minutes.focus();
      minutes.select();
    }

    function onKey(e) {
      if (stage === "kind" && BY_KEY.has(e.key)) {
        pickKind(BY_KEY.get(e.key));
        toRpe();
        return true;
      }
      // Digits answer the RPE from either stage — landing on the panel and
      // typing `7` means the same thing whether or not you changed the kind,
      // and boxing is the default because it is most of them.
      if (/^[0-9]$/.test(e.key)) {
        pickRpe(e.key === "0" ? 10 : Number(e.key));
        toMinutes();
        return true;
      }
      if (e.key === "Enter") {
        if (stage === "kind") toRpe();
        else if (rpe) toMinutes();
        return true;
      }
      return false;
    }

    handlers = { onKey, paintStage, onEnd };

    const logButton = el("button.primary", { text: "log session", onclick: log });

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

    // Every box gets its name above it, in the same small type, so the column
    // reads as four labelled fields rather than three and a stray unit. The
    // unit lives in the minutes placeholder, where it is a hint about what to
    // type rather than a word sitting beside the box afterwards.
    mount(node,
      el("div.row.spread.gap", null,
        el("span.faint", { text: "type" }),
        keyHint,
      ),
      kindRow,
      el("div.row.spread.gap", null,
        el("span.faint", { text: "RPE" }),
        anchor,
      ),
      rpeRow,
      el("span.faint", { text: "time" }),
      el("div.row", null, minutes),
      el("span.faint", { text: "notes" }),
      el("div.row", null, note),
      el("div.row", null, logButton, message),
      logged,
    );

    // Re-taken after a redraw, because the handlers above close over nodes
    // this render has just replaced.
    if (stage === null) paintStage();
    else take();
  },
};
