import * as api from "../api.js";
import { lineChart } from "../charts.js";
import { el } from "../dom.js";
import { put, store } from "../store.js";

const METRICS = [
  { metric: "weight", label: "weight", unit: " lb", color: "var(--gold)" },
  { metric: "sleep", label: "sleep score", unit: "", color: "var(--accent)" },
];

export default {
  id: "health",
  title: "health",
  tab: "health",
  slot: "hero",
  hero: true,

  async render(node) {
    const byMetric = Object.fromEntries(store.state.health.map((s) => [s.metric, s.points]));

    const block = ({ metric, label, unit, color }) => {
      const points = byMetric[metric] ?? [];
      const last = points[points.length - 1];
      const input = el("input", { type: "number", step: "0.1", placeholder: `log today's ${label}` });
      const log = el("button", {
        text: "log",
        onclick: async () => {
          const value = Number.parseFloat(input.value);
          if (Number.isNaN(value) || value <= 0) return;
          // UNIQUE(metric, date): a second entry today is an UPSERT, not the
          // find-then-mutate-else-append dance this used to be.
          put("health", await api.post("/api/health", { metric, value }));
          await this.render(node);
        },
      });
      return el("div.chart-block", null,
        el("div.chart-title", null,
          el("span", { text: label }),
          el("span.gold", { text: last ? `${last[1]}${unit}` : "—" }),
        ),
        lineChart(points.map(([, value]) => value), { color }),
        el("div.row", null, input, log),
      );
    };

    node.replaceChildren(
      ...METRICS.map(block),
      el("div.faint", { text: "manual for now — Apple Health import can slot in here later" }),
    );
  },
};
