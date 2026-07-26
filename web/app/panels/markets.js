import * as api from "../api.js";
import { sparkline } from "../charts.js";
import { el, mount } from "../dom.js";

function row(quote) {
  if (quote.error || typeof quote.price !== "number") {
    return el("div.row", null,
      el("b", { text: quote.ticker }),
      el("span.err", { text: quote.error || "no data" }),
    );
  }
  const change = quote.change_pct;
  return el("div.row.spread", null,
    el("span", null,
      el("b", { text: quote.ticker }), " ",
      el("span.big", { text: quote.price.toFixed(2) }), " ",
      // A ticker with a single close has a price but no comparison point.
      change == null
        ? el("span.faint", { text: "—" })
        : el(`span.${change >= 0 ? "ok" : "bad"}`, { text: `${change >= 0 ? "+" : ""}${change}%` }),
    ),
    sparkline(quote.spark),
  );
}

export default {
  id: "mkt",
  title: "markets",
  col: "left",

  async render(node) {
    mount(node, el("div.faint", { text: "fetching quotes…" }));
    mount(node, ...(await api.get("/api/markets")).map(row));
  },
};
