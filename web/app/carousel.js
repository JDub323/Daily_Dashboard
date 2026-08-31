// Five logging panels in one slot, showing one at a time.
//
// The health tab had five cards under the hero — the check-in, the plan, the
// session log, the meals and the in-and-out — and four of them were empty for
// most of the day. That is not a full page, it is one page of forms you scroll
// past to reach the one you came for, and the cost of it is paid every time:
// the plan, which is the thing you actually read, was below the fold behind
// three boxes waiting to be filled in later.
//
// So they take turns. The plan keeps the left half and stays still; the
// right half is one card that changes every three seconds, in the shape of the
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
//   have selected would move the buttons out from under the keyboard.
//
// All three are one rule on screen: a lit cell is a still one. The shell --
// not the slide inside it — takes the highlight for the ring and for the
// pointer both, so the thing that stops rotating and the thing that lights up
// are visibly the same thing. Before this the pause was invisible, and a
// widget that stops for no stated reason reads as a widget that has broken.
//
// --- how you pick one --------------------------------------------------------
//
// A tab bar, and the same one the sections use: `.tab`, with its key printed
// on it and its active state lit. Four dots and a pair of chevrons answered
// "which of them is showing" only for somebody who already knew the
// order, and they made picking a particular one a matter of stepping past the
// ones you did not want. `f` then the tab's letter goes straight there, from
// anywhere in the app. The arrow keys still step to the neighbour once the
// ring is on the card, which is the small convenience it should always have
// been rather than the only way in.
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
// visible. That is deliberate and it is the same argument tabs.js makes: these
// panels hold live captures, half-typed inputs and a cursor into a ladder, and
// rebuilding them on every rotation would drop all three every three seconds.

import { captureOwner, capturing } from "./capture.js";
import { el } from "./dom.js";
import { focusedPanel } from "./nav.js";
import { activeTab } from "./tabs.js";

/**
 * How long each slide gets.
 *
 * Three rather than five, which is what the tab bar buys. A dwell has to be
 * long enough to finish reading in only while the slide that turned away is
 * gone until the rotation brings it back; with all five named on a bar and one
 * keystroke away, missing one costs a keypress. Fifteen seconds for the full
 * turn is about the longest a thing can take to come round and still read as
 * cycling, rather than as a card that changes when you are not looking — and
 * it is the number that decides how many slides this widget can hold. A sixth
 * would be the one that broke it.
 */
const DWELL_MS = 3000;

/**
 * The five, and how to address one.
 *
 * `key` is the letter `f` waits for, and it is printed on the tab — the same
 * argument as the digit on a section tab, that a shortcut you have to look up
 * is one you never learn.
 *
 * `c`heck-in, `l`og a session, `m`eals, `d`rinks, `p`lumbing — the last of
 * which is the only one that is not the first letter of its title, because `i`
 * is taken by insert mode and "in and out" has no other free letter. `m`
 * agrees with the global meal binding on purpose: the same letter means the
 * same panel from either direction.
 *
 * Letters and not digits, though these are painted as the same control as the
 * section tabs and those are digits. Under `f` a digit already means "open
 * that numbered notification" (see keys.js), and the whole reason `f` can
 * carry an alphabet and a numbering at once is that the two never overlap.
 *
 * `label` is not the panel's own title. A title is a line at the top of a card
 * — "morning check-in", "log a session" — and five of those in a row is a
 * stack of headlines rather than a tab bar.
 *
 * `task` names the entry in the day's list (chips.js) that speaks for this
 * tab, and it is what the dot beside the label reports. Three of the five have
 * one and two of them deliberately do not:
 *
 *   check-in  its own required task — weight and the questionnaire both.
 *   session   `training`, which is the plan's prescribed blocks all logged.
 *             The chip points at the plan panel because that is where you read
 *             what is owed, but this is the card you discharge it from, and
 *             the tab is answering "have I written the day's sessions down".
 *             On a rest day the task does not exist and the dot is absent,
 *             which is the correct thing for it to say.
 *   meals     three logged.
 *   drinks    nothing, and this is the one where the call was close. There is
 *             a defensible number of drinks of water a day and it would have
 *             been easy to invent one — but a hydration target you hit by
 *             logging rather than by drinking is a target that teaches you to
 *             log, and the panel already has a measurement of whether it
 *             worked: `dark_urine_days`, two tabs over, which is your body's
 *             answer rather than your own.
 *   in & out  nothing. It is a report with no target — there is no number of
 *             times a day you are supposed to have gone — and inventing an
 *             obligation for it so that every tab could carry a dot would
 *             be the tail wagging the dog. An absent dot means this card asks
 *             nothing of today, which is a true and useful third state.
 */
