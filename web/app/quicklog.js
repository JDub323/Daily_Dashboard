// A panel you fill in with three keystrokes and then forget about.
//
// Three of the health tab's logs — what went in, what you drank, and what came
// out — are the same shape: a handful of questions, each with three or four
// legal answers, asked four to sixteen times a day. Nothing about that is
// hard, but the *cost* is the whole design problem. A meal log that takes
// fifteen seconds and a mouse is a meal log kept for nine days; the entries
// have to cost about as much as not bothering, or the series ends up
// describing which weeks you had the patience to log rather than which weeks
// you ate well.
//
// So the budget is one keystroke per question, and the last one commits.
//
//   m l 4        a large, fairly clean meal, now
//   b s 2 m      a soft stool, medium, now
//   b u 4 l      dark urine, large volume, now
//   w ⏎          one drink of water, now
//
// All three panels are built from a list of steps, here, rather than being
// written three times. They diverge in exactly two ways — bathroom branches on
// its first answer, and drinks asks for a number rather than for one of four
// answers — and those are the `when` predicate and the `field` step below,
// both of which are smaller things than any of the three panels would have
// been on its own.
//
// --- the one question that is not a keystroke -------------------------------
//
// A `field` step is a text box: it is answered by typing and committed with
// Enter, and it exists for exactly one situation — a quantity that is *printed
// on the thing you are holding*. Sizes and volumes are buckets because nobody
// knows how big their lunch was, and a five-point scale is an honest way to
// say so. A bottle says 16.9 fl oz on the label, so bucketing it would be
// throwing away accuracy that cost nothing, and a number is one keystroke over
// budget rather than five.
//
// It is deliberately hard to reach for. Everything about the rest of this file
// assumes a step is answered with one key, and a panel that asked two typed
// questions would not be a quick log any more.
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

/**
 * Keys that move the hour rather than answering the question in front of you.
 *
 * Four spellings each, and the reason is that the obvious two are both awkward
 * to reach: `+` needs a shift on most layouts, and `-` is a stretch from the
 * home row for a key you press two or three times in a row. `,`/`.` sit under
 * the fingers, and `m`/`p` are minus and plus said as words — which is the pair
 * that gets used, because the hour is now the first thing this panel asks and
 * they are what opens it.
 *
 * `m` is safe here despite also meaning *medium* on the meal log's size row:
 * the hour is answered and gone before that row is reached, so the two never
 * compete for the same keystroke.
 */
const EARLIER = new Set(["-", "_", ",", "m"]);
const LATER = new Set(["+", "=", ".", "p"]);

/** `cursor` while the hour is the question. Anything else is an index into
 *  the applicable steps, or null for an idle panel. */
const HOUR = "hour";

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
 * What a `field` step's box will accept, as a number, or null.
 *
 * Three spellings, because all three are what somebody standing at a sink
 * actually types: `1.5`, `.5`, and `1 1/2`. The fraction is not decoration —
 * the unit here is a twelve-ounce drink and half of one is the commonest
 * answer there is, and a box that took `1.5` but silently rejected `1/2` would
 * be a box you learn to distrust rather than one you learn the grammar of.
 *
 * Null for anything else, including the empty string and `0`. The caller shows
 * the box's own hint rather than a validation message: there is one rule, it
 * is printed beside the box, and a red sentence explaining it a second time is
 * a sentence that appears when you are already looking at the answer.
 *
 * `max` is the fat-finger guard the table and the request model both carry. It
 * is checked here as well so a mistyped `15` — which is a legal number, just
 * not a legal answer — is refused by the box that took it rather than by a
 * toast a round trip later.
 */
export function parseAmount(text, max = 20) {
  const said = String(text ?? "").trim();
  if (!said) return null;
  const mixed = said.match(/^(?:(\d+)\s+)?(\d+)\s*\/\s*(\d+)$/);
  const value = mixed
    ? Number(mixed[1] ?? 0) + Number(mixed[2]) / Number(mixed[3])
    : Number(said);
  if (!Number.isFinite(value) || value <= 0 || value > max) return null;
  // Two places is past the precision anyone has about a bottle, and it is what
  // stops a third of a drink arriving as 0.3333333333333333 in a table that
  // will be summed for the rest of the year.
  return Math.round(value * 100) / 100;
}

