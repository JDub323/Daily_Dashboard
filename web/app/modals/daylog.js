import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openModal } from "../ui.js";

const ECHOES = [
  [7, "a week ago"],
  [30, "a month ago"],
];

export function openDaylogModal() {
  openModal("day log", async (body) => {
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

    mount(body, line, message);

    // The echoes need days the boot payload doesn't carry, so they arrive
    // after the input is already usable.
    for (const [days, label] of ECHOES) {
      const past = await api.get("/api/state", { date: api.daysAgo(days) }).catch(() => null);
      if (past?.daylog) body.prepend(el("div.faint", { text: `${label}: ${past.daylog}` }));
    }
  });
}
