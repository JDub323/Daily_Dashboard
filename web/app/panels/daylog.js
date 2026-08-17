// One line about today, and the same line from a week and a month ago.
//
// The echoes are the point — a day log you only ever write into is a diary you
// never read. They need days the boot payload does not carry, so they arrive
// after the input is already usable rather than gating it.
//
// The whole archive used to be a second card further down this tab. It is a
// button here now: two entries deep is what you want while writing, and every
// entry is what you want occasionally, which is exactly the difference between
// a panel and a modal.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { openDaylogArchive } from "../modals/daylogs.js";
import { put, store } from "../store.js";

const ECHOES = [
  [7, "a week ago"],
  [30, "a month ago"],
];

export default {
  id: "log",
  title: "day log",
  tab: "home",
  slot: "grid",
  span: 3,

  async render(node) {
    const message = el("div.faint.gap", { text: store.state.daylog ? "saved ✓" : "" });
    const line = el("input", {
      placeholder: "one line about today",
      value: store.state.daylog ?? "",
      onchange: async (e) => {
        await api.post("/api/daylog", { text: e.target.value });
        put("daylog", e.target.value);
        message.textContent = "saved ✓";
      },
    });
    const echoes = el("div");

    mount(node, line, message, echoes,
      el("div.row.gap", null,
        el("button", { text: "past logs", onclick: openDaylogArchive }),
      ),
    );

    for (const [days, label] of ECHOES) {
      const past = await api.get("/api/state", { date: api.daysAgo(days) }).catch(() => null);
      if (past?.daylog) {
        echoes.append(el("div.faint", { text: `${label}: ${past.daylog}` }));
      }
    }
  },
};
