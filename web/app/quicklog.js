// A panel you fill in with three keystrokes and then forget about.
//
// Two of the health tab's logs — what went in and what came out — are the same
// shape: a handful of questions, each with three or four legal answers, asked
// four to eight times a day. Nothing about that is hard, but the *cost* is the
// whole design problem. A meal log that takes fifteen seconds and a mouse is a
// meal log kept for nine days; the entries have to cost about as much as not
// bothering, or the series ends up describing which weeks you had the patience
// to log rather than which weeks you ate well.
//
// So the budget is one keystroke per question, and the last one commits.
//
//   m l 4        a large, fairly clean meal, now
//   b s 2 m      a soft stool, medium, now
//   b u 4 l      dark urine, large volume, now
//
// Both panels are built from a list of steps, here, rather than being written
// twice. They diverge in exactly one way that matters — bathroom branches on
// its first answer, meals do not — and that is the `when` predicate below,
// which is a smaller thing than either panel would have been on its own.
//
// --- the hour ---------------------------------------------------------------
//
// Every entry carries the hour it happened, rounded down, in 24h. Rounded
// because that is what can be answered truthfully from memory a minute later,
// and because a minutes field is a second keystroke on a budget of three.
// It starts at the browser's current hour and `-`/`+` walk it, so logging the
// lunch you forgot about is `m` `-` `-` `m` `4`.
//
// The clock, like the calendar, belongs to the browser and never to the
// server: [Invariant 1] holds for the hour for exactly the reason it holds for
// the date.

import * as api from "./api.js";
import { beginCapture, captureLabel, endCapture } from "./capture.js";
import { el, mount } from "./dom.js";
import { leaveCell } from "./nav.js";
import { put, store } from "./store.js";

/** Keys that move the hour rather than answering the question in front of
 *  you. Two spellings each, because `+` needs a shift on most layouts and `=`
 *  is the key it lives on. */
const EARLIER = new Set(["-", "_", ","]);
const LATER = new Set(["+", "=", "."]);

const pad = (n) => String(n).padStart(2, "0");

/**
 * Which steps this entry actually asks, given what has been answered so far.
 *
 * The whole of the branching, and pure so it can be tested without a DOM —
 * which matters more than it looks. Getting this wrong in the direction of
 * *too many* steps means a urine entry stopping to ask for a solidity, and the
 * table would reject the row after four keystrokes had already been spent on
 * it; getting it wrong the other way means committing an entry with a field
 * missing, which is the same rejection with less warning.
 */
export const applicableSteps = (steps, draft) =>
  steps.filter((step) => !step.when || step.when(draft));

/**
 * Build one logging panel.
 *
 * `steps` is the ladder, in order: each is `{ name, options, when }` where
 * `options` is `[key, value, label]` triples and `when(draft)` decides whether
 * this step applies to the answers so far. A step whose `when` is false is
 * skipped on the way past, which is all the branching either panel needs.
 *
 * The POST body is the answers plus the hour, verbatim — so a step's `name` is
 * the request field it fills, and adding a question to either panel is adding
 * a step here and a column there and nothing in between. `entries(state)` is
 * today's rows out of the store and `describe(entry)` says one of them in a
 * few words.
 */
