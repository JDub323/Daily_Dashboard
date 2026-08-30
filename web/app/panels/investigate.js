// The investigation of the day: the tab's hero, and the day's biggest ask.
//
// The class hands out a list of things to look into. They land here in a paste,
// one per line, one is drawn each morning, it is written up in a real editor,
// and the result comes back graded. Four states, all hanging off the one drawn
// row:
//
//   nothing drawn   the pool, and the button that spends from it
//   drawn           the topic, and the editor it is written in
//   read back       the prompt to carry to the Claude app, and the paste box
//   graded          five rubric lines, what was corrected, and the prose
//
// [Invariant] Drawing is a button, never something that happens on render. A
// panel that drew on mount would spend a topic every morning the politics tab
// was painted, which is every morning. Same rule as the theorem draw, and the
// reason `/api/investigate/draw` is a POST.
//
// The pool travels with the boot payload rather than being fetched: it is a
// few hundred short strings at the very worst, and it has to be on screen to
// be struck off — a list you cannot see is a list that only ever grows.

import * as api from "../api.js";
import { promptBlock } from "../clipboard.js";
import { el, mount } from "../dom.js";
import { put, store } from "../store.js";
import { openEditor, ready as termReady } from "../term.js";
import { toast } from "../ui.js";

/** Half-pasted ballots, kept across re-renders. See `draft` below. */
let ballotDraft = "";

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

/** The rating, and the per-folder strip. Read like the math header: the last
 *  folder in the row is the one to write about next. */
function header(panel) {
  const { rating, k_next, measured_count, success_rate } = panel.summary;
  const detail = [`elo · K=${k_next}`];
  if (success_rate !== null) detail.push(`${success_rate}% last ${Math.min(measured_count, 10)}`);

  return [
    el("div.figure-row", null,
      el("span.figure", { text: rating }),
      el("span.figure-label", { text: detail.join(" · ") }),
    ),
    panel.folders.length
      ? el("div.areas", null,
          ...panel.folders.map((f) =>
            el("span.area", {
              title: `${f.area}: ${f.events} write-up${f.events === 1 ? "" : "s"}`,
            },
              el("span.faint", { text: `${f.area} ` }),
              el("span", { text: f.rating }),
            ),
          ),
        )
      : null,
  ];
}

/** Drawn: what today is about, and wherever it has got to. */
function drawn(panel, redraw) {
  const day = panel.day;
  const graded = Boolean(day?.ballot);

  return el("div", null,
    el("div.row.spread", null,
      el("div", null,
        el("div.big.value", { text: panel.today.topic }),
        // The file is the `title`, not the text. It is the folder plus a slug
        // of the topic already on screen above, so spelling it out costs three
        // lines to repeat two things you can see — but it is still the thing
        // you want when you go to open the file in a terminal.
        el("div.faint", { text: statusLine(panel, graded), title: panel.today.path ?? "" }),
      ),
      graded ? deltaTag(panel) : null,
    ),
    graded ? scored(panel) : el("div", null, writeUp(panel, redraw), submit(panel, redraw)),
  );
}

function statusLine(panel, graded) {
  if (graded) return `graded · filed under ${panel.today.folder ?? "—"}`;
  if (panel.day?.write_up) return "read back · take the prompt below";
  if (panel.written) return "written · not handed in yet";
  return "drawn today · it stays in the pool until the write-up is graded";
}

function deltaTag(panel) {
  const delta = panel.delta;
  if (delta == null) return null;
  return el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
    text: delta === 0 ? "no change" : `${delta > 0 ? "+" : ""}${delta}`,
    title: "investigation rating",
  });
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
        editor = openEditor({
          url: "/api/investigate/term",
          date: api.today(),
          scrollback: panel.scrollback,
        });
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

