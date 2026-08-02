import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { feedStamp, refreshAfter } from "../freshness.js";

export default {
  id: "news",
  title: "headlines",
  col: "left",
  live: true,

  async render(node) {
    mount(node, el("div.faint", { text: "fetching feeds…" }));
    // Dated: the server suppresses links it has already shown you, and the
    // window is measured in days that only the browser knows the bounds of.
    const news = await api.get("/api/news", { date: api.today() });
    mount(node,
      el("ul.clean", null,
        ...news.items.map((item) =>
          el("li", null, el("span.tag", { text: item.cat }), " ", link(item.link, item.title)),
        ),
      ),
      feedStamp(news.feed),
    );
    // Refetching also re-records these links as seen, which is idempotent for
    // the same day — but it is the reason the poll follows the server's cache
    // window rather than some tighter interval of its own.
    return refreshAfter(news.feed);
  },
};