export function quickLog({
  id,
  title,
  span = 3,
  steps,
  endpoint,
  deleteEndpoint,
  entries,
  describe,
  empty,
}) {
  // --- state that outlives a render ---------------------------------------
  //
  // All of it module-scoped for the same reason the check-in's cursor is: the
  // last keystroke posts, the store notifies, and this panel's body is rebuilt
  // from scratch underneath the handler that is still running. Anything held
  // in a render closure would be thrown away by the keystroke that set it.

  /** Which step the keyboard is on, or null when the ladder is not running. */
  let cursor = null;
  /** Answers so far, by step name. Never posted until the ladder finishes —
   *  half a meal is not a row the table has any use for. */
  let draft = {};
  /** The hour being logged against. Set when the ladder starts, so a tab left
   *  open through the afternoon does not log dinner at 11am. */
  let hour = 0;
  /** This render's key handler and painter, rebound every draw. */
  let handlers = null;

  const applicable = () => applicableSteps(steps, draft);

  const label = () =>
    cursor === null ? title : `${title} ▸ ${applicable()[cursor]?.name} · ${pad(hour)}:00`;

  /** The next step that applies, given what has been answered. Null when the
   *  ladder is finished — which is the signal to post. */
  function next(from) {
    return from + 1 < applicable().length ? from + 1 : null;
  }

  function start() {
    draft = {};
    hour = new Date().getHours();
    cursor = 0;
    handlers?.paint();
    take();
  }

  function take() {
    if (!handlers) return;
    beginCapture({ owner: id, label: label(), onKey: handlers.onKey, onEnd: handlers.onEnd });
  }

  return {
    id,
    title,
    tab: "health",
    slot: "grid",
    span,
    // Both quick logs ride in the rotation with the check-in and the session
    // log. Their old argument for being last in the column — that you are
    // never looking at them when you use them — is unchanged and is now
    // served better: they are reachable in two keystrokes from anywhere and
    // cost no height at all on the days nobody scrolls past them.
    host: "cyc",

    /** Enter, from the focus ring — and the global key, through `quickStart`.
     *  Always a fresh entry: this panel has no half-finished state worth
     *  resuming, and resuming one would mean the second meal of the day
     *  inherited the first one's hour. */
    onEnter() {
      start();
      return true;
    },

    /**
     * A first-rung answer, pressed at the card rather than inside the ladder.
     *
     * `s` on the meal log means a small meal and always did — it just needed
     * an Enter in front of it to open the ladder the `s` belonged to. Only
     * the first step is offered here, because only the first step is
     * answerable without context: every rung after it is a question that
     * depends on what came before, and there is nothing before yet.
     *
     * The hour is deliberately not reachable this way. `-` and `+` at an
     * idle card would start an entry whose only content was a timestamp,
     * which is a row nobody wants and an easy one to leave half made.
     */
    onKey(e) {
      if (cursor !== null) return false;
      const first = applicableSteps(steps, {})[0];
      const hit = first?.options.find(([key]) => key === e.key);
      if (!hit) return false;
      start();
      return Boolean(handlers?.onKey(e));
    },


    async render(node) {
      // An idle panel shows the hour it *would* log against, not the hour it
      // last logged one at — a card sitting there saying 00:00 all afternoon
      // reads as a broken clock, and the first thing you would do about it is
      // stop trusting the timestamps underneath. Only while the ladder is
      // running is `hour` a choice rather than a reading, and then it is left
      // alone so a redraw cannot undo the `-` you just pressed.
      if (cursor === null) hour = new Date().getHours();

      const message = el("span.faint");
      const clock = el("span.value", { text: `${pad(hour)}:00` });
      const keyHint = el("span.faint");

      const rows = steps.map((step) =>
        el("div.scale-row", null,
          el("div.row.spread", null,
            el("span.faint", { text: step.name }),
            step.hint ? el("span.faint", { text: step.hint }) : null,
          ),
          el("div.scale", null,
            ...step.options.map(([key, value, text]) =>
              el("button.tiny.keyed", {
                text,
                dataset: { key },
                class: draft[step.name] === value ? "on" : "",
                onclick: (e) => {
                  e.currentTarget.blur();
                  // A click that arrives with no ladder running starts one, so
                  // the mouse can log an entry without the keyboard having to
                  // begin it. It answers whatever step it belongs to rather
                  // than the first — clicking `large` means large.
                  if (cursor === null) start();
                  answer(step, value);
                },
              }),
            ),
          ),
        ),
      );

      /** Answer one step, from a tap or from its key, and advance.
       *
       * The paint happens here rather than being left to the next render for
       * the reason the check-in gives: until the ladder finishes there IS no
       * next render, because nothing has been posted, so the first two answers
       * would light nothing at all.
       */
      async function answer(step, value) {
        draft[step.name] = value;
        paint();

        const list = applicable();
        const index = list.indexOf(step);
        const to = next(index);
        if (to !== null) {
          cursor = to;
          paint();
          captureLabel(label());
          return;
        }
        // Finished. The capture is ended by hand here rather than left to the
        // redraw, because the redraw comes from the store notification the
        // post triggers — and that is a round trip later than the moment the
        // keyboard should have gone back to the tabs.
        const body = { hour, ...draft };
        cursor = null;
        endCapture(id);
        // Out of the cell as well as out of the ladder, on the same argument
        // the session log makes: the entry is finished, nothing is being
        // typed, and an Esc pressed at a form that has already posted is a
        // keystroke spent on housekeeping.
        leaveCell();
        try {
          put("health", await api.post(endpoint, body));
          draft = {};
        } catch (error) {
          message.textContent = `failed: ${error.message}`;
        }
      }

      /** Highlight the current step, light the answers, show the hour. */
      function paint() {
        const list = applicable();
        const current = cursor === null ? null : list[cursor];
        for (const [index, step] of steps.entries()) {
          const row = rows[index];
          // A step that does not apply to this entry is hidden rather than
          // dimmed: on a urine entry a stool's solidity row is not an option
          // you are declining, it is a question that was never asked.
          row.hidden = Boolean(step.when) && !list.includes(step);
          row.classList.toggle("on-row", step === current);
          // By index rather than by label: the buttons were built from
          // `options` in order and have not moved, so the two lists line up.
          const buttons = row.querySelector(".scale").children;
          for (const [i, [, value]] of step.options.entries()) {
            buttons[i].classList.toggle("on", draft[step.name] === value);
          }
        }
        clock.textContent = `${pad(hour)}:00`;
        keyHint.textContent = cursor === null ? "⏎ to log one" : "a key each · − + hour · esc";
      }

      function shift(by) {
        hour = (hour + by + 24) % 24;
        paint();
        captureLabel(label());
      }

      function onKey(e) {
        if (EARLIER.has(e.key)) {
          shift(-1);
          return true;
        }
        if (LATER.has(e.key)) {
          shift(1);
          return true;
        }
        const step = applicable()[cursor];
        if (!step) return false;
        const hit = step.options.find(([key]) => key === e.key);
        if (!hit) return false;
        answer(step, hit[1]);
        return true;
      }

      /** Esc, a tab switch, or a key the ladder does not use. A half-answered
       *  entry is dropped rather than kept: there is no partial row worth
       *  posting, and a draft that survived would have the next `b` continue
       *  an entry you had walked away from an hour ago. */
      function onEnd() {
        cursor = null;
        draft = {};
        paint();
      }

      handlers = { onKey, onEnd, paint };

      const today = entries(store.state);
      const logged = today.length
        ? el("div.divider", null,
            ...today.map((entry) =>
              el("div.row.spread", null,
                el("span", null,
                  el("span.faint", { text: `${entry.at} ` }),
                  el("span.value", { text: describe(entry) }),
                ),
                el("button.tiny", {
                  text: "×",
                  title: "delete",
                  onclick: async () => {
                    put("health", await api.post(deleteEndpoint, { entry_id: entry.id }));
                  },
                }),
              ),
            ),
          )
        : el("div.faint.divider", { text: empty });

      mount(node,
        el("div.row.spread.gap", null, clock, keyHint),
        ...rows,
        el("div.row", null, message),
        logged,
      );

      paint();
      // Re-taken after a redraw, because the handlers above close over nodes
      // this render has just replaced. `beginCapture` replaces rather than
      // nests, so this is not a second capture.
      if (cursor !== null) take();
    },
  };
}
