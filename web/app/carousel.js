// Four logging panels in one slot, showing one at a time.
//
// The health tab had five cards under the hero — the check-in, the plan, the
// session log, the meals and the in-and-out — and four of them were empty for
// most of the day. That is not a full page, it is one page of forms you scroll
// past to reach the one you came for, and the cost of it is paid every time:
// the plan, which is the thing you actually read, was below the fold behind
// three boxes waiting to be filled in later.
//
// So the four take turns. The plan keeps the left half and stays still; the
// right half is one card that changes every five seconds, in the shape of the
// news strip in a Windows taskbar — which is the reference for a reason. That
// strip works because it never asks anything of you: it cycles in the corner
// of your eye and stops the moment you look at it, and looking at it is
// exactly the gesture (moving the pointer there) that you were going to make
// anyway if you wanted it.
//
// --- what stops it -----------------------------------------------------------
//
// Three things, and the difference between them matters:
//
//   **Hovering pauses it.** Temporary and implicit. Move away and it resumes
//   from wherever it stopped. This is the taskbar's own rule and it is what
//   makes a rotating widget usable at all — a card that slid out from under
//   the pointer as you reached for its button would be unusable, and one that
//   *stayed* stopped after you brushed past it would be broken in the other
//   direction.
//
//   **The focus ring selects it.** Explicit, and it holds. Once h/j/k/l has
//   landed on this card you are working here, and a rotation under a card you
//   have selected would move the buttons out from under the keyboard. The
//   arrow keys then steer it by hand.
//
//   **Filling one in holds it.** A capture running on the card (see
//   capture.js) is the strongest signal there is that you are mid-answer, and
//   nothing rotates through a half-typed meal.
//
// --- what it does not do -----------------------------------------------------
//
// It does not lazily build its slides, and it does not tear down the ones that
// are off screen. Every panel is mounted and rendered exactly as it was when
// each had its own card; the only thing this file does is decide which one is
// `hidden`. That is deliberate and it is the same argument tabs.js makes: these
// panels hold live captures, half-typed inputs and a cursor into a ladder, and
// rebuilding them on every rotation would drop all three every five seconds.

import { captureOwner, capturing } from "./capture.js";
import { $, el } from "./dom.js";
import { focusedPanel } from "./nav.js";

/** How long each slide gets. Long enough to read a card's worth of numbers,
 *  short enough that the four come round inside half a minute. */
const DWELL_MS = 5000;

/**
 * `key: panel id`. The letters `f` waits for, and what each one means.
 *
 * `c`heck-in, `l`og a session, `m`eals, `p`lumbing — the last of which is the
 * only one that is not the first letter of its title, because `i` is taken by
 * insert mode and "in and out" has no other free letter. `m` agrees with the
 * global meal binding on purpose: the same letter means the same panel from
 * either direction.
 */
export const JUMPS = { c: "checkin", l: "session", m: "meals", p: "bathroom" };

/** Mounted slides, in rotation order. `{ id, card }`. */
const slides = [];

let index = 0;
let timer = null;
let hovering = false;
let track = null;
let dots = null;

/** The card the rotation lives in, or null before `mount` has run. */
let host = null;

const current = () => slides[index];

/** True when something says a human is working on this card right now. */
function held() {
  if (hovering) return true;
  // The ring is on the slide that is showing. Selecting it is the explicit,
  // sticky version of hovering: you are working here now, and the card must
  // not move its buttons out from under the keyboard.
  const focused = focusedPanel();
  if (focused && slides.some((s) => s.card.id === focused)) return true;
  // Mid-ladder. `capturing()` alone would be too broad — the check-in on
  // another tab is not a reason to freeze this one — so it is the owner that
  // is checked.
  return capturing() && slides.some((s) => s.id === captureOwner());
}

// --- the rotation -----------------------------------------------------------

function paint() {
  for (const [i, slide] of slides.entries()) {
    const showing = i === index;
    slide.card.hidden = !showing;
    // Re-triggered by removing and re-adding, because an animation replayed on
    // an element that never lost the class does not replay at all.
    if (showing) {
      slide.card.classList.remove("cyc-in");
      // Reading offsetWidth is the documented way to force the style flush
      // between the two, and the only reason this is not one line.
      void slide.card.offsetWidth;
      slide.card.classList.add("cyc-in");
    }
  }
  if (!dots) return;
  for (const [i, dot] of [...dots.children].entries()) {
    dot.classList.toggle("on", i === index);
    dot.setAttribute("aria-current", String(i === index));
  }
}

/**
 * Show slide `i`, and restart the dwell.
 *
 * The restart is the point of doing this in one function: arrowing to a card
 * and having it slide away 200ms later — because the old timer was still
 * running — is the single most annoying thing a widget like this can do.
 */
export function show(i) {
  if (!slides.length) return;
  index = ((i % slides.length) + slides.length) % slides.length;
  paint();
  arm();
}

