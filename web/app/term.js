// A real editor, in a panel.
//
// xterm.js draws the terminal; the websocket carries bytes to and from a pty
// the server forked nvim onto. Nothing here emulates anything — the editor on
// the other end is the one in your PATH, with your config, and this file's
// whole job is to be a faithful screen and keyboard for it.
//
// That faithfulness is the reason this exists at all. A vim *mode* in a
// textarea is always a subset, and the subset you notice missing is whichever
// motion you reached for without thinking.
//
// --- what the browser keeps ------------------------------------------------
//
// Two chords cannot be had, and no amount of `preventDefault` changes it:
// Chrome reserves Ctrl-W (close tab) and Ctrl-R (reload), which are vim's
// window prefix and its redo. Redo is remapped to `U` server-side — see
// `[editor].commands` — and window commands are simply not available in a
// browser tab. The file is a real file on disk, so the answer to wanting them
// is to open it in a real terminal, where none of this applies.
//
// Two panels open one of these: the investigation write-up, where the file is
// the work, and the theorem recall boxes, where the file is a form of two
// textareas. Which is which is entirely the caller's business — everything
// below is a screen and a keyboard, and it does not know what is in the buffer.

import { el } from "./dom.js";

const FONT = '"SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';

/**
 * Are the vendored bundles there yet?
 *
 * Loaded with `defer`, like KaTeX, so a fast boot can render this panel before
 * they exist. The caller draws a line of text instead of a dead black box.
 */
export const ready = () => Boolean(window.Terminal && window.FitAddon);

/**
 * Open an editor on whatever the given socket serves.
 *
 * Returns `{node, dispose, closed}` — `closed` resolves when the editor exits,
 * which is the panel's cue to read the file back and redraw.
 *
 * @param opts.url         the websocket route. A path, not a file: which file
 *                         is opened is decided server-side from the date, and
 *                         nothing this function sends could name one.
 * @param opts.scrollback  lines kept; only `:!` output ever reaches it.
 */
export function openEditor({ url: route, date, scrollback = 1000 } = {}) {
  const node = el("div.term");

  const term = new window.Terminal({
    scrollback,
    fontFamily: FONT,
    fontSize: 13,
    // The block cursor is not decoration here: it is how you tell normal mode
    // from insert mode at a glance, and nvim drives it over the wire.
    cursorBlink: true,
    theme: themeFromPage(),
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(node);

  const url = new URL(route, location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("date", date);
  const socket = new WebSocket(url);
  socket.binaryType = "arraybuffer";

  let resolveClosed;
  const closed = new Promise((resolve) => { resolveClosed = resolve; });

  const send = (message) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  };

  // Fit before the first resize is sent, so the editor's very first paint is
  // already the right shape — nvim redrawing from 80x24 to the real size is a
  // visible flash otherwise.
  const resize = () => {
    try {
      fit.fit();
    } catch {
      /* the panel is hidden or zero-sized — nothing to fit to yet */
    }
    send({ t: "resize", rows: term.rows, cols: term.cols });
  };

  socket.addEventListener("open", () => {
    resize();
    term.focus();
  });
  socket.addEventListener("message", (e) => {
    term.write(new Uint8Array(e.data));
  });
  socket.addEventListener("close", () => {
    resolveClosed();
  });
  socket.addEventListener("error", () => {
    term.write("\r\n\x1b[31mthe editor connection dropped\x1b[0m\r\n");
  });

  term.onData((d) => send({ t: "in", d }));

  // The panel is redrawn by any store change and the pane resizes with the
  // window, so the terminal has to follow rather than being measured once.
  const observer = new ResizeObserver(resize);
  observer.observe(node);

  const dispose = () => {
    observer.disconnect();
    socket.close();
    term.dispose();
  };

  return { node, dispose, closed, focus: () => term.focus() };
}

/**
 * The terminal's palette, read off the page.
 *
 * Hard-coding it would give a terminal that is right in one theme and wrong in
 * the other, and this app's whole surface is one palette defined in
 * `style.css`. Falling back per-value rather than wholesale so a renamed
 * custom property costs one colour, not a black-on-black editor.
 */
function themeFromPage() {
  const style = getComputedStyle(document.documentElement);
  const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  return {
    background: read("--panel", "#141822"),
    foreground: read("--fg", "#dfe6f2"),
    cursor: read("--gold", "#e8b458"),
    // Literal: there is no selection colour in the palette, and inventing a
    // custom property here would be a token with exactly one reader.
    selectionBackground: "rgba(232,180,88,.28)",
  };
}
