// Headlines, split across the tabs that want them.
//
// `/api/news` returns one quota-balanced list tagged by category, which was
// exactly right for a single "headlines" card and is wrong now: politics,
// finance and tech each belong to a different section. So the panel became a
// factory, and the categories are divided up at the bottom of this file.
//
// One fetch serves all three. The endpoint also *records* the links it returns
// as seen — that is idempotent within a day, but three panels each firing
// their own request would still be three round trips for one answer, and
// three independently-aged `feed` stamps claiming different fetch times for
// the same data.

import * as api from "../api.js";
import { el, link, mount } from "../dom.js";
import { feedStamp, refreshAfter } from "../freshness.js";

let inflight = null;
let stamp = null;

/**
 * The shared fetch. Panels rendering in the same tick get one request; the
 * first to render after that window starts a fresh one.
 *
 * `stamp` is the date the in-flight promise was issued for, so a rollover past
 * midnight cannot serve yesterday's list to whichever panel asks next.
 */
function fetchNews(date) {
  if (!inflight || stamp !== date) {
    stamp = date;
    inflight = api.get("/api/news", { date }).finally(() => {
      // Cleared on the microtask queue rather than kept: the panels' own
      // refresh timers are what decide when this is stale, and holding the
      // resolved promise here would defeat them.
      queueMicrotask(() => {
        inflight = null;
      });
    });
  }
  return inflight;
}

/**
 * @param spec.cats  categories this panel claims, or null for "everything no
 *                   other panel claimed" — so a category added to config.toml
 *                   shows up somewhere instead of vanishing.
 */
export function newsPanel(spec) {
  return {
    id: spec.id,
    title: spec.title,
    tab: spec.tab,
    slot: spec.slot,
    span: spec.span,
    hero: spec.hero,
    live: true,

    async render(node) {
      mount(node, el("div.faint", { text: "fetching feeds…" }));
      const news = await fetchNews(api.today());
      const items = news.items.filter((item) =>
        spec.cats ? spec.cats.includes(item.cat) : !CLAIMED.has(item.cat),
      );

      mount(node,
        items.length
          ? el("ul.clean", null,
              ...items.map((item) =>
                el("li", null,
                  // The tag earns its place only where a panel carries more
                  // than one category. On the finance card every row would
                  // read "finance", which is what the heading already says.
                  spec.cats?.length === 1 ? null : el("span.tag", { text: item.cat }),
                  spec.cats?.length === 1 ? null : " ",
                  link(item.link, item.title),
                ),
              ),
            )
          : el("div.faint", { text: "nothing new in this section today" }),
        feedStamp(news.feed),
      );
      return refreshAfter(news.feed);
    },
  };
}

/** Categories with a home of their own. Everything else falls to the catch-all. */
const CLAIMED = new Set(["finance"]);

// Demoted from the hero when the investigation arrived, and widened again when the
// newsletters tab was folded away: the "elsewhere" card that used to live there
// was the catch-all, and the catch-all is now this one. So the rule is simply
// "everything the money pages did not take" — a category added to config.toml
// still shows up somewhere instead of vanishing, and it does not need a tab of
// its own to do it.
//
// `cats: null` also means the per-row category tag comes back, which it has to:
// this card is no longer one subject.
export const politics = newsPanel({
  id: "news-politics",
  title: "headlines",
  tab: "politics",
  slot: "grid",
  span: 2,
  cats: null,
});

export const finance = newsPanel({
  id: "news-finance",
  title: "money pages",
  tab: "markets",
  slot: "grid",
  span: 3,
  cats: ["finance"],
});
