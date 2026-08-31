// Keyboard bindings, so the mouse is optional rather than assumed.
//
// One listener on the document: every binding here is global by nature, and
// the panels are rebuilt on every store change, so anything attached to them
// would have to be reattached each time.
//
// Three gates matter more than the bindings do.
//
//   - Off while a modal is open. You are doing the task, and `j` belongs in
//     the sentence you are typing.
//   - Off inside any editable element — with Esc and Tab as the deliberate
//     exceptions, since they are the way out of one and the way across to the
//     next one.
//   - Off whenever Ctrl/Cmd is held, so Cmd-1 still switches *browser* tabs. A
//     shortcut layer that eats the browser's own keys is worse than no
//     shortcut layer.
//
// Alt is the exception to the third gate, and only for h/j/k/l: moving between
// panels is the one thing that has to work from inside a mode you did not mean
// to enter, including with a text box holding the keyboard. Nothing in a
// browser is bound to Alt-hjkl, so the cost is a menu shortcut nobody presses.
//
// Shift is not in that list either, because Shift-Tab is a binding.
//
// `b` and `m` are the two bindings here that do not merely move the focus
// ring: they switch section, land on a logging panel and start its ladder, all
// on one press. That is a deliberate exception to how everything else in this
// table behaves, and it is bought by the same argument the panels themselves
// are built on — a meal log that costs a tab switch, four arrow keys and an
// Enter is a meal log kept for nine days. The rest of the day's work is
// something you sit down to; these two are answered on the way past.

import { capturing, endCapture, takeKey } from "./capture.js";
import { JUMPS, takeArrow, takeHoverKey } from "./carousel.js";
import { chipTasks, openNextUnfinished, required, score } from "./chips.js";
import { $, el } from "./dom.js";
import * as nav from "./nav.js";
import { nudgeCount, openNudge } from "./nudges.js";
import { focusPanel, panelEnter, panelKey, quickStart } from "./panel.js";
import { splashOpen } from "./splash.js";
import { TABS, activateNth, cycle } from "./tabs.js";
import { openModal, toast } from "./ui.js";

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Keys that are only ever held, never pressed at something. */
const MODIFIERS = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock", "Dead"]);

function isTyping(target) {
  return Boolean(target) && (EDITABLE.has(target.tagName) || target.isContentEditable);
}

/**
 * Is the focus inside the embedded editor?
 *
 * The two gates below that survive a text box — Esc and Tab — are exactly the
 * two keys a modal editor cannot do without. Esc is how you leave insert mode
 * and Tab is a real character in it, so a terminal that did not get them back
 * would be an editor you can type into and never escape from.
 *
 * xterm.js takes its input through a hidden textarea, so `isTyping` already
 * hands it the whole alphabet — h, j, k, l, i, f, n, gg and the digits are all
 * yielded before this. This is only about the two exceptions to that rule.
 *
 * Alt-hjkl is deliberately *not* exempted. It stays the way out of anywhere,
 * including out of a buffer left in a mode you did not mean to enter, and vim
 * binds nothing to it — so the app keeps it and loses nothing.
 */
export function inEditor(target) {
  // Optional-chained twice on purpose. `e.target` is not always an Element —
  // it is `document` for some synthetic events and `window` for others, and
  // neither has `closest`. A TypeError thrown from here would take Esc down
  // for the whole app, which is the one binding that must never fail.
  return Boolean(target?.closest?.(".term"));
}

const modalOpen = () => !$("#modal-overlay").hidden;

/** `gg` is the only two-key sequence, so the pending state is one flag. */
let pendingG = false;
let gTimer = null;

function armG() {
  pendingG = true;
  clearTimeout(gTimer);
  gTimer = setTimeout(() => {
    pendingG = false;
  }, 700);
}

/**
 * The bindings, as data — the cheatsheet is generated from the same list that
 * dispatches. A legend maintained separately from the handler is a legend that
 * is wrong by the second change.
 */
