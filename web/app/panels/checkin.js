// The morning check-in: what you weigh, and how you feel about it.
//
// Thirty seconds, before anything else, in this order — weight first because
// it is the one reading that is ruined by breakfast, coffee or a shower.
//
// The five questions are the Hooper index plus motivation. They are five and
// not fifteen on purpose: the failure mode of a self-report questionnaire is
// not that it is too short, it is that it gets long enough to be answered
// carelessly, and a carelessly-answered scale is worse than no scale because
// it still produces a number. Five taps is a thing you will still be doing in
// February.
//
// --- which way the numbers run ----------------------------------------------
//
// On screen, every item now counts *up* in the thing it names: 5 fatigue is
// wiped out, 5 soreness is wrecked, 5 stress is frazzled. That is how the words
// work and how Hooper is published, and the version that ran them the other way
// — 5 fatigue meaning fresh — was a scale you had to translate before you could
// answer it, every morning, which is exactly the kind of friction that produces
// a carelessly-answered scale.
//
// Underneath, nothing moved. The table still stores 1 = worst, 5 = best for all
// five, so `score` stays a plain sum where 25 is the best morning you can have,
// every derived number keeps its meaning, and the rows logged before this
// change are still comparable with the ones after it. The panel flips the three
// inverted items on the way in and on the way back out, and that pair of
// functions is the only place the two directions meet.
//
// --- the ladder -------------------------------------------------------------
//
// Enter puts the caret in the weight box; Enter commits it and moves to the
// hours; Enter there hands the keyboard to the questionnaire, where 1–5
// answers the highlighted row and drops to the next one. Seven keystrokes for
// the whole morning, no reaching for the mouse, no tabbing past twenty-five
// buttons. `capture.js` explains why the digits can mean this here without
// ceasing to be the tab switches everywhere else.
//
// Once the Garmin sync is running the hours box arrives already filled in and
// Enter walks straight past it, so the morning is back to six keystrokes and
// one of the two measurements takes none of them. Typing over a synced value
// is allowed and permanent — see `Repository.log_health`.

import * as api from "../api.js";
import { beginCapture, captureLabel, endCapture } from "../capture.js";
import { el, mount } from "../dom.js";
import { rerender } from "../panel.js";
import { put, store } from "../store.js";

/**
 * `[key, label, what 1 means, what 5 means]`, in the order the ladder walks
 * them. The two ends are printed beside every row, because a scale whose
 * direction you have to remember is one you will eventually answer backwards.
 */
const ITEMS = [
  ["sleep_quality", "sleep quality", "slept badly", "slept well"],
  ["fatigue", "fatigue", "fresh", "wiped out"],
  ["soreness", "soreness", "loose", "wrecked"],
  ["stress", "stress", "calm", "frazzled"],
  ["motivation", "motivation", "dreading it", "keen"],
];

/**
 * The three the panel shows upside down from how the table stores them.
 *
 * Membership here is the entire difference between the two directions. Adding
 * an item to the questionnaire whose words count up in something bad — a
 * `pain` scale, say — means adding its key here and nothing else.
 */
const INVERTED = new Set(["fatigue", "soreness", "stress"]);

/** What a tap on `n` means to the database. */
export const toStored = (key, shown) => (INVERTED.has(key) ? 6 - shown : shown);

/** What a stored value means on screen. Its own inverse — 6-(6-n) is n. */
export const toShown = (key, stored) => (INVERTED.has(key) ? 6 - stored : stored);

/** Today's reading for one metric, or null. The series is oldest-first, so
 *  the last point is today's only if its date says so — which is the whole
 *  check: "the most recent weight" and "this morning's weight" are the same
 *  number on most days and very different ones on the day you skipped. */
export function metricToday(state, metric) {
  const series = state.health.series.find((s) => s.metric === metric);
  const last = series?.points?.[series.points.length - 1];
  return last && last[0] === state.date ? last[1] : null;
}

export const weightToday = (state) => metricToday(state, "weight");

/**
 * Whether today's reading for a metric came from the watch rather than a hand.
 *
 * The distinction is worth showing on exactly one box. A synced number is one
 * you can leave alone; a typed one is one you are responsible for, and once
 * the Forerunner is on the wrist the ordinary morning has the hours already
 * filled in. Typing over it wins permanently — `Repository.log_health` will
 * not let a later sync take the correction back.
 */
export const syncedToday = (state, metric) => state.health.synced_today.includes(metric);