/**
 * The prompt, and the box the graded result comes back in.
 *
 * Only once something has been read back: the prompt quotes the *snapshot*,
 * not the file, so offering it before there is one would hand the grader an
 * empty document. The copy button is the three-fallback one from
 * `clipboard.js`, shared with the trick panel.
 */
function submit(panel, redraw) {
  if (!panel.prompt) return null;

  const box = el("textarea.recall-box", {
    rows: 4,
    value: ballotDraft,
    placeholder: "paste the whole reply back — the parser finds the ```json block in it",
    oninput: (e) => { ballotDraft = e.target.value; },
  });
  const message = el("span.faint");

  const grade = el("button.primary", {
    text: "grade it",
    onclick: async () => {
      if (!box.value.trim()) {
        message.textContent = "paste the reply first";
        return;
      }
      grade.disabled = true;
      message.textContent = "grading…";
      try {
        put("investigate", await api.post("/api/investigate/ballot", { text: box.value }));
        ballotDraft = "";
        redraw();
      } catch (error) {
        grade.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div.divider", null,
    promptBlock(panel.prompt, {
      summary: "read the prompt",
      label: "copy the prompt",
    }).node,
    el("div.faint.gap", { text: "the result" }),
    box,
    el("div.row", null, grade, message),
  );
}

/** Graded: the five lines, what was corrected, and the prose. */
function scored(panel) {
  const ballot = panel.day.ballot;
  const byKey = new Map(ballot.items.map((item) => [item.key, item]));
  const total = ballot.items.reduce((sum, item) => sum + item.score, 0);
  const max = 2 * panel.rubric.length;

  const line = (spec) => {
    const item = byKey.get(spec.key);
    const score = item?.score ?? 0;
    return el("div.ballot-line", null,
      el("div.row.spread", null,
        el("span", { text: spec.label, title: spec.asks }),
        el(`span.${score === 2 ? "ok" : score === 1 ? "gold" : "bad"}`, { text: `${score}/2` }),
      ),
      item?.note ? el("div.dim.small", { text: item.note }) : null,
      // Said out loud rather than silently applied: a rule you cannot see
      // enforced is one you assume is not being enforced.
      ballot.capped.includes(spec.key)
        ? el("div.err.small", { text: "capped — full marks are not available once "
            + "something needed correcting" })
        : null,
    );
  };

  return el("div", null,
    el("div.faint.gap", { text: `${total}/${max}` }),
    ...panel.rubric.map(line),
    ballot.corrections.length
      ? el("div.divider", null,
          el("div.faint", { text: "what it checked and I got wrong" }),
          ...ballot.corrections.map((c) =>
            el("div.correction", null,
              el("div.err.small", { text: c.claim }),
              el("div.small", { text: c.correction }),
              c.source ? el("div.faint.small", { text: c.source }) : null,
            ),
          ),
        )
      : el("div.faint.divider", { text: "nothing it could fault on the facts" }),
    ballot.feedback
      ? el("div.divider", null,
          el("div.faint", { text: "feedback" }),
          el("div.dim.feedback", { text: ballot.feedback }),
        )
      : null,
    ballot.verdict ? el("div.dim.divider", { text: ballot.verdict }) : null,
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
  slot: "hero",
  hero: true,
  collapsible: true,

  summary() {
    const panel = store.state.investigate;
    if (!panel.today) {
      return panel.pool_size ? `${panel.pool_size} waiting` : "pool empty";
    }
    if (panel.day?.ballot) {
      const total = panel.day.ballot.items.reduce((sum, item) => sum + item.score, 0);
      const delta = panel.delta == null ? "" : ` ${panel.delta >= 0 ? "+" : ""}${panel.delta}`;
      return `${panel.summary.rating}${delta} · ${total}/${2 * panel.rubric.length}`;
    }
    return `${panel.written ? "✓ " : ""}${panel.today.topic}`;
  },

  async render(node) {
    const panel = store.state.investigate;
    const redraw = () => this.render(node);

    mount(node,
      ...header(panel),
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
