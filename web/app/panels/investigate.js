// The investigation of the day: the pool, and the one topic drawn from it.
//
// The class hands out a list of things to look into. They land here in a
// paste, one per line, and each morning one of them is drawn. That is the
// whole panel at the moment — the write-up and the ballot come next, and they
// hang off the same drawn row.
//
// [Invariant] Drawing is a button, never something that happens on render. A
// panel that drew on mount would spend a topic every morning the politics tab
// was painted, which is every morning. Same rule as the theorem draw and the
// debate session, and the reason `/api/investigate/draw` is a POST.
//
// The pool travels with the boot payload rather than being fetched: it is a
// few hundred short strings at the very worst, and it has to be on screen to
// be struck off — a list you cannot see is a list that only ever grows.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openEditor, ready as termReady } from "../term.js";
import { toast } from "../ui.js";

/**
 * The live editor, if one is open. At most one, ever.
 *
 * Module-level rather than per-render because the panel is rebuilt by any
 * store change and a terminal rebuilt underneath you is a lost buffer. The
 * node is kept and re-attached; only an explicit close disposes it.
 */
let editor = null;

function closeEditor() {
  editor?.dispose();
  editor = null;
}

/**
 * What is half-typed in the box, kept across re-renders.
 *
 * The panel is redrawn by any store change — a feed landing, a chip update, a
 * rollover check — and each redraw builds a fresh textarea. Without this, half
 * a pasted handout disappears because something unrelated finished loading.
 * The debate panel learned this the same way.
 */
let draft = "";

/** The pool, listed so it can be struck off. */
function pool(panel, redraw) {
  if (!panel.pool.length) return null;

  const strike = (item) =>
    el("button.tiny", {
      text: "✕",
      title: "strike this off — not worth a day",
      onclick: async () => {
        put("investigate", await api.post("/api/investigate/clear", {
          investigation_id: item.id,
        }));
        redraw();
      },
    });

  return el("div.gap", null,
    el("div.faint", {
      text: `${panel.pool_size} waiting`
        + (panel.investigated ? ` · ${panel.investigated} done` : ""),
    }),
    el("ul.clean", null,
      ...panel.pool.map((item) =>
        el(`li${panel.today?.id === item.id ? ".read" : ""}`, null,
          strike(item),
          " ",
          el("span", { text: item.topic }),
          // Only on the one that is also today's, so the list does not repeat
          // the date twenty times over for no one.
          panel.today?.id === item.id ? el("span.tag", { text: "today" }) : null,
        ),
      ),
    ),
  );
}