export const step = (delta) => show(index + delta);

/** The panel id currently on screen. */
export const showing = () => current()?.id ?? null;

/**
 * Put slide `id` on screen. True if there is one.
 *
 * This is what `f c` and the notifications both call, and it is separate from
 * `show` because everything outside this file addresses these panels by id —
 * their position in the rotation is this file's business and nobody else's.
 */
export function reveal(id) {
  const i = slides.findIndex((s) => s.id === id);
  if (i < 0) return false;
  show(i);
  return true;
}

function arm() {
  clearTimeout(timer);
  timer = setTimeout(tick, DWELL_MS);
}

function tick() {
  // Re-armed rather than cancelled: whatever is holding it will stop holding
  // it, and a timer that had to be restarted by an event would mean finding
  // every event that could end a hold. Checking once every five seconds costs
  // nothing and cannot be forgotten.
  if (!held()) step(1);
  arm();
}

// --- mounting ---------------------------------------------------------------

/**
 * The node a hosted panel's card mounts into, building the shell on first ask.
 *
 * Called from `panel.js` during `mountAll`, which is what puts the shell in
 * the right place in the health grid without this file knowing anything about
 * the layout: it lands wherever the first hosted panel would have landed, and
 * the panels registered before it — the plan — are already beside it.
 */
export function slot(gridNode) {
  if (host) return track;

  track = el("div.cyc-track");

  dots = el("div.cyc-dots", { role: "tablist", "aria-label": "which log" });

  // Deliberately *not* a `.panel`, though it is painted as one. nav.js finds
  // its targets with `$$(".panel")`, so a shell that carried the class would
  // be a focus target wrapped around another focus target, and h/j/k/l would
  // stop twice on one card — once on the frame and once on what is in it.
  // The ring belongs on the slide: it is the slide that owns the keyboard,
  // has the buttons, and answers Enter.
  host = el("section.cyc", {
    id: "cyc",
    onmouseenter: () => {
      hovering = true;
    },
    onmouseleave: () => {
      hovering = false;
    },
  },
    el("div.cyc-bar", null,
      el("button.ghost.tiny", {
        text: "‹",
        title: "previous log",
        "aria-label": "previous log",
        onclick: () => step(-1),
      }),
      dots,
      el("button.ghost.tiny", {
        text: "›",
        title: "next log",
        "aria-label": "next log",
        onclick: () => step(1),
      }),
    ),
    track,
  );
  host.style.setProperty("--span", 3);
  gridNode.append(host);
  return track;
}

/**
 * Register a card as a slide. Called once per hosted panel, from `mountAll`.
 *
 * The dot carries the panel's title as its label rather than a number: the
 * question it answers is "which of the four is showing", and four unlabelled
 * pips answer it only for someone who already knows the order.
 */
export function addSlide(id, card, title) {
  slides.push({ id, card });
  dots.append(
    el("button.cyc-dot", {
      title,
      "aria-label": title,
      role: "tab",
      onclick: () => reveal(id),
    }),
  );
}

/** Start rotating. Called once, after every panel has mounted. */
export function startCarousel() {
  if (!slides.length) return;
  paint();
  arm();
}

/**
 * The arrow keys, offered before the bindings table sees them.
 *
 * Only while the ring is actually on this card — Left and Right are not bound
 * to anything globally and could safely be taken, but a key that steers a
 * widget you are not looking at is a key that appears to do nothing.
 */
export function takeArrow(e) {
  const focused = focusedPanel();
  if (!focused || !slides.some((s) => s.card.id === focused)) return false;
  if (e.key === "ArrowRight") {
    step(1);
    return true;
  }
  if (e.key === "ArrowLeft") {
    step(-1);
    return true;
  }
  return false;
}

/** The shell's element id, so `nav` and `panel.js` can address it. */
export const hostId = () => host?.id ?? null;

/** The showing slide's card element, which is what the focus ring should
 *  actually land on when something asks for this widget. */
export const showingCard = () => current()?.card ?? null;

/** True when `id` is one of the four. Lets callers route a "show me that
 *  panel" through the rotation rather than scrolling to a hidden card. */
export const hosts = (id) => slides.some((s) => s.id === id);

// Rebuilt bodies do not disturb the rotation — the cards themselves are never
// replaced — so there is nothing to re-run after a render. This export exists
// only so `main.js` can put the ring back on the right card after a rollover.
export function repaintCarousel() {
  if (slides.length) paint();
}

/** Used by the cheatsheet, so the letters are documented from the same table
 *  that dispatches them. */
export function jumpHints() {
  return Object.entries(JUMPS).map(([key, id]) => {
    const slide = slides.find((s) => s.id === id);
    return [key, slide ? $(`#${slide.card.id} h2`)?.textContent ?? id : id];
  });
}
