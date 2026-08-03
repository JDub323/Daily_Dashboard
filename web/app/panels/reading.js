// arXiv triage plus the reading queue.
//
// current / queue / archive were three lists holding one entity, spliced by
// hand in the browser. They are one `paper` table with a status now, and the
// splicing is four endpoints.

import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { put, store } from "../store.js";

const ACTIONS = [
  { action: "up", label: "↑", cls: "" },
  { action: "read", label: "read", cls: "ok" },
  { action: "drop", label: "×", cls: "bad" },
];

export default {
  id: "reading",
  title: "reading",
  tab: "papers",
  slot: "hero",
  hero: true,

  async render(node) {
    let papers = store.state.papers;
    if (!papers.surfaced) {
      mount(node, el("div.faint", { text: "fetching arXiv…" }));
      papers = put("papers", await api.get("/api/papers/today", { date: api.today() }));
    }

    const update = async (promise) => {
      put("papers", await promise);
      await this.render(node);
    };

    const paper = papers.surfaced;
    // No candidate is now a real state, not a failure: every paper arXiv
    // offered has already been triaged, so there is genuinely nothing new.
    // Before the dedupe this could not happen — it just re-showed one.
    const header = paper
      ? [
          el("div.tag", { text: paper.published ? `arXiv — ${paper.published}` : "arXiv" }),
          el("div.paper-title", null, link(paper.link, paper.title)),
          paper.summary ? el("div.dim.small", { text: `${paper.summary}…` }) : null,
          paper.status !== "surfaced"
            ? el("div.row", null, el("span.ok", { text: `marked: ${paper.status}` }))
            : el("div.row", null,
                ...["read", "queued", "skipped"].map((mark) =>
                  el("button", {
                    text: mark === "queued" ? "→ queue" : mark === "read" ? "read it" : "skip",
                    onclick: () => update(api.post("/api/paper/triage", { mark })),
                  }),
                ),
              ),
        ]
      : [
          el("div.tag", { text: "arXiv" }),
          el("div.faint", { text: "nothing new — every match has been triaged" }),
          el("div.faint.small", { text: "widen [arxiv].query to see more" }),
        ];

    const queueItem = (item, index) =>
      el("li", null,
        el("span.faint", { text: `${index + 1}.` }), " ", link(item.link, item.title), " ",
        el("span.row.inline", null,
          ...ACTIONS.map(({ action, label, cls }) =>
            el(`button${cls ? `.${cls}` : ""}`, {
              text: label,
              onclick: () =>
                update(api.post("/api/paper/queue/action", { paper_id: item.id, action })),
            }),
          ),
        ),
      );

    const url = el("input", { placeholder: "paste a link to queue it" });
    const addMsg = el("span.faint");
    const add = el("button", {
      text: "add",
      onclick: async () => {
        const value = url.value.trim();
        if (!value) return;
        try {
          // No scheme check here: the request model only admits http(s), so a
          // `javascript:` paste fails validation instead of reaching the DOM.
          await update(api.post("/api/paper/queue", { url: value }));
        } catch (error) {
          addMsg.textContent = error.message;
        }
      },
    });

    mount(node, ...header.filter(Boolean),
      el("div.faint.divider", { text: `queue · ${papers.cleared_count} cleared` }),
      papers.queue.length
        ? el("ul.clean", null, ...papers.queue.map(queueItem))
        : el("ul.clean", null, el("li.faint", { text: "empty" })),
      el("div.row", null, url, add),
      addMsg,
    );
  },
};
