// What is owed, and when.
//
// The list is filled from the calendar box above it — there is no second input
// here, on purpose. One grammar, one place to type, and this panel is the
// *answer* rather than a second way to ask. What arrives here is whatever the
// box sent to a calendar named in `[todo].todo_calendars`; everything else it
// writes is an ordinary event and never appears.
//
// Three things about the layout are decisions rather than defaults:
//
// Overdue work sits at the top and stays there. Nothing rolls forward and
// nothing is hidden after a week — a reading you skipped is still owed, and a
// list that quietly drops it is a list that helps you forget it.
//
// Days are the grouping, not subjects. Subjects are what you *typed*; days are
// what you have to act on, and the question this panel answers at 9am is "what
// is due today", never "how much Operating Systems is there in total".
//
// The subject is a *colour* rather than a chip, and that swap is most of what
// makes the list readable. A chip repeating "US National Security Policy
// Making" beside a title that begins "US National Security Policy Making:" is
// the same eleven words twice on one row, and it was pushing every line to
// three. Now the alias tag carries the subject in the three letters you
// actually typed, the rail down the left carries it as colour, and the title
// has its own subject stripped off the front — so the row reads "Quiz on the
// Civil War and Saving the Union", which is the part that identifies it.
//
// The hues come from `hueMap` in `dayplan.js` — the same function the week grid
// colours its calendars with, and now the same one the phone's todo list uses.
// Same twelve-hue palette, same rule that gold is reserved for "done" — a subject
// and a calendar are different things, but they are both "one of a small set of
// named categories that has to be told apart on a 3px border", and two
// colouring schemes on one screen is one more than anybody can hold.
//
// Undated work is a second list under a rule. It is the same rows with no day
// on them — "fix the coffee table" — and it is kept below rather than mixed in
// because a list sorted by date cannot hold something that has no date without
// inventing a position for it. See `todo.py`: these are the rows that exist
// only here, with no Google event behind them.
//
// Ticking is one click and it is local-first: the row greys out immediately,
// and the Google event is deleted behind it. When that delete fails the item
// stays done and says its event is still standing, because an unreachable
// Google is not a reason to refuse to mark homework done.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { get as getPanel, rerender } from "../panel.js";
import { hueMap } from "../dayplan.js";
// The grouping and the two shortenings live in `../todolist.js` now, and are
// re-exported here because they were this file's public surface before the
// phone had a todo screen of its own. Where a row goes is as much the
// definition of the list as the rows are, and two clients that group
// differently produce two different backlogs off one table.
export { groupByDay, shorten, undatedItems } from "../todolist.js";
import { clock, dayLabel, groupByDay, keyFor, shorten, undatedItems } from "../todolist.js";

export default {
  id: "todo",
  title: "✦ what is owed",
  tab: "home",
  // Beside the year rather than under it. Both are worth about half the width
  // and neither fills it, so stacking them left a full-width card with an empty
  // half beside it and pushed this one under the fold. See `.hero` in the
  // stylesheet, which grew six columns for exactly this.
  slot: "hero",
  hero: true,
  span: 3,

  async render(node) {
    const panel = await api.get("/api/todo", { date: api.today() });
    const todayISO = api.today();
    const groups = groupByDay(panel.items, todayISO);
    const undated = undatedItems(panel.items);
    const finished = panel.items.filter((i) => i.done);

    // One hue per subject, assigned by the same function the week grid colours
    // its calendars with. Over every subject in the payload rather than over
    // the aliases in config, so an item whose class has since been removed from
    // `[todo.subjects]` still gets a colour instead of falling to the default.
    const subjects = [...new Set(panel.items.map((i) => i.subject).filter(Boolean))];
    const hues = hueMap(subjects);
    const shortKey = keyFor(panel.aliases);

    const tick = async (item, done) => {
      await api.postRaw("/api/todo/done", {
        todo_id: item.id,
        done,
        today: todayISO,
      });
      // Ticking an item deletes its Google event, which means the week grid
      // above is now drawing something that is gone. It holds its own weeks in
      // a map, so telling it to redraw is not enough — it has to be told to
      // forget first, or it will faithfully repaint the event we just removed.
      const cal = getPanel("cal");
      cal?.forget?.();
      rerender("cal");
      await this.render(node);
    };

    // A row is a button, not a checkbox in a label.
    //
    // The box itself was the largest thing on the row — a 13px control holding
    // the full line-height open, repeated down a list whose actual content is
    // three words and a time. What it bought was an affordance nobody needed
    // twice: this list has exactly one verb. So the row *is* the control, and
    // the check appears in the right margin under the cursor, where it costs
    // no layout at rest. A `<button>` rather than a clickable div because that
    // is what `nav.ITEMS` walks, so `i` still steps the list and Enter still
    // ticks — the keyboard path is unchanged.
    const row = (item) =>
      el("button.todo-row", {
        class: `${item.done ? "done" : ""}${item.subject ? " has-subject" : ""}`,
        // The full stored title, which is the one on the Google event — so the
        // thing the row is named after is always one hover away even though the
        // line on screen has had its subject taken off the front.
        title: `${item.title}\n${item.done ? "mark as still to do" : "mark done"}`,
        style: item.subject ? `--hue:${hues.get(item.subject)}` : null,
        onclick: () => tick(item, !item.done),
      },
        el("span.todo-title", { text: shorten(item.title, item.subject) }),
        item.subject ? el("span.todo-key", { text: shortKey(item.subject) }) : null,
        item.start ? el("span.todo-at", { text: clock(item.start) }) : null,
        // Only ever shown when it is true, and it is true only after a delete
        // that failed. Silence is the normal state — and an undated item never
        // had an event, so it can never say this.
        item.done && !item.event_deleted
          ? el("span.todo-stuck", { title: "still on the calendar — gcalcli could not remove it",
              text: "on calendar" })
          : null,
      );

    mount(node,
      groups.length
        ? el("div.todo-list", null,
            ...groups.map((group) =>
              el("div.todo-day", { class: group.overdue ? "overdue" : "" },
                el("div.todo-when", {
                  text: group.overdue ? `overdue · ${group.items.length}`
                                      : dayLabel(group.date, todayISO),
                }),
                ...group.items.map(row),
              ),
            ),
          )
        : el("div.faint", { text: "nothing due — add a line to the week above" }),

      // The rule is drawn whenever there is anything under it. Absent on an
      // account with no undated work, rather than a heading over an empty
      // list saying a feature exists — the box's own refusal message is where
      // that is advertised, at the moment it is useful.
      undated.length
        ? el("div.todo-undated", null,
            el("div.todo-when", { text: `no date · ${undated.length}` }),
            ...undated.map(row),
          )
        : null,

      finished.length
        ? el("details.todo-done-box", null,
            el("summary.faint", { text: `${finished.length} done recently` }),
            ...finished.map(row),
          )
        : null,
    );
  },
};
