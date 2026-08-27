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
// is due today", never "how much Operating Systems is there in total". The
// subject rides along as a chip so the eye can still find one course's work.
//
// Ticking is one click and it is local-first: the row greys out immediately,
// and the Google event is deleted behind it. When that delete fails the item
// stays done and says its event is still standing, because an unreachable
// Google is not a reason to refuse to mark homework done.

import * as api from "../api.js";
import { el, mount } from "../dom.js";

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

function parseISO(iso) {
  // Not `new Date(iso)`: that reads a bare date as UTC and renders it local,
  // which moves every due date back a day west of Greenwich.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const dayLabel = (iso, todayISO) => {
  if (iso === todayISO) return "today";
  const date = parseISO(iso);
  const days = Math.round((date - parseISO(todayISO)) / 86400000);
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  const stamp = `${DAY_NAMES[date.getDay()]} ${MONTHS[date.getMonth()]} ${date.getDate()}`;
  // Inside the coming week the weekday is the useful half, past that it is the
  // date — "Thu" a month out tells you nothing you can act on.
  return days > 1 && days < 7 ? `${DAY_NAMES[date.getDay()]}` : stamp;
};

const clock = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
};

/**
 * Group into days, overdue first and then forward in time.
 *
 * Everything before today collapses into one "overdue" bucket rather than
 * keeping its own date heading. Six headings each holding one late item is a
 * panel that looks like a backlog of dates; one heading holding six is a
 * backlog of work, which is the thing that has to be dealt with.
 */
export function groupByDay(items, todayISO) {
  const overdue = [];
  const days = new Map();
  for (const item of items) {
    if (item.done) continue;
    if (item.due_date < todayISO) {
      overdue.push(item);
      continue;
    }
    if (!days.has(item.due_date)) days.set(item.due_date, []);
    days.get(item.due_date).push(item);
  }
  const groups = [...days.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, list]) => ({ date, items: list, overdue: false }));
  return overdue.length ? [{ date: null, items: overdue, overdue: true }, ...groups] : groups;
}

export default {
  id: "todo",
  title: "due",
  tab: "home",
  slot: "grid",
  span: 3,

  async render(node) {
    const panel = await api.get("/api/todo", { date: api.today() });
    const todayISO = api.today();
    const groups = groupByDay(panel.items, todayISO);
    const finished = panel.items.filter((i) => i.done);

    const tick = async (item, done) => {
      await api.postRaw("/api/todo/done", {
        todo_id: item.id,
        done,
        today: todayISO,
      });
      await this.render(node);
    };

    const row = (item) =>
      el("label.todo-row", { class: item.done ? "done" : "" },
        el("input", {
          type: "checkbox",
          checked: item.done,
          onchange: (e) => tick(item, e.target.checked),
        }),
        el("span.todo-title", { text: item.title }),
        item.subject ? el("span.todo-subject", { text: item.subject }) : null,
        item.start ? el("span.todo-at", { text: clock(item.start) }) : null,
        // Only ever shown when it is true, and it is true only after a delete
        // that failed. Silence is the normal state.
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
      finished.length
        ? el("details.todo-done-box", null,
            el("summary.faint", { text: `${finished.length} done recently` }),
            ...finished.map(row),
          )
        : null,
    );
  },
};
