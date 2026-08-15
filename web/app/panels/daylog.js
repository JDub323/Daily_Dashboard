// One line about today, and the same line from a week and a month ago.
//
// The echoes are the point — a day log you only ever write into is a diary you
// never read. They need days the boot payload does not carry, so they arrive
// after the input is already usable rather than gating it.
//
// The archive of every entry is its own panel further down this tab; this one
// stays about today.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
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
  span: 2,

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

    mount(node, line, message, echoes);

    for (const [days, label] of ECHOES) {
      const past = await api.get("/api/state", { date: api.daysAgo(days) }).catch(() => null);
      if (past?.daylog) {
        echoes.append(el("div.faint", { text: `${label}: ${past.daylog}` }));
      }
    }
  },
};