const BINDINGS = [
  {
    keys: ["tab", "shift-tab"],
    hint: "next / previous section",
    match: (e) => e.key === "Tab",
    run: (e) => cycle(e.shiftKey ? -1 : 1),
  },
  {
    keys: ["1", "…", String(TABS.length)],
    hint: "jump straight to a section",
    match: (e) => /^[1-9]$/.test(e.key) && Number(e.key) <= TABS.length,
    run: (e) => activateNth(Number(e.key)),
  },
  {
    keys: ["h", "j", "k", "l"],
    hint: "move the focus ring — between panels, or between links inside one",
    match: (e) => ["h", "j", "k", "l"].includes(e.key),
    run: (e) => nav.move(e.key),
  },
  {
    keys: ["alt", "+", "hjkl"],
    hint: "move between panels from any mode — even with a text box focused",
    // Handled before every gate below; listed here so the sheet is the map.
    match: () => false,
    run: () => {},
  },
  {
    keys: ["enter"],
    hint: "fill this panel in from the keyboard, press its only button, or step "
      + "inside it · shift-enter always steps inside",
    match: (e) => e.key === "Enter",
    run: (e) => {
      // The panel first, and only unshifted: Shift-Enter stays the way into
      // item mode on a card that has taken Enter for something of its own.
      if (!e.shiftKey && panelEnter()) return;
      nav.enter({ descend: e.shiftKey });
    },
  },
  {
    keys: ["1", "…", "5"],
    hint: "on the check-in and the session log: answer the highlighted row and "
      + "drop to the next one · esc gives the digits back to the tabs",
    // Dispatched by the capture layer, ahead of the table. Listed here because
    // the sheet is the map, and a mode whose keys are undocumented is one you
    // will not use twice.
    match: () => false,
    run: () => {},
  },
  {
    keys: ["i"],
    hint: "type in this panel's first text box · tab walks the rest of them",
    match: (e) => e.key === "i",
    run: () => nav.insert(),
  },
  {
    keys: ["f"],
    hint: "letter every link in this panel, then press its letter to open it "
      + "· c d l m p select one of the logging card's five tabs, which print "
      + "their letter · a digit opens that numbered notification",
    match: (e) => e.key === "f",
    run: () => nav.hint(nudgeCount()),
  },
  {
    keys: ["←", "→"],
    hint: "step the logging card to the tab beside this one, once the ring is "
      + "on it — f and a letter go straight to one · it stops turning by "
      + "itself whenever it is lit: hovered, held, or being filled in",
    // Dispatched ahead of the table, like the capture and the hints. Listed
    // here because the sheet is the map.
    match: () => false,
    run: () => {},
  },
  {
    keys: ["gg", "G"],
    hint: "first / last panel of the section",
    match: (e) => e.key === "g" || e.key === "G",
    run: (e) => {
      if (e.key === "G") {
        nav.edge("last");
        return;
      }
      if (pendingG) {
        pendingG = false;
        nav.edge("first");
        return;
      }
      armG();
    },
  },
  {
    keys: ["n"],
    hint: "open the next thing you have not done today",
    match: (e) => e.key === "n",
    run: () => {
      if (!openNextUnfinished()) toast("everything is done — nice day ✦", "info");
    },
  },
  {
    keys: ["m"],
    hint: "log a meal from wherever you are — size, then how processed",
    match: (e) => e.key === "m",
    run: () => quickStart("meals"),
  },
  {
    keys: ["?"],
    hint: "this list",
    // Both spellings. A US layout reports `?` for shift-/, but the shifted
    // character is layout-dependent and some environments report the unshifted
    // key with `shiftKey` set instead — including headless automation.
    // Matching one of them works on a keyboard and not on another, which is
    // the worst of the three outcomes.
    match: (e) => e.key === "?" || (e.key === "/" && e.shiftKey),
    run: () => openHelp(),
  },
  {
    keys: ["esc"],
    hint: "back to the highlighted panel, in one press · also closes a modal "
      + "· inside the editor it belongs to vim",
    match: (e) => e.key === "Escape",
    run: () => nav.escape(),
  },
  {
    keys: ["alt", "+", "hjkl"],
    hint: "the way out of the editor — esc and tab are vim's in there, so this "
      + "is what leaves it · ctrl-w and ctrl-r stay the browser's, and redo is "
      + "remapped to U",
    // Handled with the other Alt-hjkl, ahead of every gate. Listed separately
    // because the editor is the one place where "how do I get out" has a
    // different answer, and the sheet is the map.
    match: () => false,
    run: () => {},
  },
];