// --- state that outlives a render -------------------------------------------
//
// All three of these are module-level because the panel re-renders underneath
// them: the weight post and the fifth answer both write to the store, which
// notifies, which rebuilds this body from scratch. A cursor held in a render
// closure would be thrown away by the very keystroke that advanced it.

/** Which question the ladder is on, or null when it is not running. */
let cursor = null;
/** Answers not yet posted, in *stored* form. The row is all-or-nothing in the
 *  database — `score` over three items would be a number that means nothing —
 *  so a partial questionnaire lives here and nowhere else. */
let draft = {};
/** The day `draft` belongs to, so a tab open past midnight starts empty. */
let draftDate = null;
/** This render's weight box, for the Enter that starts the whole sequence. */
let weightBox = null;
/** …and the hours box, which sits between it and the questions. */
let sleepBox = null;

const OWNER = "checkin";

function answers() {
  if (draftDate !== store.state.date) {
    draft = {};
    draftDate = store.state.date;
    cursor = null;
  }
  const saved = store.state.health.readiness_today;
  // Only the five. `readiness_today` is a response model and carries `date`
  // and the computed `score` with it, and the request model forbids extras —
  // spreading it into the body is a 422 the moment you change one answer on a
  // morning already answered.
  const merged = {};
  for (const [key] of ITEMS) {
    const value = draft[key] ?? saved?.[key];
    if (value) merged[key] = value;
  }
  return merged;
}

/** The first unanswered row, or the first row when the morning is complete. */
function firstOpen() {
  const current = answers();
  const index = ITEMS.findIndex(([key]) => !current[key]);
  return index === -1 ? 0 : index;
}

function label() {
  return `check-in ▸ ${ITEMS[cursor][1]}`;
}

function startLadder(from = firstOpen()) {
  cursor = from;
  rerender(OWNER);
}

async function save() {
  const current = answers();
  if (ITEMS.some(([key]) => !current[key])) return false;
  put("health", await api.post("/api/readiness", current));
  draft = {};
  return true;
}