const TABS = [
  { key: "c", id: "checkin", label: "check-in", task: "checkin" },
  { key: "l", id: "session", label: "session", task: "training" },
  { key: "m", id: "meals", label: "meals", task: "meals" },
  { key: "d", id: "drinks", label: "drinks" },
  { key: "p", id: "bathroom", label: "in & out" },
];

/** `key: panel id`, for the hint layer in keys.js. Derived rather than written
 *  out a second time, so the letter printed on the tab and the letter that
 *  works are one fact. */
export const JUMPS = Object.fromEntries(TABS.map((tab) => [tab.key, tab.id]));

const byId = new Map(TABS.map((tab) => [tab.id, tab]));

/** Mounted slides, in rotation order. `{ id, card, tab, mark, title }`. */
const slides = [];

let index = 0;
let timer = null;
let hovering = false;
let track = null;
let bar = null;

/** The card the rotation lives in, or null before `mount` has run. */
let host = null;

const current = () => slides[index];

/** True when something says a human is working on this card right now. */
function held() {
  // Nobody is looking at it: another section is showing, or the whole tab is
  // in the background. Turning anyway is not harmful so much as pointless —
  // and it is actively wrong for the one thing the rotation is for, which is
  // being seen out of the corner of an eye. Coming back to health should show
  // the card the rotation left off on, not the last one it turned to while
  // the maths tab was up.
  //
  // It also stops the dwell from being a lie. Browsers throttle timers in a
  // background tab to about one a minute, so a widget that kept rotating there
  // was doing it at whatever interval the browser felt like.
  if (document.hidden || activeTab() !== "health") return true;
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

/**
 * Show slide `index` and hide the rest.
 *
 * `visibility` rather than the `hidden` attribute, and the difference is the
 * whole reason this card no longer moves. The slides are stacked in one
 * grid cell (see `.cyc-track`), so the shell is always as tall as the tallest
 * of them — and a hidden-by-`display:none` slide contributes no height, which
 * meant the card was 505px on the check-in and 300px on the meal log and
 * resized under the pointer every three seconds, shoving the plan beside it and
 * the whole page below it. A rotating widget that reflows the page is worse
 * than no rotating widget.
 *
 * `visibility: hidden` also takes a slide out of the accessibility tree and
 * out of the tab order, which `opacity: 0` would not — see `visible()` in
 * nav.js, which had to learn about it so the focus ring does not offer you
 * three cards you cannot see.
 */
function paint() {
  for (const [i, slide] of slides.entries()) {
    const showing = i === index;
    slide.card.classList.toggle("cyc-on", showing);
    // `active` because that is the class the section tabs carry: one class,
    // one look, one idea of what a selected tab is.
    slide.tab.classList.toggle("active", showing);
    slide.tab.setAttribute("aria-selected", String(showing));
    // Roving tabindex, the documented tablist pattern and what tabs.js does
    // upstairs — only the selected tab is in the browser's own focus order.
    slide.tab.tabIndex = showing ? 0 : -1;
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

/**
 * Put slide `id` on screen. True if there is one.
 *
 * This is what a tab, `f c` and the notifications all call, and it is separate from
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
  // every event that could end a hold. Checking once every three seconds costs
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

  // A real tablist, and the same control the sections are switched with. The
  // look, the printed key and the lit active state all come from `.tab`, so
  // this app has one tab rather than two things that resemble each other.
  bar = el("div.cyc-bar", { role: "tablist", "aria-label": "which log" });

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
  }, bar, track);
  host.style.setProperty("--span", 3);
  gridNode.append(host);
  return track;
}

/**
 * Register a card as a slide. Called once per hosted panel, from `mountAll`.
 *
 * Rotation order and tab order are both the order of these calls — which is
 * the order the panels are listed in main.js — so the bar reads left to right
 * in the order the card turns, and there is no second table anywhere saying
 * which of them comes first.
 *
 * `TABS` supplies the short label and the letter; the panel's own `title` is
 * the fallback and the tooltip, so a fifth hosted panel added without an entry
 * gets a working, if unkeyed, tab rather than a blank one.
 */
export function addSlide(id, card, title) {
  const meta = byId.get(id);
  // Starts at `none` and stays there until `markTabs` says otherwise, which is
  // the right default in both directions: a tab with no task never moves off
  // it, and one whose task has not been counted yet shows nothing rather than
  // guessing that it is owed.
  const mark = el("span.cyc-mark", { dataset: { state: "none" }, "aria-hidden": "true" });
  const tab = el("button.tab.cyc-tab", {
    title,
    role: "tab",
    "aria-selected": "false",
    "aria-controls": card.id,
    tabIndex: -1,
    // Drawn by `.tab::before` from the attribute — which is most of the reason
    // this is the section tabs' own class and not a copy of it.
    dataset: { key: meta?.key ?? "" },
    onclick: () => reveal(id),
  }, meta?.label ?? title, mark);
  slides.push({ id, card, tab, mark, title });
  bar.append(tab);
}

/**
 * Say which of them today still wants, from the day's task list.
 *
 * Called by `renderChips`, which already runs on every store change and is
 * already where `markDone` turns a finished panel gold — so this is the same
 * fact reaching one more surface rather than a second definition of done. The
 * list is passed in rather than imported: chips.js reaches this file through
 * panel.js already, and an import back the other way would be a cycle.
 *
 * The point of it is that the card shows one slide at a time. Everything else
 * on the page can be read by looking at it; this cell could only be read by
 * waiting for it, and "have I done my check-in" is not a question worth twelve
 * seconds. The dot answers all five at once, from any one of them.
 *
 * Gold and filled is done, hollow is owed, absent is not asked — which is the
 * app's own gold-means-finished rule, and the reason the tabs themselves are
 * lit cyan rather than the gold the section tabs use: on a bar that now has a
 * real gold marker on it, a gold *selection* would be the same colour saying a
 * different thing.
 */
export function markTabs(tasks) {
  for (const slide of slides) {
    const wanted = byId.get(slide.id)?.task;
    const task = wanted ? tasks.find((entry) => entry.id === wanted) : null;
    const state = task ? (task.done ? "done" : "owed") : "none";
    slide.mark.dataset.state = state;
    // On the button rather than the dot, because a `title` on a span inside a
    // button is a tooltip you have to find. The state is spelled out because a
    // colour is not a label — and this one has three values.
    slide.tab.title = task
      ? `${slide.title} · ${task.done ? "done today" : "still owed"}`
      : slide.title;
  }
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
 *
 * Stepping to the neighbour, and nothing more. Picking a particular one of the
 * one is the tab bar's job and `f`'s: arrowing past two cards to reach the
 * third was never a way to choose a card, it was a way to be shown the two you
 * had not asked for.
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


/**
 * The five letters, offered while the pointer is on the card.
 *
 * Hovering already means "I am looking at this one" — it is what stops the
 * rotation — and the letter is printed on the tab an inch from the pointer.
 * Having to type `f` first to use a key you can *see* is the kind of prefix
 * that only makes sense to whoever wrote it: `f c` is for reaching the
 * check-in from the other side of the app, and this is for picking a tab on
 * the card you are already pointing at.
 *
 * It steps aside for a capture on one of the slides, which is the same rule
 * the rotation itself follows and the reason `m` and `p` are safe: mid-ladder
 * they are the hour's minus and plus (see quicklog.js), and a half-typed meal
 * is not a card you are choosing between.
 *
 * Unmodified keys only — Ctrl-D and friends belong to the browser.
 */
export function takeHoverKey(e) {
  if (!hovering) return false;
  if (e.ctrlKey || e.metaKey || e.altKey) return false;
  if (capturing() && slides.some((s) => s.id === captureOwner())) return false;
  const id = JUMPS[e.key.toLowerCase()];
  if (!id) return false;
  return reveal(id);
}