/**
 * Alt-hjkl, by physical key.
 *
 * `e.key` is the wrong side of the keyboard layout for this one: on macOS Alt
 * is the compose modifier, so Alt-j arrives as "∆" and Alt-h as "˙". `e.code`
 * is the key you actually pressed.
 */
const ALT_MOVE = { KeyH: "h", KeyJ: "j", KeyK: "k", KeyL: "l" };

function openHelp() {
  // The required ones, matching the statusline — both layers, in the same
  // shape. Two surfaces disagreeing about how much of the day is left is worse
  // than either number alone.
  const list = chipTasks();
  const tasks = required(list);
  const done = tasks.filter((task) => task.done).length;
  const { earned, available } = score(list);
  openModal("keyboard", (body) => {
    body.replaceChildren(
      el("div.faint.small", {
        text: "active on the dashboard itself — not while a modal is open. "
          + "in a text box only esc, tab and alt-hjkl still answer.",
      }),
      el("div.keys", null,
        ...BINDINGS.flatMap((binding) => [
          el("div.keycombo", null, ...binding.keys.map((key) => el("kbd", { text: key }))),
          el("div.dim", { text: binding.hint }),
        ]),
      ),
      el("div.faint.small.gap", {
        text: `${done}/${tasks.length} required done today · +${earned} of ${available} pts `
          + "· each section's digit is printed on its tab",
      }),
    );
  });
}