export default {
  id: OWNER,
  title: "morning check-in",
  tab: "health",
  slot: "grid",
  span: 3,

  /**
   * Enter, from the focus ring.
   *
   * Straight to whichever half is not done: the box if there is no weight yet,
   * the questions if there is. Doing it in that order is the point — the
   * weight reading is the one that is ruined by waiting.
   */
  onEnter() {
    if (weightToday(store.state) === null && weightBox) {
      weightBox.focus();
      weightBox.select();
      return true;
    }
    // Then the hours, and only if they are still missing — which, once the
    // watch is syncing, is a morning it spent on the charger.
    if (metricToday(store.state, "sleep_hours") === null && sleepBox) {
      sleepBox.focus();
      sleepBox.select();
      return true;
    }
    startLadder();
    return true;
  },

  async render(node) {
    const current = answers();

    const weight = el("input", {
      type: "number",
      step: "0.1",
      inputmode: "decimal",
      placeholder: "morning weight",
      value: weightToday(store.state) ?? "",
      onchange: async (e) => {
        const value = Number.parseFloat(e.target.value);
        if (Number.isNaN(value) || value <= 0) return;
        // No re-render here: `put` notifies, and this panel watches the slice
        // (see WATCHES in main.js) along with the two others that read it.
        put("health", await api.post("/api/health", { metric: "weight", value }));
      },
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        // Blur first: that is what fires `change`, so the weight is committed
        // by the same keystroke that moves on rather than a moment later when
        // focus happens to leave the box.
        e.target.blur();
        sleep.focus();
        sleep.select();
      },
    });
    weightBox = weight;

    // Hours slept, beside the weight and before the questions, because it is
    // the same kind of thing: a measurement of the night rather than an
    // opinion about it. `sleep_quality` further down asks how it *felt*, and
    // the two are genuinely different — six good hours and six bad ones are
    // the same number and a different morning, which is the entire reason both
    // are asked.
    //
    // Once the watch is syncing this box is already full when you get here,
    // and Enter walks straight past it. Typing over a synced value wins for
    // good: the write goes in as `manual`, and no later sync may touch a
    // manual row.
    const synced = syncedToday(store.state, "sleep_hours");
    const sleep = el("input", {
      type: "number",
      step: "0.1",
      min: "0",
      max: "24",
      inputmode: "decimal",
      placeholder: "hours slept",
      value: metricToday(store.state, "sleep_hours") ?? "",
      onchange: async (e) => {
        const value = Number.parseFloat(e.target.value);
        if (Number.isNaN(value) || value <= 0 || value > 24) return;
        put("health", await api.post("/api/health", { metric: "sleep_hours", value }));
      },
      onkeydown: (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        e.target.blur();
        startLadder();
      },
    });
    sleepBox = sleep;

    const rows = ITEMS.map(([key, name, low, high], index) => {
      const buttons = el("div.scale", null,
        ...[1, 2, 3, 4, 5].map((shown) =>
          el("button.tiny", {
            text: String(shown),
            class: current[key] && toShown(key, current[key]) === shown ? "on" : "",
            "aria-pressed": Boolean(current[key]) && toShown(key, current[key]) === shown,
            onclick: () => answer(index, shown),
          }),
        ),
      );
      return el(`div.scale-row${cursor === index ? ".on-row" : ""}`, null,
        el("div.row.spread", null,
          el("span", { text: name }),
          el("span.faint", { text: `1 ${low} · 5 ${high}` }),
        ),
        buttons,
      );
    });

    /**
     * Answer one row, from a tap or from a digit.
     *
     * The paint is done here rather than left to the next render because until
     * the fifth answer lands there IS no next render — `save` refuses to post a
     * partial row — so the first four would light nothing at all, and a scale
     * that does not respond to being tapped reads as broken.
     */
    async function answer(index, shown) {
      const [key] = ITEMS[index];
      draft[key] = toStored(key, shown);
      draftDate = store.state.date;
      for (const b of rows[index].querySelector(".scale").children) {
        const picked = b.textContent === String(shown);
        b.classList.toggle("on", picked);
        b.setAttribute("aria-pressed", String(picked));
      }
      // The last answer ends the ladder rather than wrapping to the top: the
      // questionnaire is finished, and a cursor still sitting on a row would
      // have the next digit silently overwrite an answer you just gave.
      //
      // Ended by hand rather than through `stopLadder`, which would redraw —
      // the save below writes to the store and the redraw comes from there.
      if (cursor !== null) {
        if (index < ITEMS.length - 1) {
          move(index + 1);
        } else {
          rows[index].classList.remove("on-row");
          cursor = null;
          endCapture(OWNER);
        }
      }
      await save();
    }

    /** Put the ladder on a row: the highlight, and the statusline. */
    function move(to) {
      rows[cursor]?.classList.remove("on-row");
      cursor = Math.max(0, Math.min(ITEMS.length - 1, to));
      rows[cursor].classList.add("on-row");
      rows[cursor].scrollIntoView({ block: "nearest" });
      captureLabel(label());
    }

    const done = store.state.health.readiness_today;
    const answered = ITEMS.filter(([key]) => current[key]).length;
    // The keys, printed rather than left in the cheatsheet — same argument as
    // the digit on a tab.
    const keyHint = el("span.faint", {
      text: cursor === null ? "⏎ to fill in" : "1–5 · j/k · esc",
    });

    mount(node,
      el("div.row", null, weight, el("span.faint", { text: "lb" })),
      el("div.row", null,
        sleep,
        el("span.faint", { text: synced ? "h · from the watch" : "h" }),
      ),
      el("div.divider"),
      ...rows,
      el("div.row", null,
        done
          ? el("span.dim", { text: `${done.score}/25 ✓` })
          : el("span.faint", { text: answered ? `${answered}/5` : "answer all five" }),
        keyHint,
      ),
    );

    // Re-taken on every render, because every render replaces the nodes the
    // handler closes over. `beginCapture` replaces rather than nests.
    if (cursor !== null) {
      beginCapture({
        owner: OWNER,
        label: label(),
        onKey: (e) => {
          if (/^[1-5]$/.test(e.key)) {
            answer(cursor, Number(e.key));
            return true;
          }
          if (e.key === "j" || e.key === "ArrowDown" || e.key === "Enter") {
            move(cursor + 1);
            return true;
          }
          if (e.key === "k" || e.key === "ArrowUp") {
            move(cursor - 1);
            return true;
          }
          return false;
        },
        // Esc, a tab switch, or a key the ladder does not use. Painted here
        // rather than by a redraw: the ladder ending is not a change to
        // anything the server knows about, so nothing else would draw it.
        onEnd: () => {
          cursor = null;
          for (const row of rows) row.classList.remove("on-row");
          keyHint.textContent = "⏎ to fill in";
        },
      });
    }
  },
};
