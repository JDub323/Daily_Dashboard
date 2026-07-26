// Inline SVG charts, built as DOM rather than concatenated markup.

import { el, svg } from "./dom.js";

export function sparkline(values, { w = 110, h = 26 } = {}) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - min) / range) * (h - 4)).toFixed(1)}`)
    .join(" ");
  const rising = values[values.length - 1] >= values[0];
  return svg("svg", { width: w, height: h },
    svg("polyline", {
      points,
      fill: "none",
      stroke: rising ? "var(--ok)" : "var(--bad)",
      "stroke-width": 1.5,
    }),
  );
}

export function lineChart(values, { w = 300, h = 80, color = "var(--gold)" } = {}) {
  if (!values || values.length < 2) {
    return el("div.faint", {
      text: values?.length === 1 ? `need ≥2 data points (now: ${values[0]})` : "need ≥2 data points",
    });
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const px = (i) => 34 + (i / (values.length - 1)) * (w - 40);
  const py = (v) => 6 + (1 - (v - min) / range) * (h - 16);
  const points = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");

  const guide = (v) =>
    svg("line", {
      x1: 34, y1: py(v), x2: w, y2: py(v),
      stroke: "var(--border-soft)", "stroke-dasharray": "3,3",
    });
  const label = (v) => svg("text", { x: 30, y: py(v) + 3, "text-anchor": "end" }, String(v));

  // No inline style: the rendered height is `.line-chart`'s business, and
  // `h` only sets the coordinate space the points are computed in.
  return svg("svg.line-chart", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none" },
    guide(max), guide(min), label(max), label(min),
    svg("polyline", { points, fill: "none", stroke: color, "stroke-width": 1.8 }),
    svg("circle", {
      cx: px(values.length - 1),
      cy: py(values[values.length - 1]),
      r: 2.6,
      fill: color,
    }),
  );
}

/** A small labelled chart block: title, current value, chart. */
export function chartBlock(label, current, values, color) {
  return el("div.chart-block", null,
    el("div.chart-title", null, el("span", { text: label }), el("span.gold", { text: current })),
    lineChart(values, { color }),
  );
}