export function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey) return;

    // The title screen, ahead of even Alt-hjkl. splash.js already stops these
    // events in the capture phase, so this is the second lock rather than the
    // first — but the coupling is worth writing down where the other gates
    // are, because a binding added below would otherwise look safe and fire
    // at a dashboard nobody can see.
    if (splashOpen()) return;

    // Alt-hjkl, ahead of every gate. This is the key that has to work when you
    // are somewhere you did not mean to be — three fields deep in a form, or
    // in item mode on the wrong card.
    if (e.altKey) {
      const key = ALT_MOVE[e.code];
      if (!key || modalOpen()) return;
      // The blur is what makes it a *move* and not a move-while-still-typing:
      // nav returns to panel mode on the card it lands on.
      if (isTyping(e.target)) e.target.blur();
      // Leaving the card is leaving its keyboard: a capture that survived the
      // move would eat digits meant for the panel you just arrived at.
      endCapture();
      e.preventDefault();
      nav.move(key, { toPanels: true });
      return;
    }

    // Esc next, and ahead of both remaining gates: it is the way out of the
    // text box, and ui.js owns it while a modal is up.
    //
    // The editor is the one place Esc is not ours. It is the key vim is built
    // on, and taking it would blur the terminal on every mode change — the
    // whole editor, lost to the one binding this app documents as always
    // working. Alt-hjkl is still the way out, and still works from in there.
    if (e.key === "Escape") {
      if (inEditor(e.target)) return;
      if (modalOpen()) return;
      // One press, one layer, outermost first — the capture, then the mode.
      // Esc out of the ladder should leave the ring on the card you were
      // filling in, which is what makes it safe to press when unsure.
      if (!endCapture()) nav.escape();
      e.preventDefault();
      return;
    }

    if (modalOpen()) return;

    // Inside a text box the keyboard belongs to the box, with one exception:
    // Tab walks that panel's other boxes. See nav.cycleField.
    //
    // …and the exception has its own exception. In the editor Tab is a
    // character you are typing, so walking to the next form field would
    // indent nothing and lose your place.
    if (isTyping(e.target)) {
      if (inEditor(e.target)) return;
      if (e.key === "Tab" && nav.cycleField(e.shiftKey ? -1 : 1)) e.preventDefault();
      return;
    }

    // A panel filling itself in gets the keys before the table does — that is
    // what buys the digits back from the tab switches for as long as the ladder
    // is up.
    //
    // A key it declines both ends the capture and then does its normal job, so
    // there is one rule to hold: the ladder owns the keys it uses, and anything
    // else drops you out of it. Without that, `h` moved the focus ring to the
    // next card while the digits still belonged to the card you had left.
    if (capturing()) {
      if (takeKey(e)) {
        e.preventDefault();
        return;
      }
      // A bare modifier is not a key you pressed at anything — holding Shift
      // to type `?` fires its own keydown first, and ending on that would make
      // every shifted key look like two.
      if (!MODIFIERS.has(e.key)) endCapture();
    }

    // The hint layer eats the next key whatever it is — that is what buys it
    // the whole alphabet while `g`, `h` and `n` keep their normal meanings.
    if (nav.inHints()) {
      e.preventDefault();
      const key = e.key.toLowerCase();
      // A digit is a notification, top card first.
      //
      // This is what `f` is *for* on a panel full of links: the letters go to
      // the links, and the digits go to the stack in the corner, so there is
      // no page anywhere in the app where reaching a notification competes
      // with reaching an article. The two alphabets never touch because one
      // of them is not an alphabet.
      if (/^[1-9]$/.test(key)) {
        nav.exitHints();
        openNudge(Number(key));
        return;
      }
      // Five letters are the carousel rather than a link, and `nav` has been
      // told not to paint them, so the two can never mean the same key.
      // `focusPanel` rather than `quickStart`: `f c` is "show me the
      // check-in", and starting its ladder as well would be answering a
      // question you had only asked to look at.
      if (JUMPS[key]) {
        nav.exitHints();
        focusPanel(JUMPS[key]);
        return;
      }
      if (/^[a-z]$/i.test(key)) nav.takeHint(key);
      else nav.exitHints();
      return;
    }

    // The arrows steer the logging card, and only while the ring is on it.
    // Ahead of the table because the table has no entry for them and the
    // browser would otherwise scroll the pane sideways.
    if (takeArrow(e)) {
      e.preventDefault();
      return;
    }

    // A key the focused panel would have taken *after* an Enter takes it
    // without one: pressing `b` on the session log starts it on boxing,
    // rather than starting it on nothing and waiting to be told again.
    //
    // Offered after the hints and the arrows and before the table, which is
    // the one ordering that works: a panel may claim any key the table has
    // not already spent on *going somewhere else*. Panels enforce that half
    // themselves — see `RESERVED` in panel.js — because the rule is about
    // which keys are safe to claim, and that is one fact rather than six.
    if (panelKey(e)) {
      e.preventDefault();
      return;
    }

    // The carousel's own letters, while the pointer is on the card. After the
    // focused panel and before the table: a panel you have selected outranks a
    // card you are merely pointing at, and the table's own meanings for these
    // letters are exactly what pointing at the card is meant to override.
    if (takeHoverKey(e)) {
      e.preventDefault();
      return;
    }

    const binding = BINDINGS.find((b) => b.match(e));
    if (!binding) return;
    // Tab is the one that would otherwise do something: without this the
    // browser walks its own focus order underneath the tab switch.
    e.preventDefault();
    binding.run(e);
    if (e.key !== "g") pendingG = false;
  });
}
