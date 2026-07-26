import * as api from "../api.js";
import { el, link, mount } from "../dom.js";

export default {
  id: "news",
  title: "headlines",
  col: "left",

  async render(node) {
    mount(node, el("div.faint", { text: "fetching feeds…" }));
    const items = await api.get("/api/news");
    mount(node,
      el("ul.clean", null,
        ...items.map((item) =>
          el("li", null, el("span.tag", { text: item.cat }), " ", link(item.link, item.title)),
        ),
      ),
    );
  },
};