/**
 * Build one logging panel.
 *
 * `steps` is the ladder, in order: each is `{ name, options, when }` where
 * `options` is `[key, value, label]` triples and `when(draft)` decides whether
 * this step applies to the answers so far. A step whose `when` is false is
 * skipped on the way past, which is all the branching any of the panels needs.
 *
 * A step may instead carry `field: { unit, preset, max }`, and then it is a
 * text box rather than a row of buttons: typed, committed with Enter, parsed
 * by `parseAmount`. See the note at the top for when that is the right answer,
 * which is rarely.
 *
 * The POST body is the answers plus the hour, verbatim — so a step's `name` is
 * the request field it fills, and adding a question to a panel is adding a
 * step here and a column there and nothing in between. `entries(state)` is
 * today's rows out of the store and `describe(entry)` says one of them in a
 * few words.
 *
 * `help` is an optional `() => void` that opens a modal explaining what the
 * answers mean. It draws a `?` beside the clock and nothing else — the rules
 * belong behind it rather than on the card, which is the whole point: a panel
 * whose job is to cost nothing cannot also be a page of definitions.
 */
export function quickLog({
  id,
  title,
  span = 3,
  hourFirst = false,
  steps,
  endpoint,
  deleteEndpoint,
  entries,
  describe,
  empty,
  help,
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

  const label = () => {
    if (cursor === null) return title;
    const step = cursor === HOUR ? "when" : applicable()[cursor]?.name;
    return `${title} ▸ ${step} · ${pad(hour)}:00`;
  };

  /** The next step that applies, given what has been answered. Null when the
   *  ladder is finished — which is the signal to post. */
  function next(from) {
    return from + 1 < applicable().length ? from + 1 : null;
  }

  /**
   * Open the ladder.
   *
   * `at` is where the keyboard lands. `HOUR` is the meal log's first question
   * — which hour this belongs to — and `0` skips straight past it to the
   * first real one, which is what Enter means: the hour is already right.
   *
   * Always a fresh entry. This panel has no half-finished state worth
   * resuming, and resuming one would mean the second meal of the day
   * inherited the first one's hour.
   */
  function start(at = 0) {
    draft = {};
    hour = new Date().getHours();
    cursor = at;
    // Typed answers go back to their preset here rather than after the post,
    // so an entry abandoned half-way does not leave its number lying in the
    // box for the next one to inherit. Every entry starts from the same known
    // default, which is what makes a bare Enter mean something you can predict.
    handlers?.resetFields();
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
    // All three quick logs ride in the rotation with the check-in and the
    // session log. Their old argument for being last in the column — that you
    // are never looking at them when you use them — is unchanged and is now
    // served better: they are reachable in two keystrokes from anywhere and
    // cost no height at all on the days nobody scrolls past them.
    host: "cyc",

    /**
     * Enter, from the focus ring — and the global key, through `quickStart`.
     *
     * Enter means *the hour is already right*, so it opens past the hour and
     * onto the first real question. That is what keeps a meal at three
     * keystrokes (`m` `l` `4`) now that the hour is a question at all: the
     * common case is a meal you are logging as you eat it, and the common case
     * must not pay for the exception.
     */
    onEnter() {
      start(0);
      return true;
    },

    /**
     * A key pressed at the card, which opens it *and* answers with the same
     * press. This is the "one key to enter a cell" rule, and which keys count
     * differs between the logs because their first questions differ.
     *
     * On the meal log the first control is the hour, so `m`/`p`/`-`/`+` open
     * it standing on the hour with that shift already applied — `m` `m` is a
     * meal two hours ago, and Enter from there moves on to the size. Nothing
     * else opens it, which is what keeps `m` unambiguous: at the card it is
     * always minus, and by the time the size row asks, the hour is behind you.
     *
     * On the other two the first control is the kind, so `s` and `u` open the
     * in-and-out log already answered and `w` `e` `s` `c` `a` do the same for
     * drinks. Neither has a global key of its own, and both are answered more
     * often than meals are, so a hidden question in front of either would cost
     * a keystroke on every entry to save one on a few.
     *
     * Whichever keys those are, `panel.js` will not offer this handler one it
     * has reserved for going somewhere else — which is a constraint on the
     * first step of a ladder and on nothing after it, since by then the
     * capture is running and sees the keyboard first. `drinks.js` explains
     * what that cost it.
     */
    onKey(e) {
      if (cursor !== null) return false;
      if (hourFirst) {
        if (!EARLIER.has(e.key) && !LATER.has(e.key)) return false;
        start(HOUR);
        return Boolean(handlers?.onKey(e));
      }
      const first = applicableSteps(steps, {})[0];
      if (!first?.options?.some(([key]) => key === e.key)) return false;
      start(0);
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
      // Wrapped rather than left as two spans in a bare row: it is a step now,
      // and a step has to be able to light up.
      //
      // The `?` rides on the right of the hint rather than beside the card's
      // title, because the title belongs to `panel.js` and this card's title
      // is also its tab. One `?`, in the corner of the panel it explains,
      // which is where the week grid keeps its own.
      const clockRow = el("div.scale-row.row.spread.gap", null,
        clock,
        // `.inline` and not `.gap`: `.gap` is a vertical margin utility, and
        // on a pair of things sitting inside a row it would push them off the
        // clock's baseline for no reason anyone could see.
        el("span.row.inline", null,
          keyHint,
          help
            ? el("button.ghost", {
                text: "?",
                title: `what the answers mean — ${title}`,
                "aria-label": `what the answers on the ${title} log mean`,
                onclick: (e) => {
                  e.currentTarget.blur();
                  help();
                },
              })
            : null,
        ),
      );

      /** The boxes of any `field` steps, by step name, so `paint` can reach
       *  them without a query and `answer` can read them. */
      const boxes = new Map();

      const rows = steps.map((step) => {
        const head = el("div.row.spread", null,
          el("span.faint", { text: step.name }),
          step.hint ? el("span.faint", { text: step.hint }) : null,
        );
        if (step.field) {
          // `text` and not `number`: a number input brings spinners, a scroll
          // wheel that changes the value, and a browser-owned idea of what
          // counts as valid — none of which survive `1 1/2`. `inputMode`
          // still gets the numeric keypad on a phone, which is the only part
          // of `number` that was wanted.
          const box = el("input.tiny.qlog-field", {
            type: "text",
            inputMode: "decimal",
            autocomplete: "off",
            spellcheck: false,
            value: step.field.preset ?? "",
            size: 4,
            "aria-label": step.name,
            onkeydown: (e) => {
              if (e.key !== "Enter") return;
              // The box owns Enter, and it has to: `keys.js` hands the whole
              // keyboard to a focused text field, so nothing upstairs will
              // ever see this one.
              e.preventDefault();
              commitField(step);
            },
          });
          boxes.set(step.name, box);
          return el("div.scale-row", null, head,
            el("div.scale", null,
              box,
              el("span.faint", { text: step.field.unit ?? "" }),
            ),
          );
        }
        return el("div.scale-row", null, head,
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
        );
      });

      /**
       * Read a `field` step's box and answer with it, or say why not.
       *
       * A refusal leaves the ladder exactly where it is, with the text still
       * in the box and selected. That is the only behaviour that is not
       * infuriating: the alternatives are dropping a half-finished entry
       * because of a typo, or posting a number the server will reject after
       * the card has already gone back to sleep.
       */
      function commitField(step) {
        const box = boxes.get(step.name);
        const value = parseAmount(box?.value, step.field.max);
        if (value === null) {
          message.textContent = `${step.name}: a number, ${step.field.unit ?? ""}`.trim();
          box?.select();
          return;
        }
        message.textContent = "";
        answer(step, value);
      }

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
        const current = typeof cursor === "number" ? list[cursor] : null;
        for (const [index, step] of steps.entries()) {
          const row = rows[index];
          // A step that does not apply to this entry is hidden rather than
          // dimmed: on a urine entry a stool's solidity row is not an option
          // you are declining, it is a question that was never asked.
          row.hidden = Boolean(step.when) && !list.includes(step);
          row.classList.toggle("on-row", step === current);
          if (step.field) {
            // Arriving at a typed step *is* focusing its box — there is no
            // second gesture, and a highlighted row you then had to click into
            // would be the one place in this panel where the keyboard stops
            // working.
            //
            // Only on arrival, and only if it is not already ours: `paint`
            // runs on every keystroke and every redraw, and a `select()` on
            // each of those would fight the caret while you type.
            const box = boxes.get(step.name);
            if (step === current && box && document.activeElement !== box) {
              box.focus();
              box.select();
            }
            continue;
          }
          // By index rather than by label: the buttons were built from
          // `options` in order and have not moved, so the two lists line up.
          const buttons = row.querySelector(".scale").children;
          for (const [i, [, value]] of step.options.entries()) {
            buttons[i].classList.toggle("on", draft[step.name] === value);
          }
        }
        clock.textContent = `${pad(hour)}:00`;
        // The clock is a question like any other while it is the one being
        // asked, and is lit the same way the rows are, or the ladder would be
        // standing somewhere with nothing on screen saying where.
        clockRow.classList.toggle("on-row", cursor === HOUR);
        keyHint.textContent =
          cursor === null
            ? (hourFirst ? "⏎ now · m p to shift" : "⏎ to log one")
            : cursor === HOUR
              ? "m earlier · p later · ⏎ accepts"
              : current?.field
                // The hour keys are not offered here and must not be: the box
                // has the keyboard, `.` is a decimal point and `m` is a letter
                // you might be typing. Esc still leaves, because Esc is the
                // one binding that outranks a text field.
                ? "type it · ⏎ logs it · esc"
                : "a key each · m p hour · esc";
      }

      function shift(by) {
        hour = (hour + by + 24) % 24;
        paint();
        captureLabel(label());
      }

      function onKey(e) {
        // The hour is answerable from anywhere in the ladder, not only from its
        // own step: realising halfway through that this was lunch and not a
        // snack should not mean starting the entry again. It is only *asked*
        // first; it stays available after.
        //
        // The one place it must not win is the meal log's size row, where `m`
        // means medium. `EARLIER` carries `m` for the hour step, so the step's
        // own options are matched first and the hour keys only get what is
        // left over.
        const step = cursor === HOUR ? null : applicable()[cursor];
        const hit = step?.options?.find(([key]) => key === e.key);
        if (hit) {
          answer(step, hit[1]);
          return true;
        }
        if (EARLIER.has(e.key)) {
          shift(-1);
          return true;
        }
        if (LATER.has(e.key)) {
          shift(1);
          return true;
        }
        // Enter accepts the hour and moves on. Only from the hour step: on an
        // answer row there is nothing for it to accept, and a stray Enter
        // there should fall through and leave rather than silently skip a
        // question.
        if (cursor === HOUR && e.key === "Enter") {
          cursor = 0;
          paint();
          captureLabel(label());
          return true;
        }
        return false;
      }

      /** Esc, a tab switch, or a key the ladder does not use. A half-answered
       *  entry is dropped rather than kept: there is no partial row worth
       *  posting, and a draft that survived would have the next `b` continue
       *  an entry you had walked away from an hour ago. */
      function onEnd() {
        cursor = null;
        draft = {};
        // Out of the box as well as out of the ladder. Esc reaches here from
        // inside a focused text field — that is the one gate in `keys.js` it
        // is allowed through — and a cursor left blinking in a panel that has
        // stopped listening is the state where every subsequent keystroke
        // silently does nothing.
        for (const box of boxes.values()) {
          if (document.activeElement === box) box.blur();
        }
        paint();
      }

      function resetFields() {
        for (const step of steps) {
          if (step.field) boxes.get(step.name).value = step.field.preset ?? "";
        }
        message.textContent = "";
      }

      handlers = { onKey, onEnd, paint, resetFields };

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
        clockRow,
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
