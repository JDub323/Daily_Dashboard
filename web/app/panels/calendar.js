import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { feedStamp, refreshAfter } from "../freshness.js";

function list(items, empty, format) {
  if (!items.length) return el("ul.clean", null, el("li.faint", { text: empty }));
  return el("ul.clean", null, ...items.map(format));
}

export default {
  id: "cal",
  title: "calendar",
  tab: "home",
  slot: "grid",
  live: true,

  async render(node) {
    const message = el("div.faint");
    const input = el("input", { placeholder: 'quick add: "essay due Fri 5pm"' });
    const add = el("button", {
      text: "add",
      onclick: async () => {
        const text = input.value.trim();
        if (!text) return;
        message.textContent = "adding…";
        try {
          // The one POST with no date: gcalcli parses the day out of the text.
          await api.postRaw("/api/calendar/add", { text });
          input.value = "";
          await this.render(node);
        } catch (error) {
          message.textContent = `failed: ${error.message}`;
        }
      },
    });
    const addBox = [el("div.row", null, input, add), message];

    mount(node, el("div.faint", { text: "loading gcalcli…" }));
    let calendar;
    try {
      calendar = await api.get("/api/calendar", { date: api.today() });
    } catch (error) {
      // Handled here rather than thrown, because the quick-add box has to
      // survive gcalcli being down — it is how you fix the thing that is
      // wrong. The harness's error card would replace the whole panel.
      mount(node,
        el("div.err", { text: `calendar: ${error.message}` }),
        el("div.row", null, el("button", { text: "retry", onclick: () => this.render(node) })),
        ...addBox,
      );
      return;
    }

    mount(node,
      el("div.big", { text: "today" }),
      list(calendar.today, "nothing scheduled", (ev) =>
        el("li", null, el("span.dim", { text: ev.start || "all day" }), " ", ev.title)),
      el("div.big", { text: "this week" }),
      list(calendar.week, "clear", (ev) =>
        el("li", null, el("span.dim", { text: ev.date }), " ", ev.title)),
      ...addBox,
      feedStamp(calendar.feed),
    );
    return refreshAfter(calendar.feed);
  },
};