/** The paste box. One topic per line, the whole handout at once. */
function box(redraw) {
  const text = el("textarea.recall-box", {
    rows: 3,
    value: draft,
    placeholder: "today's list — one per line. Bullets and numbering are stripped.",
    oninput: (e) => { draft = e.target.value; },
  });
  const message = el("span.faint");

  const submit = el("button", {
    text: "add to the pool",
    onclick: async () => {
      if (!text.value.trim()) {
        message.textContent = "nothing in the box";
        return;
      }
      submit.disabled = true;
      try {
        const got = await api.post("/api/investigate/add", { text: text.value });
        put("investigate", got.panel);
        draft = "";
        // Said out loud, because the two interesting outcomes are both
        // silent otherwise: a paste of six that added four, and a paste
        // that added nothing because it was yesterday's list again.
        const rejected = got.lines.filter((line) => line.error);
        // The default (error) styling for a paste that added nothing, because
        // that is the one outcome you have to notice: it means the handout was
        // already in the pool and the box you just filled in did nothing.
        if (got.added) {
          toast(
            `${got.added} added${rejected.length ? `, ${rejected.length} skipped` : ""}`,
            "info",
          );
        } else {
          toast("nothing new in that list");
        }
        redraw();
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div.gap", null,
    text,
    el("div.row", null, submit, message),
  );
}

/** Nothing drawn yet: the button, or the reason there is none. */
function undrawn(panel, redraw) {
  if (!panel.pool_size) {
    return el("div.faint", {
      text: "nothing in the pool — paste the day's list below and draw from it",
    });
  }

  const button = el("button.primary", {
    text: "draw today's topic",
    onclick: async () => {
      button.disabled = true;
      button.textContent = "drawing…";
      try {
        put("investigate", await api.post("/api/investigate/draw"));
        redraw();
      } catch (error) {
        button.disabled = false;
        button.textContent = "draw today's topic";
        toast(error.message);
      }
    },
  });

  return el("div", null,
    el("div.row", null, button),
    el("div.faint", {
      text: `${panel.pool_size} to choose from · the draw is fixed once made, a reload will `
        + "not reroll it",
    }),
  );
}

/** Drawn: what today is about, and the editor it is written in. */
function drawn(panel, redraw) {
  return el("div", null,
    el("div.big.value", { text: panel.today.topic }),
    el("div.faint", {
      text: panel.written
        ? `written · ${panel.today.path ?? "on disk"}`
        : "drawn today · it stays in the pool until the write-up is graded",
    }),
    writeUp(panel, redraw),
  );
}

/**
 * The write-up: the editor, or the way into it, plus what is on disk.
 *
 * The file is the source of truth and this panel is a window onto it — which
 * is why "read it back" exists beside the editor rather than only after it.
 * The same file is openable from a real terminal, and that is a supported way
 * to use this, not a workaround.
 */
function writeUp(panel, redraw) {
  if (editor) return editorBlock(panel, redraw);

  const open = el("button.primary", {
    text: panel.written ? "back to the editor" : "write it in nvim",
    onclick: async () => {
      open.disabled = true;
      try {
        // The file first, over HTTP, so a missing editor still leaves
        // something on disk with a path recorded against the topic.
        put("investigate", await api.post("/api/investigate/open"));
        if (!termReady()) {
          toast("the terminal did not load — the file is on disk, open it yourself");
          redraw();
          return;
        }
        editor = openEditor({ date: api.today(), scrollback: panel.scrollback });
        // The editor exiting is the cue to re-read the file: `:wq` is how you
        // finish, and the panel should show the paragraphs without being asked.
        editor.closed.then(async () => {
          closeEditor();
          try {
            put("investigate", await api.get("/api/investigate", { date: api.today() }));
          } finally {
            redraw();
          }
        });
        redraw();
      } catch (error) {
        open.disabled = false;
        toast(error.message);
      }
    },
  });

  return el("div.gap", null,
    el("div.row", null, open, panel.written ? readBack(redraw) : null),
    panel.file_body ? el("pre.write-up", { text: panel.file_body }) : null,
  );
}

/** The terminal, and the two ways out of it. */
function editorBlock(panel, redraw) {
  const done = el("button", {
    text: "done — close the editor",
    onclick: () => {
      closeEditor();
      redraw();
    },
  });

  return el("div.gap", null,
    editor.node,
    el("div.row", null, done, readBack(redraw)),
    el("div.faint", {
      text: "esc and tab are vim's in here · alt-hjkl leaves · ctrl-w and ctrl-r "
        + "stay the browser's, so redo is U",
    }),
  );
}

/** Hand the file in as today's submission. */
function readBack(redraw) {
  const button = el("button", {
    text: "read it back",
    title: "take what is in the file as today's write-up",
    onclick: async () => {
      button.disabled = true;
      try {
        put("investigate", await api.post("/api/investigate/read"));
        toast("read back ✓", "info");
        redraw();
      } catch (error) {
        button.disabled = false;
        toast(error.message);
      }
    },
  });
  return button;
}

export default {
  id: "investigate",
  title: "⌕ investigation of the day",
  tab: "politics",
  slot: "grid",
  span: 2,
  collapsible: true,

  summary() {
    const panel = store.state.investigate;
    if (panel.today) return `${panel.written ? "✓ " : ""}${panel.today.topic}`;
    return panel.pool_size ? `${panel.pool_size} waiting` : "pool empty";
  },

  async render(node) {
    const panel = store.state.investigate;
    const redraw = () => this.render(node);

    mount(node,
      panel.today ? drawn(panel, redraw) : undrawn(panel, redraw),
      // Hidden while the editor is up. The pool is a list of things you are
      // *not* doing today, and it has no business under a buffer you are
      // typing into — nor should a store change be able to reflow the page
      // beneath a cursor.
      editor ? null : pool(panel, redraw),
      editor ? null : box(redraw),
    );
  },
};
