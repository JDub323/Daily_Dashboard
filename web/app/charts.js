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

/**
 * Redraw an SVG whenever its box changes, in a coordinate space that *is* that
 * box.
 *
 * This exists because of what the charts looked like without it. They were
 * drawn in a fixed 300×80 space and stretched to fit with
 * `preserveAspectRatio="none"`, which is fine for a polyline and wrong for
 * everything else in the picture: on a 560px card the horizontal scale was
 * 1.9× and the vertical scale was 1×, so every glyph on the y-axis came out
 * nearly twice as wide as it was tall. Letter-spacing, stroke widths and the
 * end-of-series dot were all distorted by the same factor — the dot was an
 * ellipse — but the axis labels were where you could see it.
 *
 * Fitting the viewBox to the element's own pixel width makes the scale exactly
 * 1:1 in both directions, so `svg text` renders at the same 11px as the rest of
 * the app and a circle is round. `preserveAspectRatio` goes back to its default
 * for the frames between a resize and the callback: `meet` letterboxes for one
 * frame where `none` would flash a stretched chart.
 *
 * A width of 0 is ignored rather than drawn: every pane but the active one is
 * `hidden` at boot, so most charts are first observed at zero and get their
 * real width the moment their tab is shown.
 */
const FALLBACK_W = 300;

function fitted(node, h, paint) {
  let current = 0;
  const draw = (w) => {
    if (w <= 0 || w === current) return;
    current = w;
    node.setAttribute("viewBox", `0 0 ${w} ${h}`);
    node.replaceChildren(...paint(w));
  };

  draw(FALLBACK_W);
  // Measured on the next task rather than waiting for the observer's first
  // callback. The node is in the document by then — panels mount and then
  // render — so this is the width for anything on the tab you are looking at,
  // and the chart is right on the frame it first appears rather than the one
  // after. `getBoundingClientRect` forces a layout, which is the point.
  setTimeout(() => draw(Math.round(node.getBoundingClientRect().width)), 0);
  // And the observer for everything else: a window resize, and the tabs that
  // are `hidden` at boot and therefore first measure zero.
  new ResizeObserver(([entry]) => draw(Math.round(entry.contentRect.width))).observe(node);
  return node;
}

/** `w` is only what it is drawn at before the first measurement lands. */
export function lineChart(values, { h = 80, color = "var(--gold)" } = {}) {
  if (!values || values.length < 2) {
    return el("div.faint", {
      text: values?.length === 1 ? `need ≥2 data points (now: ${values[0]})` : "need ≥2 data points",
    });
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const py = (v) => 6 + (1 - (v - min) / range) * (h - 16);

  // No inline style: the rendered height is `.line-chart`'s business, and
  // `h` only sets the coordinate space the points are computed in.
  return fitted(svg("svg.line-chart"), h, (w) => {
    const px = (i) => 34 + (i / (values.length - 1)) * (w - 40);
    const points = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
    const guide = (v) =>
      svg("line", {
        x1: 34, y1: py(v), x2: w, y2: py(v),
        stroke: "var(--border-soft)", "stroke-dasharray": "3,3",
      });
    const label = (v) => svg("text", { x: 30, y: py(v) + 3, "text-anchor": "end" }, String(v));

    return [
      guide(max), guide(min), label(max), label(min),
      svg("polyline", { points, fill: "none", stroke: color, "stroke-width": 1.8 }),
      svg("circle", {
        cx: px(values.length - 1),
        cy: py(values[values.length - 1]),
        r: 2.6,
        fill: color,
      }),
    ];
  });
}

export { fitted };
