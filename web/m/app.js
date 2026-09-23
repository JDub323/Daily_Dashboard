// The phone log. **The only logging surface there is.**
//
// It began as the dashboard's logging cell — five cards that took turns in one
// slot on the health tab — made usable standing up, one-handed, in a kitchen
// or a gym, with the laptop shut. It ended as the whole thing: those five
// cards have been deleted, because for months every one of these logs was
// being answered here anyway, and a second surface that is never used is a
// second surface that goes quietly wrong.
//
// What that changes is the stakes rather than the code. A row that does not
// arrive from this app does not arrive at all — nothing on the laptop will
// catch it later — so `queue.js`'s rules about what counts as delivered are
// load-bearing in a way they were not, and the health tab has had to grow a
// vocabulary for saying *which kind* of nothing a missing metric is.
//
// --- what it is not ---------------------------------------------------------
//
// It was never the desktop cell made narrow. That cell was a *keyboard
// ladder*: `m` `l` `4` was a large, fairly clean meal, and the whole design of
// it — one keystroke per question, the last one commits — was an argument
// about keystrokes. None of that survives contact with a touchscreen, where
// there is no keyboard until you ask for one and asking for one covers half
// the screen.
//
// So the ladder was unrolled: every question a log asked was on screen at once
// as a row of buttons big enough to hit without looking. It has since been
// rolled partway back up — three questions open at a time, each answered one
// shrunk to a line — because "every question at once" was a check-in that had
// to be scrolled at 6am. See `ladderRows`. Same questions, same legal answers,
// same request bodies — those still come from `app/logs.js`, which is where
// the *contract* lives rather than where the other client lived.
//
// --- the theme --------------------------------------------------------------
//
// Identical, and by construction rather than by eye: `style.css` here imports
// the same `tokens.css` the dashboard does. Same palette, same type scale, same
// spacing. What changes is the layout and the hit targets.
//
// --- offline ----------------------------------------------------------------
//
// Every write goes through `queue.js` and the screen draws the server's answer
// plus whatever is still queued. See that file for why this is a list and a
// lock rather than a synchronisation problem — the short version is that the
// body already carries the date and the hour, so a write flushed tomorrow
// still lands on the right hour of today.

// Everything this screen asks, and the two rules for reading it. All of it out
// of `logs.js`, which imports nothing — which is the property that matters
// here rather than a matter of taste. This module's whole import graph has to
// be in the service worker's `SHELL`, or the app does not open with the laptop
// off; `applicableSteps` and `parseAmount` used to come from `quicklog.js` and
// quietly brought the store, the nav ring, the capture stack and the desktop's
// HTTP client along with them.
import {
  applicableSteps, BATHROOM_STEPS, CHECKIN_ITEMS, drinkNameLabel, DRINK_STEPS,
  focusLabel, focusLevels, forcedPath, likelyMeals, MEAL_LABEL_MAX, MEAL_STEPS, mealFills,
  mealItem, mealMatches, mealName, mealTree, missingSteps, newMealSlug, openBlocks, parseAmount, prunedDraft, prunedTree, QUALITY_ANCHOR, RPE_ANCHOR,
  sameMeal, sessionFocus, SESSION_TREE, sessionKind, stepOptions, toShown, toStored,
  withNamed,
} from "../app/logs.js";
// What today asks for, and it is the desktop's own list rather than a second
// opinion about it — see the head of `day.js`. Pure over a `DashboardState`,
// which is exactly what `GET /api/day` hands back, and it imports `logs.js` and
// nothing else, which is what makes it legal here.
import { dayTasks, optional, required, score } from "../app/day.js";
// The week grid's arithmetic, with the week taken out of it: packing, the
// drawn window, the twelve-hue table and the two clock conversions. Same
// module the laptop draws its calendar from, which is the whole point —
// the schedule below is one column of that grid rather than a second
// implementation of it. It imports nothing, which is what makes it legal
// here; `panels/calendar.js`, where it used to live, imports five things
// that are not in the shell.
import {
  clock as atMinute, dayWindow, HUES, hueMap, pack, timedEvent,
} from "../app/dayplan.js";
// Where a todo row goes, and how its title is printed. Same argument, same
// property: no DOM, no store, no imports at all.
import {
  clock as dueClock, dayLabel as dueLabel, groupByDay, keyFor, shorten, undatedItems,
} from "../app/todolist.js";
// The dashboard's own title screen, not a copy of it: same module, same
// markup in index.html, same stylesheet. It imports nothing but `dom.js`,
// which imports nothing at all — so bringing it here costs exactly the splash
// and none of the store, the panel harness or the nav ring.
import { initSplash, paintSplash } from "../app/splash.js";
import {
  forget, forgetDay, recall, recallDay, recallMenu, remember, rememberDay, rememberMenu,
  stillTrue,
} from "./held.js";
import { enqueue, flush, isDay, isPanel, onQueueChange, pending, pendingOn } from "./queue.js";
import { count, tallies } from "./tally.js";
import {
  clock,
  clockTime,
  dateForHour,
  dateForTime,
  dayLabel,
  entryClock,
  hhmm,
  hoursLabel,
  nowHour,
  spanHours,
  timeLabel,
  today,
} from "./when.js";

// --- the smallest possible DOM helper ---------------------------------------
//
// `app/dom.js` would do, and is not imported for the same reason `api.js` is
// not: it is 200 lines carrying an SVG path, a `mount` that manages focus
// across re-renders, and a tristate attribute table, none of which this screen
// has any use for.

function el(spec, props = null, ...kids) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.classList.add(...String(value).split(/\s+/).filter(Boolean));
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat()) if (kid != null) node.append(kid);
  return node;
}

const $ = (sel) => document.querySelector(sel);

// --- state -------------------------------------------------------------------

/** The last health panel the server gave us, or null if the laptop has not
 *  been heard from at all today. Null is drawn as "what the queue holds, and
 *  nothing else", which on a phone that has not reached the laptop all morning
 *  is the truth.
 *
 *  Seeded from `held.js` rather than starting empty, and that seed is the whole
 *  of the "what did I already log?" fix. This was a bare `let` for as long as
 *  the app existed, which meant an entry was on screen only while the queue
 *  still owed it: sending — the thing you do when you next get near the laptop
 *  — is what emptied the screen, and it stayed empty for the rest of the day.
 *  Sent and unsent are the same list to the person holding the phone, and now
 *  they both survive both the send and the app being closed. */
let remembered = recall(today());
let panel = remembered?.panel ?? null;

/** When the laptop last answered, ISO, or null if it has not today. Printed
 *  under the log — see `heardLine`, which is where the argument for saying it
 *  out loud is. */
let heard = remembered?.at ?? null;

/**
 * Take a panel that just came off the wire.
 *
 * The one place `panel` is assigned, so that "the laptop answered" is written
 * down once: there is no way to put fresher rows on screen without also moving
 * the stamp that says how old the screen is, and no way to do either without
 * the copy that outlives the tab.
 */
function adopt(fresh) {
  panel = fresh;
  panelDay = today();
  heard = new Date().toISOString();
  remember(fresh);
  // The menu rides on the panel but outlives it: see its slot in `held.js`.
  // Taken only when the panel carries one, so a panel from a laptop still
  // running the build before menus cannot empty the list.
  if (Array.isArray(fresh?.meal_menu)) {
    menu = fresh.meal_menu;
    rememberMenu(menu);
  }
}

/**
 * The meal menu the laptop last sent — `logs.js` says what one is — kept
 * across days and across the laptop being asleep, because breakfast is when it
 * is wanted and the laptop has usually not been heard from since last night.
 */
let menu = recallMenu();

/**
 * Names typed into the form on screen that no meal has carried yet. Emptied on
 * commit, when the name moves into the queue with its meal and `mealMenu`
 * finds it there instead.
 */
let named = [];

/**
 * The menu as this phone knows it: the laptop's, plus every name still on its
 * way to the laptop — in the queue, or in the form. A name typed an hour ago
 * in a kitchen with no signal is a name the box should already find.
 */
const mealMenu = () => withNamed(menu, [
  ...pending()
    .filter((entry) => entry.path === "/api/meal" && entry.body?.preset)
    .map((entry) => ({ slug: entry.body.preset, label: entry.body.label })),
  ...named,
]);

/** Which day `panel` describes. `recall` will not hand back another day's, so
 *  this can only ever become stale by the app being left open across midnight —
 *  see the `visibilitychange` handler, which is where it is spent. */
let panelDay = panel ? today() : null;

// --- the day, and what is left of it when it is yesterday's --------------------
//
// `GET /api/day`, cached the way the health panel is and with one rule the
// health panel does not have: a copy from another day is *kept*, partially.
//
// That is not a nicety on this client. The laptop is asleep most of the day, so
// a phone opened on Tuesday morning may not have spoken to it since Monday
// evening — and the whole point of a schedule on a phone is that it is there on
// the morning you are running late, which is the morning you have not sat down
// at a laptop. A screen that went blank at midnight would go blank exactly when
// it was wanted.
//
// So `stillTrue` in `held.js` decides, once, which parts of a stale panel
// survive:
//
//   the schedule   yes, one day forward, and *only* one. The route sends
//                  tomorrow's events with today's for this. Two days would be
//                  guessing: an event moved on Monday night is wrong by
//                  Wednesday, and a schedule that is confidently wrong is worse
//                  than one that says it does not know.
//   the todo list  yes, unchanged. An overdue reading is still overdue in the
//                  morning, and the one thing you can do to a row here is tick
//                  it, which is addressed by id and is right whenever it is
//                  done. What shifts is only which heading a row falls under,
//                  and `groupByDay` is given today's date rather than the
//                  panel's, so it re-groups correctly against the new day.
//   what is owed   no. Yesterday's "3 of 5 done" under today's heading is not
//                  stale, it is false — the day it describes is over, and the
//                  new one has not been looked at. Dropped, and the tab says
//                  why rather than showing a zero it did not earn.

const heldDay = recallDay();

/** The laptop's last word on the day, or null. May describe yesterday — see
 *  `dayFor` and `stillTrue`. */
let day = heldDay?.day ?? null;

/** When that was heard, ISO, or null. */
let dayHeard = heldDay?.at ?? null;

/**
 * Take a `DayPanel` off the wire.
 *
 * The one place `day` is assigned from a response, on the same argument as
 * `adopt`: fresher rows and the stamp that says how old they are move together
 * or not at all.
 */
function adoptDay(fresh) {
  day = fresh;
  dayHeard = new Date().toISOString();
  rememberDay(fresh);
}

/** Which day the cached panel is about, or null when there is none. */
const dayFor = () => day?.date ?? null;

/** Yesterday, as an ISO date. `when.js` has no such thing on purpose — every
 *  other date question this app asks is about today or about an hour that has
 *  already happened — and this is the only caller. */
function yesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * What is left of the cached day, against the day you are standing in.
 *
 * `{ stale, events, owed }`, and the rule behind it is `stillTrue` in
 * `held.js` -- it lives there rather than here because it is the one thing in
 * this app that cannot be checked by looking at it. Reproducing it by hand
 * means a phone that has not reached the laptop since yesterday, and the shell
 * that would have to open in that state is a service worker, which does not
 * register in a headless browser. So it is pure, it is over there, and
 * `mheld.test.js` is what says it works.
 */
const left = () => stillTrue(day, today());

/** Whether what is on screen describes a day that is over. */
const dayStale = () => left().stale;

/**
 * Whether this morning's check-in is in: on the laptop, or queued to go.
 *
 * The check-in is a once-a-day screen — answered 26 mornings of the first 28 —
 * and after that it was a cap in the bar nobody pressed and, worse, the tab
 * every open landed on. So this decides whether it has a cap at all, and
 * whether the app opens on it.
 */
function checkedIn() {
  return panel?.readiness_today != null
    || pendingOn().some((entry) => entry.path === "/api/readiness");
}

/**
 * Which tab is open. One bar for the whole app since 2026-09-23: the log and
 * the day used to be two roots behind an entrance screen, and every open cost
 * a tap on the entrance before the tap on a log. With meal and drink folded
 * into `eat` and the check-in gone once it is done, both halves fit one bar —
 * see `TABS`.
 *
 * Opens on the check-in until it is done, and on `eat` after, which is where
 * well over half of a day's taps go.
 */
let tab = checkedIn() ? "eat" : "checkin";

/** Which of `eat`'s two ladders is up. */
let intake = "meals";

/** Set when a meal has just gone in and the screen turned to drinks on its
 *  own, so the drink ladder can say why it is there. Cleared by anything else. */
let offered = false;

/** The day the tab was last chosen on. Coming back to the app on a new day
 *  goes back to the check-in if it is owed — see the `visibilitychange`
 *  handler. */
let openedOn = today();

/** The hour every entry is filed against. One control for all five logs: the
 *  case it exists for — noticing at three that lunch never got logged — is one
 *  you are in before you have picked a log. Reset to the real hour whenever the
 *  app is brought back to the front, so a phone unlocked at six does not file
 *  dinner at eleven this morning. */
let hour = nowHour();
let hourOpen = false;

/** The check-in's five answers, in *stored* form, held until all five are in.
 *  A partial questionnaire is not a row the table has any use for, and the
 *  request model requires all five, so this is the one log with a draft.
 *  Persisted, because it is answered in a bathroom before a shower and the
 *  phone will lock halfway through. */
const DRAFT_KEY = "dashboard.m.checkin";

function draft() {
  try {
    const held = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}");
    return held.date === today() ? (held.answers ?? {}) : {};
  } catch {
    return {};
  }
}

function setDraft(answers) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ date: today(), answers }));
  } catch { /* see queue.js — a screen that will not draw is worse */ }
}

/** A stored time, `HH:MM` 24h — `ClockTime` in `models/common.py`. */
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Into bed and up, as this phone usually has them — what the check-in's night
 *  starts from when nothing else has answered it. Per phone and nowhere else:
 *  a default is a convenience of the thing you type on, not a fact about a
 *  day, so the server never hears of it. Only the answer it produces is sent.
 *
 *  The cost of a default, said once here: a night that was not the usual one
 *  and was saved without touching the wheels is filed as the usual one, and
 *  nothing afterwards can tell the two apart. The line above the wheels says
 *  "from your defaults" for exactly that reason. */
const NIGHT_KEY = "dashboard.m.night";

/** `MAX_IN_BED_HOURS` in `models/api.py`. A time wheel invites an am/pm slip,
 *  and bed at 11am, up at 7am reads as twenty hours. */
const MAX_IN_BED = 14;

function nightDefaults() {
  try {
    const held = JSON.parse(localStorage.getItem(NIGHT_KEY) ?? "null");
    return held && HHMM.test(held.bed) && HHMM.test(held.woke) ? held : null;
  } catch {
    return null;
  }
}

function setNightDefaults(night) {
  try {
    localStorage.setItem(NIGHT_KEY, JSON.stringify(night));
  } catch { /* the same shrug as `setDraft` */ }
}

/** The session being built: which prescribed block it answers, if any, then the
 *  three things every session is, then the two it may also carry. Not
 *  persisted: it is answered in one sitting on the way out of a gym, and a
 *  stale kind from Tuesday is worse than an empty form.
 *
 *  `quality` and `note` are null and "" rather than absent, and both stay
 *  optional through the commit — see `SessionRequest`, which defaults them. A
 *  form that will not submit until you have rated the session is a form that
 *  teaches you to press 3.
 *
 *  `started` is the opposite case and is required: when the session proper
 *  began, after the warm-up. It is a clock time you can read off your own
 *  memory of walking onto the floor, not a judgement — so it cannot be answered
 *  badly the way a rating can, and it is never pre-filled, because the only
 *  default available is the time you are logging it, which is the end. */
let session = { pick: null, path: [], rpe: null, started: "", minutes: "", quality: null, note: "" };

/** `session.pick` for a session the plan never asked for.
 *
 *  A value rather than `null` so the button can light up. "Nothing picked yet"
 *  and "deliberately off-plan" offer an identical form, and without a name for
 *  the second the only way to say you had chosen it would be a button that
 *  never lights — which reads as a tap that did not register. */
const FREEHAND = "free";

// --- talking to the server ----------------------------------------------------

/**
 * One write. It goes in the queue and stops there.
 *
 * Never `fetch` directly, even when the server is plainly reachable. Queue-then-
 * flush is one path with one set of failure modes; posting directly when online
 * and queueing when not is two paths, and the second one is exercised about
 * twice a year on a phone in a basement, which is exactly when you find out it
 * was never right.
 *
 * This used to flush on the way out, so a write on a live connection landed
 * immediately and the queue was a fallback. It is not a fallback any more — see
 * `sendNow`. The reason is that "try the network on every write" makes every
 * tap in the app a thing that can hang: on a captive-portal wifi, or a tailnet
 * whose other end is asleep, the `fetch` sits open and the second question of
 * the meal you are logging waits on it. Logging is now entirely local and
 * entirely instant, and reaching the server is one deliberate button.
 */
function send(path, body, label) {
  const entry = enqueue(path, { date: today(), ...body }, label);
  // `null` means storage refused the write — out of quota, or switched off —
  // and the entry is gone rather than merely unsent. It used to fail here in
  // silence, which on a screen that draws itself from the queue looks exactly
  // like a tap that did not register.
  if (!entry) toast(`could not save that ${label} — the phone’s storage is full`);
  draw();
  return entry;
}

/**
 * The same, for the day screen's two writes — with no `date` on the body.
 *
 * `TodoDoneRequest` and `TodoWriteRequest` forbid unknown fields, and their day
 * is called `today`. So these cannot go through `send`, which injects `date`,
 * and the server would refuse them with a 422 — a 4xx, which the flush drops
 * rather than retries, so the tick would vanish and be reported as the server
 * refusing something.
 *
 * The other half of that is `pendingOn`, which finds log rows by `body.date`
 * and so cannot see these at all. Correct: a ticked todo is not a row on any
 * log. The day screen reads them straight off `pending()` by path — see
 * `queuedTicks` and `queuedAdds`.
 */
function sendDay(path, body, label) {
  const entry = enqueue(path, { today: today(), ...body }, label);
  if (!entry) toast(`could not save that ${label} — the phone’s storage is full`);
  draw();
}

/**
 * How long to wait on the laptop before deciding it is not there.
 *
 * There is no good unbounded answer. An unreachable host on a normal network
 * refuses in milliseconds, but a hotel portal or a network that blackholes
 * outbound traffic leaves the socket open until the OS gives up, which is tens
 * of seconds — and on a screen whose entire premise is that the server is
 * usually absent, tens of seconds of spinner is the app appearing broken in
 * precisely the situation it was built for. Four seconds is well past a
 * healthy tailnet round trip and well short of noticing.
 */
const REACH_MS = 4000;

async function load() {
  try {
    const response = await fetch(`/api/health?date=${today()}`, {
      signal: AbortSignal.timeout(REACH_MS),
    });
    if (!response.ok) return;
    // The same rule the flush uses, for the same reason: a 200 says something
    // answered. A portal that hands back JSON of its own would be adopted as
    // the panel, and `panel?.meals_today` on it is `undefined` — which draws
    // as "nothing eaten yet today" over a day of meals that are sitting in the
    // table. Keeping the panel we have is the honest reading of an answer that
    // did not come from the dashboard.
    const body = await response.json();
    if (isPanel(body)) adopt(body);
    return true;
  } catch {
    /* Offline, or slow enough to count as offline. `panel` stays as it was — or
       null on a cold start, which draws the queue alone rather than an error
       page. There is nothing to do about an unreachable server on a screen
       whose whole job is to work without one. */
    return false;
  }
}

/**
 * How long `/api/day` gets, which is longer than the health panel gets.
 *
 * Not caution — a measurement. The route reads the whole boot payload in about
 * three hundred milliseconds and then asks gcalcli for two days of calendar,
 * which is a subprocess talking to Google: warm out of its cache that is
 * nothing, and cold it is three seconds. `REACH_MS` would time out on the cold
 * one, every time the cache had turned over, and the schedule would then be a
 * feature that worked only on the second try.
 *
 * The wait costs nothing to look at because it is never the first thing on
 * screen: whatever was cached is drawn before this is sent, and the panel is
 * replaced under you when it lands.
 */
const DAY_MS = 9000;

/** In flight, so a second visit to the day's tabs does not start a second one. */
let loadingDay = false;

async function loadDay() {
  if (loadingDay) return false;
  loadingDay = true;
  try {
    const response = await fetch(`/api/day?date=${today()}`, {
      signal: AbortSignal.timeout(DAY_MS),
    });
    if (!response.ok) return false;
    // `isDay` for the reason `isPanel` is used above: a portal's 200 is not an
    // answer, and this one would be *written to storage* and drawn as a day
    // with nothing owed and nothing due.
    const body = await response.json();
    if (isDay(body)) adoptDay(body);
    return true;
  } catch {
    return false;
  } finally {
    loadingDay = false;
    draw();
  }
}

/**
 * The send button: everything in the queue, then a fresh panel.
 *
 * The one place in the app that talks to the server, and it is pressed rather
 * than triggered. That is the whole design decision — see the note above `send`
 * — and what it buys is that the failure is *legible*. A flush that fires on
 * `visibilitychange` fails silently into a counter that does not go down; a
 * flush you asked for can say "the laptop is not answering", which is a
 * sentence you can do something about (wake it, check Tailscale) instead of a
 * number you learn to distrust.
 *
 * `flush` stops at the first entry it cannot send and leaves the rest, so a
 * partial send is normal and is reported as one.
 */
let sending = false;

/**
 * Why the drain stopped, said to the person holding the phone.
 *
 * `intercepted` is the one worth spelling out. It means something on this
 * network answered the write with a 200 that was not the dashboard — a gym or
 * hotel portal, almost always — and the entries are still here. Before this
 * was noticed those writes were counted as delivered and destroyed, so the
 * sentence is deliberately about the network and not about the laptop: waking
 * the laptop is the wrong thing to go and do.
 */
const WHY = {
  unreachable: "the laptop is not answering",
  intercepted: "something on this wifi answered instead of the laptop — sign in to it first",
  server: "the laptop answered with an error",
  busy: "already sending",
};

async function sendNow() {
  if (sending) return;
  if (!pending().length) return;
  sending = true;
  draw();
  try {
    const { panel: got, panelDate, sent, stopped } = await flush({
      onRefused: (entry, status) =>
        toast(`the server refused “${entry.label}” (${status}) — it has been dropped`),
    });

    // The panel a write hands back describes the day that write was *for*, and
    // the queue is not a view of a day — a flush on Wednesday morning may end
    // on Tuesday's last meal and hand back Tuesday. Adopting that would draw
    // yesterday's rows under today's heading, so it is only taken when it is
    // today's, and `load` is what fills the screen in either case.
    if (got && panelDate === today()) adopt(got);
    // Only worth a round trip if something actually went; otherwise the panel
    // we have is as good as the one we would get, and this is a request that
    // will hang for `REACH_MS` on the connection that just failed.
    if (sent) await load();
    // And the day, which a flush can have changed in ways the flush cannot
    // report: a ticked todo comes back as a `TodoPanel` and an added one as a
    // `CalendarWrite`, and neither is a shape this screen draws — see the note
    // in `queue.js` about why the flush no longer adopts them. Refetched only
    // if there is a day on screen to correct, so draining a pocketful of meals
    // does not also fetch a calendar.
    if (sent && day) await loadDay();
    // And which screens were opened, on the only trip to the laptop this app
    // takes. After everything else and only if something went, so it is never
    // what a meal waits behind and never a request to a laptop that just
    // failed to answer.
    if (sent) await sendTally();

    // Counted, not subtracted. `pending()` before minus `pending()` after is
    // wrong the moment a meal is logged during the flush — which is exactly
    // what happens on a slow tailnet, where the round trip is long enough to
    // go on using the app — and it reported a successful send as "nothing
    // sent". `sent` is what the dashboard actually took.
    const left = pending().length;
    if (!sent) toast(`${WHY[stopped] ?? "nothing sent"} — nothing sent`);
    else if (left) toast(`sent ${sent}, ${left} still waiting — ${WHY[stopped] ?? "stopped"}`);
    else toast(sent === 1 ? "sent" : `sent ${sent}`);
  } finally {
    sending = false;
    draw();
  }
}

/**
 * The screen-open counts, to the laptop. See the head of `tally.js`.
 *
 * Silent either way, and not queued: the tally is not a write anyone made, a
 * failure loses nothing (the counts stay here and go next time), and the
 * laptop keeps the larger of what it had and what it is sent, so sending the
 * same four weeks again is free. A laptop still running a build without the
 * route answers 404, which is the same nothing — and so, unlike every write in
 * the queue, the reply is not read at all: nothing here changes on delivery.
 */
async function sendTally() {
  const days = tallies();
  if (!Object.keys(days).length) return;
  try {
    await fetch("/api/usage", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ days }),
      signal: AbortSignal.timeout(REACH_MS),
    });
  } catch { /* next send */ }
}

// --- what is logged today ------------------------------------------------------
//
// Server rows and queued rows drawn as one list, because to the person holding
// the phone they are the same list: a meal you logged is a meal you logged, and
// whether the laptop has heard about it yet is the laptop's problem. The queued
// ones are marked, and that is the whole difference.

const queuedFor = (path) =>
  pendingOn().filter((e) => e.path === path).map((e) => ({ ...e.body, queued: true, qid: e.id }));

/**
 * Rows the queue is on its way to deleting, by the id they carry on the far
 * side.
 *
 * A delete goes through the queue like every other write, which left the row
 * it removes sitting on screen until the next successful flush — the tap
 * looked like it had missed, so it got tapped again, and the second delete
 * came back a 404 the flush then reported as the server refusing something.
 * Struck from the list the moment it is queued instead: what is drawn is what
 * the day will look like once the queue has drained, which is what the rest of
 * this screen already promises.
 */
const deletedBy = (path) =>
  new Set(pending().filter((e) => e.path === path).map((e) => e.body?.entry_id ?? e.body?.session_id));

/** Earliest first, by the hour and the minute where there is one. */
const byClock = (a, b) =>
  ((a.hour ?? 0) * 60 + (a.minute ?? 0)) - ((b.hour ?? 0) * 60 + (b.minute ?? 0));

const rowsFor = (path, fromPanel, deletePath) => {
  const going = deletePath ? deletedBy(deletePath) : new Set();
  return [
    ...(fromPanel ?? []).filter((row) => !going.has(row.id)),
    ...queuedFor(path),
  ].sort(byClock);
};

/** Today's sessions as this screen understands them: what the laptop has, plus
 *  what is still queued, minus what a queued delete is on its way to removing. */
const sessionRows = () =>
  rowsFor("/api/session", panel?.sessions_today, "/api/session/delete");

// --- the logs -------------------------------------------------------------------
//
// Each is `{ id, label, render }`. `render` returns the body of the screen; the
// tab bar, the day and the hour are the shell's and are the same for all five.

/**
 * Which grid a row of caps gets: five across, three, or two.
 *
 * Three is the default and the right answer for `small` `medium` `large`. It is
 * the wrong one for `conditioning` and `electrolyte`. A third of the panel on a
 * 375px phone is about eight characters of the display face at 10px, and a word
 * longer than that has nowhere to break — so the cap overflowed its column and
 * the label was clipped off at the edge of the panel. In the gym that means the
 * kind you are reaching for is the one you cannot read.
 *
 * It is also the wrong one for a *scale*. `1 2 3 4 5` at three across wraps to
 * `1 2 3` over `4 5`, which is the one arrangement that makes a five-point
 * answer read as two different questions — and a scale is the one row where
 * the shape of it is half the meaning. Five across is what a 375px row holds
 * at a thumb's width, so the five-point rows are one line and the ten-point
 * RPE row is two of five, which is what it has always drawn.
 *
 * All three decided by measuring the labels rather than by naming the rows
 * that have the problem, so an answer added to `logs.js` — the file whose
 * whole point is that it is edited once — gets the grid it needs without a
 * second edit here. A scale is recognised the same way: every label a digit or
 * two, which is true of the four rows that are scales and of nothing else in
 * the file.
 */
const FITS_IN_A_THIRD = 8;
const A_SCALE_LABEL = 2;

const optsClass = (labels) => {
  const measured = labels.map((label) => String(label).length);
  if (measured.every((n) => n <= A_SCALE_LABEL)) return "div.opts.opts-5";
  return measured.some((n) => n > FITS_IN_A_THIRD) ? "div.opts.opts-2" : "div.opts";
};

// --- the ladder ----------------------------------------------------------------

/**
 * Three questions open at a time, and every answered one shrunk to a line.
 *
 * He asked for this on 2026-09-23 — "I hate scrolling" — and it reverses what
 * `stepRow` used to say: every question on screen, all the time. That rule was
 * right about *noticing* (a question behind a button is a question you forget)
 * and wrong about the price, which was a check-in you scrolled through. The
 * ladder keeps what the rule was for: nothing waits behind a button you have
 * to know to press, only behind the question before it. The rail says how many
 * are left, every answered line is a tap back to its question, and the ‹ on
 * the top open question walks back one.
 *
 * Picked from a preview of six motions (the "ladder motions" artifact): the
 * CRT, three open, answered rows shrinking to a line rather than vanishing.
 * The session log is not on it — scrolling is fine there, his words.
 *
 * State is almost all derived. A row is done when it has an answer, or was
 * *passed* (skipped, or a multi-pick closed with `done ›`), and has not been
 * reopened. Open is every reopened row plus the earliest rows not done, three
 * in all; the rest wait. So a draft restored from storage, or a morning
 * already saved, lands in the right shape with no ladder state at all — the
 * two sets kept here are only the facts no answer records.
 */
const LADDER_OPEN = 3;

/** How long a row's motion lasts. The longest keyframe below is the shut. */
const LADDER_MS = 560;

const ladders = new Map();
const ladderOf = (id) => {
  if (!ladders.has(id)) ladders.set(id, { passed: new Set(), reopened: new Set(), seen: new Map() });
  return ladders.get(id);
};

/** A row answered or moved past. It closes, even if it had been reopened.
 *  Called *before* the draw the answer causes, so that draw is the one that
 *  shrinks it. */
function settle(id, name, { pass = false } = {}) {
  const lad = ladderOf(id);
  lad.reopened.delete(name);
  if (pass) lad.passed.add(name);
}

/** Back to a row — done or still waiting. Re-added so the set's order is the
 *  order of reopening, and the oldest lets go once more are open than fit. */
function reopenRow(id, name) {
  const lad = ladderOf(id);
  lad.reopened.delete(name);
  lad.reopened.add(name);
  while (lad.reopened.size > LADDER_OPEN) lad.reopened.delete(lad.reopened.values().next().value);
  draw();
}

/** The entry went: the next one starts from the top. */
function resetLadder(id) {
  const lad = ladderOf(id);
  lad.passed.clear();
  lad.reopened.clear();
}

/** Which ladder the last draw drew, and which this one is drawing. A row only
 *  moves when the same ladder was on screen the draw before: opening a tab or
 *  coming back from home draws it standing still. */
let ladderWas = null;
let ladderNow = null;

const stillMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * A log's questions as a ladder: the rail, then each row as a question, a line
 * or nothing.
 *
 * `rows` are `{ name, label, complete, full, mini, meter?, closer?, skip? }`.
 * `full()` draws the question and `mini()` the words on its line; `meter` is
 * `[n, of]` for a scale, drawn as bars beside the digit; `closer` is the cap
 * under a question no single tap finishes (a multi-pick, the night's wheels);
 * `skip` is text in the head of one that may be left blank. `focus` names the
 * one row that gets the screen to itself — the meal's name while its menu or
 * box is open, which is the only thing on these screens tall enough to need it.
 *
 * **Why keyframes, and why `--t`.** `draw()` rebuilds the whole view on every
 * change, so there is no element that persists from one state to the next for
 * a transition to run on. Each draw compares what a row is now against what it
 * was the draw before and, if it changed, draws it mid-motion: a keyframe runs
 * on an element's first frame, and a draw that lands while it is still running
 * redraws it with a negative delay of however long it has been going, so a tap
 * during a motion carries on from where it was rather than starting it again.
 *
 * The shut draws the question it is shutting as well as the line it becomes,
 * `inert`, because the CRT line has to be *of something*; it is gone by the
 * next draw.
 */
function ladderRows(id, rows, { focus = null } = {}) {
  const lad = ladderOf(id);
  const moving = ladderWas === id && !stillMotion();
  ladderNow = id;
  const now = Date.now();

  const done = (row) => !lad.reopened.has(row.name) && (row.complete || lad.passed.has(row.name));
  const waiting = rows.filter((row) => !done(row));
  const forced = waiting.filter((row) => lad.reopened.has(row.name));
  const open = new Set([
    ...forced,
    ...waiting.filter((row) => !lad.reopened.has(row.name)).slice(0, Math.max(0, LADDER_OPEN - forced.length)),
  ].map((row) => row.name));

  const stateOf = (row) => (open.has(row.name) ? "open" : done(row) ? "done" : "waiting");
  const firstOpen = rows.find((row) => open.has(row.name));
  // The ‹ goes to the last answered row above the top open one.
  const behind = firstOpen
    ? [...rows.slice(0, rows.indexOf(firstOpen))].reverse().find((row) => done(row))
    : null;

  const full = (row, live) => {
    const node = row.full();
    if (!live) return node;
    const head = node.querySelector(".q-head");
    if (row === firstOpen && behind) {
      head?.prepend(el("button.lad-back", {
        text: "‹",
        "aria-label": `back to ${behind.label}`,
        onclick: () => reopenRow(id, behind.name),
      }));
    }
    if (row.skip) {
      head?.append(el("button.lad-skip", {
        text: row.skip,
        onclick: () => { settle(id, row.name, { pass: true }); draw(); },
      }));
    }
    if (row.closer) {
      node.append(el("button.opt.lad-next", {
        text: row.closer.text,
        disabled: row.closer.disabled,
        onclick: () => { settle(id, row.name, { pass: true }); draw(); },
      }));
    }
    return node;
  };

  const mini = (row) => {
    const text = row.mini();
    return el("button.lad-mini", {
      "aria-label": `${row.label}: ${text} — tap to change`,
      onclick: () => reopenRow(id, row.name),
    },
      el("span.lad-n", { text: row.label }),
      el("span.lad-lead"),
      row.meter
        ? el("span.lad-meter", null, ...Array.from({ length: row.meter[1] }, (_, i) =>
            el("i", { class: i < row.meter[0] ? "lit" : "" })))
        : null,
      el("span.lad-v", { text }),
    );
  };

  // `--t` is how far into its motion a row already is, as a negative delay.
  const wrap = (node, motion, at, inert = false) =>
    el("div.lad", { class: motion, style: `--t:${at - now}ms`, inert },
      el("div.lad-clip", null, el("div.lad-body", null, node)));

  const drawn = [];
  const seen = new Map();
  for (const row of rows) {
    const hidden = Boolean(focus && row.name !== focus);
    const state = stateOf(row);
    const shape = hidden ? "none" : state === "open" ? "full" : state === "done" ? "mini" : "none";
    const before = lad.seen.get(row.name) ?? { shape: "none", hidden: false, at: 0, move: null };

    // A change starts a motion; no change carries on any motion still running.
    // Folding away for the menu, or back after it, is a plain fold: the CRT is
    // for a question being answered, not for one stepping aside.
    let { at, move } = before;
    if (!moving) move = null;
    else if (before.shape !== shape) {
      move = { from: before.shape, kind: before.hidden !== hidden ? "fold" : "crt" };
      at = now;
    }
    if (move && now - at >= LADDER_MS) move = null;
    seen.set(row.name, { shape, hidden, at, move });

    if (move?.from === "full" && shape !== "full") drawn.push(wrap(full(row, false), `${move.kind}-out`, at, true));
    if (move?.from === "mini" && shape === "none") drawn.push(wrap(mini(row), "fold-out", at, true));
    if (shape === "full") drawn.push(move ? wrap(full(row, true), `${move.kind}-in`, at) : full(row, true));
    if (shape === "mini") {
      drawn.push(move && move.from !== "mini"
        ? wrap(mini(row), move.kind === "crt" ? "mini-in" : "fold-in", at)
        : mini(row));
    }
  }
  lad.seen = seen;

  const rail = el("div.lad-rail", null,
    ...rows.map((row) => {
      const state = stateOf(row);
      return el("button.lad-pip", {
        class: state,
        "aria-label": `${row.label} — ${state}`,
        onclick: state === "open" ? null : () => reopenRow(id, row.name),
      });
    }));

  return [rail, ...drawn];
}

/** The digits of a scale answered on a line: `[n, of]`, or null for a row that
 *  is not one. A scale is recognised as `optsClass` recognises it. */
function meterOf(step, draftValues) {
  const options = stepOptions(step, draftValues);
  if (!options.length || !options.every(([, , label]) => /^\d+$/.test(String(label)))) return null;
  const at = options.findIndex(([, value]) => value === draftValues[step.name]);
  return at < 0 ? null : [at + 1, options.length];
}

/**
 * A row of answer buttons for one step of a quick log.
 *
 * Every answer in the row visible, answered in any order, and the entry posts
 * on a separate press. On the desktop the equivalent is three keystrokes and
 * this would be a waste of a card; here there is no keyboard and a wizard that
 * asks one question per screen is three taps and two animations to say "a
 * large clean meal" — which is why the ladder opens three of these at once
 * rather than one.
 */
function stepRow(step, draftValues, answer) {
  // A `multi` step holds an array and a plain one holds a value. The two are
  // told apart by the step rather than by what is in the draft, because the
  // empty array — no tags, a glass of water — is a real answer and an
  // unanswered step is `undefined` on both.
  const held = draftValues[step.name];
  const picked = step.multi ? new Set(held ?? []) : null;
  const isOn = (value) => (step.multi ? picked.has(value) : held === value);
  // Through `stepOptions` rather than off the step, because the drink log's
  // name row is a function of the tags above it. Read at draw time, so the row
  // rebuilt after a tag is tapped is the row for the new tag set.
  const options = stepOptions(step, draftValues);
  const press = (value) => {
    // Tapping the answer that is already on takes it off. Only where the step
    // is optional, which is where it means something: a preset or a drink's
    // name can be un-picked back to "other", and there is no other way to undo
    // one, because every other row on this screen has a right answer you would
    // reach for instead.
    if (step.optional && !step.multi && held === value) return answer(step.name, null);
    if (!step.multi) return answer(step.name, value);
    // Toggling in place: tapping a tag that is on takes it off, so a wrong tap
    // costs the tap that undoes it and nothing else. Rebuilt from the step's
    // own order rather than from tap order, so the row reads the same whichever
    // way round it was answered.
    const next = new Set(picked);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    answer(step.name, options.map(([, v]) => v).filter((v) => next.has(v)));
  };

  const caps = el(optsClass(options.map(([, , label]) => label)), null,
    ...options.map(([, value, label]) =>
      el("button.opt", {
        class: isOn(value) ? "on" : "",
        "aria-pressed": isOn(value),
        text: label,
        onclick: () => press(value),
      }),
    ),
  );

  // Every answer in the row on screen while the row is open. The one row that
  // is not like this is the meal's name, and `menuRow` is why.
  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: step.name }),
      step.hint ? el("span.q-hint", { text: step.hint }) : null,
    ),
    caps,
  );
}

/**
 * What the meal was: a few likely caps, the menu behind one button and a name
 * box behind another.
 *
 * Laid out on what the research on adaptive menus agrees on, for someone who
 * eats the same few things over and over:
 *
 *   - **Split the part that adapts from the part you learn.** The top row is
 *     `likelyMeals` for the hour on the bar and changes all day. The tree
 *     under `menu` holds its order for a week (`dashboard/menu.py`) so a thumb
 *     can learn where things are (Sears & Shneiderman's split menus; Findlater
 *     & McGrenere 2004).
 *   - **Broad and shallow.** Two levels at most, a base meal and its
 *     variations, because every level is a tap and a read (Kiger 1984; Larson
 *     & Czerwinski 1998). The usual meal is two taps: its cap, then `log`.
 *   - **Recognise, don't recall.** The box searches every name ever given as
 *     you type, so a variation gets named once and found after that, and
 *     `chicken rice` cannot fork from `chicken + rice`.
 *
 * Naming while a meal is picked makes the new name a variation of it — `salsa`
 * under chicken + rice — which is the whole of how the tree grows. The chip
 * beside the box says so and takes it off.
 *
 * The box is built once per draw and updated in place while typing. A redraw
 * per keystroke would rebuild the `<input>` under the thumb and drop the
 * keyboard after every letter.
 */
function menuRow(step, draft, answer, picker) {
  const all = mealMenu();
  const chosen = draft[step.name] ?? null;
  const redraw = () => draw();

  const pick = (slug, { toggle = true } = {}) => {
    picker.view = null;
    picker.branch = null;
    picker.typed = "";
    // Tapping the name that is already on takes it off, as on every optional
    // row — the only way to say "some other meal" once one is picked.
    answer(step.name, toggle && slug === chosen ? null : slug);
  };
  const cap = (item, text = mealName(all, item.slug)) =>
    el("button.opt", {
      class: item.slug === chosen ? "on" : "",
      "aria-pressed": item.slug === chosen,
      text,
      onclick: () => pick(item.slug),
    });
  const grid = (caps) => el(optsClass(caps.map((c) => c.textContent)), null, ...caps);

  const likely = likelyMeals(all, hour);
  // A picked name that is not among the likely ones gets its own line rather
  // than bumping one of them: the likely row must not move because you tapped.
  const offRow = chosen && !likely.some((item) => item.slug === chosen);

  const opener = (view, text) =>
    el("button.opt.menu-open", {
      class: picker.view === view ? "open" : "",
      "aria-expanded": picker.view === view,
      text,
      onclick: () => {
        picker.view = picker.view === view ? null : view;
        picker.branch = null;
        picker.under = true;
        redraw();
        // In the tap's own call stack, which is what iOS asks before it will
        // raise a keyboard for a field that was not the thing touched.
        if (picker.view === "name") document.querySelector(".name-box")?.focus();
      },
    });

  return el("div.q.q-menu", null,
    el("div.q-head", null,
      el("span.q-name", { text: "what" }),
      !chosen ? el("span.q-hint", { text: step.hint }) : null,
    ),
    // Off while the menu or the box is open, which is also when the ladder
    // gives this row the screen (`focus` in `quickLog`): opening either one is
    // saying the likely three were not it, and the menu holds them anyway.
    likely.length && !picker.view ? grid(likely.map((item) => cap(item))) : null,
    offRow && !picker.view
      ? el("button.opt.on.menu-chosen", {
          "aria-pressed": true,
          text: mealName(all, chosen),
          title: "tap to clear",
          onclick: () => pick(chosen),
        })
      : null,
    el("div.opts.opts-2.menu-tools", null,
      opener("menu", picker.view === "menu" ? "menu ▴" : "menu ▾"),
      opener("name", chosen ? "name a variation" : "name it"),
    ),
    picker.view === "menu" ? menuTree(all, chosen, cap, grid, picker, redraw) : null,
    picker.view === "name" ? nameBox(all, chosen, cap, grid, picker, pick, redraw) : null,
  );
}

/** The tree, one level at a time: the base meals, or one of them opened. */
function menuTree(all, chosen, cap, grid, picker, redraw) {
  const tree = mealTree(all);
  if (!tree.length) {
    return el("p.menu-note", {
      text: "nothing on the menu yet — name what you ate, and three times in three weeks puts it here",
    });
  }
  const open = picker.branch ? tree.find((root) => root.slug === picker.branch) : null;
  if (open) {
    return el("div.menu-level", null,
      el("button.menu-crumb", {
        text: `‹ ${open.label}`,
        onclick: () => { picker.branch = null; redraw(); },
      }),
      grid([cap(open, "plain"), ...open.children.map((child) => cap(child, child.label))]),
    );
  }
  return el("div.menu-level", null,
    grid(tree.map((root) => (root.children.length
      // A base meal with variations opens rather than answering: the variation
      // is the likelier answer, and "plain" is the first cap inside.
      ? el("button.opt.branch", {
          class: chosen === root.slug || chosen?.startsWith(`${root.slug}.`) ? "on" : "",
          text: `${root.label} ▸`,
          onclick: () => { picker.branch = root.slug; redraw(); },
        })
      : cap(root, root.label)))),
  );
}

/** Type a name: matches as you go, and `save` for one nobody has used yet. */
function nameBox(all, chosen, cap, grid, picker, pick, redraw) {
  // Hang the new name under the base meal of whatever is picked — a variation
  // of a variation is a sibling, because the tree is two levels deep.
  const base = chosen ? (mealItem(all, chosen)?.parent ?? chosen) : null;
  const parent = base && picker.under ? base : null;

  const box = el("input.amount.name-box", {
    type: "text",
    value: picker.typed ?? "",
    maxlength: MEAL_LABEL_MAX,
    placeholder: parent ? "what was different?" : "what was it?",
    autocomplete: "off",
    autocapitalize: "none",
    enterkeyhint: "done",
    "aria-label": "meal name",
  });
  const hits = el("div.menu-hits");
  const save = el("button.go.small", { text: "save" });

  const refresh = () => {
    picker.typed = box.value;
    const found = mealMatches(all, box.value);
    hits.replaceChildren(found.length ? grid(found.map((item) => cap(item))) : "");
    save.disabled = !newMealSlug(box.value, parent);
    box.classList.remove("bad");
  };
  const commitName = () => {
    const typed = box.value.trim();
    // Already a name — by slug or by its words — is that name, not a twin.
    const same = sameMeal(all, typed, parent);
    if (same) return pick(same.slug, { toggle: false });
    const slug = newMealSlug(typed, parent);
    if (!slug) return void box.classList.add("bad");
    named = [...named.filter((n) => n.slug !== slug), { slug, label: typed }];
    pick(slug, { toggle: false });
  };
  box.addEventListener("input", refresh);
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") commitName();
  });
  save.addEventListener("click", commitName);
  refresh();

  return el("div.menu-level", null,
    base
      ? el("button.opt.menu-under", {
          class: parent ? "on" : "",
          "aria-pressed": Boolean(parent),
          text: parent ? `a variation of ${mealName(all, base)} ×` : `make it a variation of ${mealName(all, base)}`,
          onclick: () => { picker.under = !picker.under; redraw(); },
        })
      : null,
    el("div.row", null, box, save),
    hits,
  );
}

/**
 * A quick log — meals, drinks, in and out.
 *
 * All three are the same shape and always were — the desktop had its own
 * factory making the same observation, and it went with the cards. The branch
 * (`when`) is honoured through `applicableSteps`, imported from `logs.js`
 * rather than reimplemented, so there is one answer to whether a urine entry
 * is asked for a solidity and it is not this file's.
 */
function quickLog({ id, label, steps, path, describe, empty, rows, remove, enrich, list, logged }) {
  let answers = {};
  /** Where the meal's name row is: which of its two buttons is open, which base
   *  meal the tree is opened at, what is in the name box, and whether a new
   *  name hangs under the picked meal. Cleared on commit along with the
   *  answers: the next meal starts from the same screen the last one did. */
  const fresh = () => ({ view: null, branch: null, typed: "", under: true });
  let picker = fresh();

  return {
    id,
    label,
    render() {
      const asked = applicableSteps(steps, answers);

      // Which steps are still owed is `logs.js`'s answer, not this file's. A
      // `multi` step is answered from the moment it is drawn — its answer is a
      // set and the empty set means something ("water") — and an `optional` one
      // says the same for a single value: not naming a drink is what most rows
      // do. Holding the commit button hostage to either would make the
      // commonest entry of the day the one you cannot file without tapping
      // something you did not mean.
      const missing = missingSteps(steps, answers);
      const ready = missing.length === 0;

      const commit = async (exact = false) => {
        if (!ready) return;
        // The date comes from the hour, not from the clock. `dateForHour` says
        // why: an hour later than now is yesterday's, and stamping it with
        // today's date files it in the future and loses it off the day it
        // happened on. `send` fills in today only when a body does not say.
        //
        // `exact` is the other case: it happened just now, so the clock *is*
        // the answer, minute and all, read at the tap rather than from the
        // hour bar — which can be an hour stale if the app sat open across one.
        const now = new Date();
        const body = exact
          ? { date: today(), hour: now.getHours(), minute: now.getMinutes(), ...answers }
          : { date: dateForHour(hour), hour, ...answers };
        enrich?.(body);
        answers = {};
        picker = fresh();
        resetLadder(id);
        // Before the send, because the send is what draws: whatever this
        // changes about the screen lands in the same frame as the entry.
        logged?.();
        await send(path, body, label);
      };

      // Answering only ever changes the selection. It used to *post* the moment
      // the last question was filled, which made the fastest possible entry —
      // two taps — also the easiest thing to do by accident: a stray thumb on
      // `quality 3` while scrolling wrote a meal, and the only way to find out
      // was to notice the row appear. A wrong tap is now free, and the write is
      // a deliberate second thing.
      const answer = (name, value) => {
        // A meal's name answers four questions with one tap, and what it fills
        // in is what the laptop learned from the meals it was logged as.
        // Written *under* the tap rather than over it, so the fills land and
        // the name itself stays what was pressed. Un-picking a name clears the
        // name and leaves what it filled in, which is the less surprising of
        // the two: nothing you can see disappears, and an edit you made after
        // tapping it survives.
        const step = steps.find((one) => one.name === name);
        const filled = value == null || !step?.menu ? {} : (mealFills(mealMenu(), value) ?? {});
        answers = { ...answers, ...filled, [name]: value };
        // Whatever that answer just invalidated, gone before the redraw. A
        // branch can strip a question that was already answered, and a row
        // whose options depend on the row above it can strip the *answer*
        // while keeping the question — see `prunedDraft`, which is where both
        // rules live and why neither is written out here.
        answers = prunedDraft(steps, answers);
        // A value closes its row; a tag toggled on a multi-pick does not —
        // that row closes on its own `done ›`. Un-picking an optional answer
        // (a null) leaves the row open, since you are still choosing. What a
        // name filled in closes too, the flags included: they were answered,
        // just not by a tap on their own row.
        if (value != null && !step?.multi) settle(id, name);
        for (const key of Object.keys(filled)) settle(id, key, { pass: true });
        draw();
      };

      // The words on an answered row's line.
      const said = (step) => {
        const value = answers[step.name];
        if (step.menu) return value ? mealName(mealMenu(), value) : "unnamed";
        if (step.field) {
          const what = AMOUNTS.find(([n]) => n === value)?.[1];
          return what ? `${say(value)} · ${what}` : `${say(value)} ${step.field.unit}`;
        }
        const options = stepOptions(step, answers);
        const labelOf = (v) => options.find(([, one]) => one === v)?.[2] ?? String(v);
        if (step.multi) return value?.length ? value.map(labelOf).join(" · ") : "none";
        return value == null ? "none" : labelOf(value);
      };

      // The meal's name gets the screen to itself while its menu or box is
      // open: nine base meals two across is taller than the rest of the log.
      const focus = picker.view ? MEAL_STEPS.find((step) => step.menu)?.name : null;

      const ladder = asked.map((step) => ({
        name: step.name,
        label: step.menu ? "what" : step.name,
        complete: !step.multi && answers[step.name] != null,
        full: () => step.field ? amountRow(step, answers, answer)
          : step.menu ? menuRow(step, answers, answer, picker)
          : stepRow(step, answers, answer),
        mini: () => said(step),
        meter: meterOf(step, answers),
        closer: step.multi ? { text: answers[step.name]?.length ? "done ›" : "none ›" } : null,
        skip: step.optional && !step.multi ? (answers[step.name] != null ? "keep ›" : "skip ›") : null,
      }));

      return [
        ...ladderRows(id, ladder, { focus }),
        // Disabled rather than hidden while the entry is incomplete: a button
        // that appears when you finish is a button you have to notice, and one
        // that is greyed the whole time you are answering is a progress
        // indicator you never have to read.
        //
        // The second button is the minute, and it is only offered while the
        // hour bar is on the hour it is now. "It happened exactly now" beside
        // an hour you scrolled back to is two answers that disagree, and the
        // row would have to pick one of them without saying so.
        el("div.commits", null,
          el("button.go.commit", {
            text: ready ? `log ${label}` : `pick ${missing.length} more`,
            disabled: !ready,
            onclick: () => commit(false),
          }),
          hour === nowHour()
            ? el("button.go.commit.exact", {
                text: "exact time",
                title: "log it at this minute — for something that just happened",
                disabled: !ready,
                onclick: () => commit(true),
              })
            : null,
        ),
        // Today's list goes with the rest while the name has the screen.
        // `list` is the eat tab's, which draws meals and drinks as one.
        focus ? null : list ? list() : entryList(rows(), describe, empty, { remove }),
      ];
    },
  };
}

/**
 * The one typed answer in the three quick logs: how many drinks.
 *
 * Argued for at length above `DRINK_KIND` in `logs.js` — the number is printed
 * on the side of the bottle, and bucketing it into small/medium/large would be
 * throwing away accuracy that cost nothing. On a phone it gets a numeric
 * keypad and three presets, because "1" is the answer nine times in ten and
 * opening a keyboard to type it is the tax this row would otherwise charge
 * every time.
 */
/**
 * How much, as six buttons and a container each one stands for.
 *
 * The unit is a twelve-ounce drink, which is the right unit to *store* and a
 * poor one to be asked for: nobody knows their bottle in twelfths of a gallon,
 * so every entry started with arithmetic and ended with a keyboard covering
 * half the screen. On a log whose entire claim is that it costs nothing, that
 * was the one row that cost something.
 *
 * So the numbers carry the thing they measure. The caption is what makes this
 * work — the answer to "how much did I drink" is *a bottle*, and the number is
 * the translation, which is the machine's problem rather than yours.
 *
 * The six are container sizes rounded to something a thumb can aim at:
 *
 *   0.5  half   a half glass, a short pour
 *   1    can    12oz — a can, a mug, a pint glass of water; also one standard
 *               drink, so it is what a beer, a shot and a glass of wine all are
 *   1.5  bottle 16.9oz is 1.4 and a US pint is 1.33 — both round here
 *   2    24oz   a big cup, two cans
 *   3    flask  a 32oz Nalgene is 2.67, a 40oz flask is 3.33
 *   4    jug    48oz, or the half-litres you stop counting individually
 *
 * The precision lost by rounding a Nalgene from 2.67 to 3 is precision this
 * log never had: it exists to show the shape of a day's hydration, and being
 * a third of a drink out on one entry does not move that. Being unable to
 * face logging it does.
 *
 * The box stays, underneath and quiet, because `parseAmount` reads `1 1/2` and
 * `.5` and there will be a bottle these six do not cover. It is the fourth
 * case now rather than the first.
 */
const AMOUNTS = [
  [0.5, "half"],
  [1, "can"],
  [1.5, "bottle"],
  [2, "24oz"],
  [3, "flask"],
  [4, "jug"],
];

function amountRow(step, answers, answer) {
  const { unit, preset, max } = step.field;
  const chosen = answers[step.name];

  const box = el("input.amount", {
    type: "text",
    inputmode: "decimal",
    value: chosen ?? preset,
    "aria-label": unit,
  });
  const typed = () => {
    const value = parseAmount(box.value, max);
    if (value == null) return void box.classList.add("bad");
    answer(step.name, value);
  };
  box.addEventListener("input", () => box.classList.remove("bad"));

  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: step.name }),
      el("span.q-hint", { text: `${unit} · 12oz each` }),
    ),
    el("div.opts", null,
      ...AMOUNTS.map(([n, what]) =>
        el("button.opt.amt", {
          class: chosen === n ? "on" : "",
          "aria-pressed": chosen === n,
          "aria-label": `${n} ${unit} — ${what}`,
          onclick: () => answer(step.name, n),
        },
          el("span.amt-n", { text: String(n) }),
          el("span.amt-for", { text: what }),
        )),
    ),
    // The escape hatch, and it looks like one. `.5`, `1 1/2` and `2.25` all
    // read; the hint says so only here, where it is the answer to a question
    // you are already asking.
    el("div.row", null, box, el("button.go.small", { text: "set", onclick: typed })),
  );
}

/**
 * Today's entries for one log, in hour order, each tappable to remove.
 *
 * `remove` is a descriptor rather than a lookup by tab, because the four
 * deletable logs do not agree on either half of it: three post `entry_id` and
 * a session posts `session_id`, and a body with the wrong field name is a 422
 * for a row that is sitting right there on screen. It can also be a function
 * of the row, for the eat tab's list, where a meal and a drink sit side by side
 * and are deleted down different paths. A row carrying `log` wears that log's
 * hue on its clock, which is how the two are told apart in that list.
 */
function entryList(rows, describe, empty, { at = (e) => entryClock(e.hour, e.minute), byKind, remove } = {}) {
  if (!rows.length) return el("p.empty", { text: empty });
  // `byKind` widens the left column from a five-character stamp to a word. It
  // travels with `at`, because the two are the same decision: a list that puts
  // something other than a clock in that column needs the room for it.
  return el(byKind ? "ul.entries.by-kind" : "ul.entries", null,
    ...rows.map((entry) =>
      el("li.entry", { class: entry.queued ? "queued" : "", "data-log": entry.log },
        el("span.entry-at", { text: at(entry) }),
        el("span.entry-what", { text: describe(entry) }),
        // A queued entry has no id on the far side yet, so there is nothing to
        // delete — it shows why instead. Reaching back into the queue to pull
        // one out would be a second way to remove something and a second thing
        // to get wrong; waiting the few seconds for it to land is the same
        // outcome with none of that.
        entry.queued
          ? el("span.entry-tag", { text: "queued", title: "not sent to the dashboard yet" })
          : el("button.del", {
              text: "×",
              "aria-label": "delete this entry",
              onclick: () => {
                const how = typeof remove === "function" ? remove(entry) : remove;
                send(how.path, { [how.field]: entry.id }, "delete");
              },
            }),
      ),
    ),
  );
}

/** `1`, `1.5`, `0.5` — never `1.00`, and never `0.30000000000000004`. */
const say = (amount) => String(Math.round(amount * 100) / 100);

/**
 * Meals and drinks, as the eat tab holds them: two ladders, one list.
 *
 * They were two tabs until 2026-09-23. They are logged together — 77% of the
 * meals in September had a drink within an hour of them — and the list under
 * either one was half of the answer to "what have I had today". So the tab is
 * one, with a switch at the top for which ladder is up, and the list under it
 * is both logs in time order, each row's clock in its own log's hue.
 */
const INTAKE = {
  meals: {
    label: "meal",
    rows: () => rowsFor("/api/meal", panel?.meals_today, "/api/meal/delete"),
    // The name stands in for size and quality rather than joining them: it is
    // what filled them, so printing all three spends half the row saying
    // "large, 5" under a line that already says chicken and sweet potato.
    // Flags are appended because nothing above them implies them.
    describe: (e) => [
      e.preset ? mealName(mealMenu(), e.preset) : `${e.size} · quality ${e.quality}`,
      ...(e.flags ?? []),
    ].join(" · "),
    remove: { path: "/api/meal/delete", field: "entry_id" },
  },
  drinks: {
    label: "drink",
    rows: () => rowsFor("/api/drink", panel?.drinks_today, "/api/drink/delete"),
    // "water" is written out rather than left blank, because a row reading
    // just `· 1` looks like a row that lost something. It is the one label on
    // this screen that is not a value the table holds — the absence of tags is
    // what says it, here and in `DrinkTag`.
    describe: (e) =>
      `${e.name ? drinkNameLabel(e.name) : e.tags?.length ? e.tags.join(" + ") : "water"}`
      + ` · ${say(e.amount)}`,
    remove: { path: "/api/drink/delete", field: "entry_id" },
  },
};

/** Today's meals and drinks as one list, earliest first. */
function intakeList() {
  const rows = Object.entries(INTAKE)
    .flatMap(([log, one]) => one.rows().map((entry) => ({ ...entry, log })))
    .sort(byClock);
  return entryList(rows, (e) => INTAKE[e.log].describe(e), "nothing eaten or drunk yet today", {
    remove: (e) => INTAKE[e.log].remove,
  });
}

const MEALS = quickLog({
  id: "meals",
  steps: MEAL_STEPS,
  path: "/api/meal",
  ...INTAKE.meals,
  list: intakeList,
  // The name's words travel with the meal, which is how a name typed here
  // reaches the laptop's table — see `MealRequest.label`. Sent for old names
  // too: the laptop keeps the first words it heard, so it costs nothing, and
  // a name the laptop somehow lost comes back with its label rather than as
  // its slug spelled out. Once it is in the queue, `mealMenu` finds it there,
  // so the form's own list of new names can be let go.
  enrich: (body) => {
    if (body.preset) body.label = mealItem(mealMenu(), body.preset)?.label;
    named = [];
  },
  // A meal usually comes with a drink, so the drink ladder is what is up
  // next. Leaving without one costs nothing — nothing is half-filled.
  logged: () => {
    intake = "drinks";
    offered = true;
  },
});

const DRINKS = quickLog({
  id: "drinks",
  steps: DRINK_STEPS,
  path: "/api/drink",
  ...INTAKE.drinks,
  list: intakeList,
  logged: () => {
    offered = false;
  },
});

const BATHROOM = quickLog({
  id: "bathroom",
  label: "visit",
  steps: BATHROOM_STEPS,
  path: "/api/bathroom",
  empty: "nothing logged yet today",
  rows: () => rowsFor("/api/bathroom", panel?.bathroom_today, "/api/bathroom/delete"),
  describe: (e) => `${e.kind} · ${e.solidity ?? e.color} · ${e.volume}`,
  remove: { path: "/api/bathroom/delete", field: "entry_id" },
});

const CHECKIN = checkinLog();
const SESSION = sessionLog();

/**
 * The eat tab: which ladder, then that ladder, then both logs' list.
 *
 * The switch is two caps, each in its own log's hue lit or not — the rule
 * the bar follows, one level down — and the whole screen wears the hue of
 * the one that is up, so turning it is visibly turning the screen.
 */
function eatScreen() {
  const log = intake === "drinks" ? DRINKS : MEALS;
  return [
    el("div.intake", { role: "group", "aria-label": "meal or drink" },
      ...Object.entries(INTAKE).map(([id, one]) =>
        el("button.intake-cap", {
          "data-log": id,
          class: intake === id ? "on" : "",
          "aria-pressed": intake === id,
          text: one.label,
          onclick: () => {
            if (intake === id) return;
            intake = id;
            offered = false;
            count(`eat.${id}`);
            draw();
          },
        })),
    ),
    offered && intake === "drinks"
      ? el("p.offer", { text: "meal logged — anything to drink with it?" })
      : null,
    ...log.render(),
  ];
}

/**
 * The morning check-in.
 *
 * Five questions on 1–5 plus the morning weight, and it is the one log with a
 * draft: the request model wants all five at once, because a `score` summed
 * over three of them is a number that means nothing.
 *
 * Every question counts *up* toward the good answer on screen — 5 is always the
 * one you want — and the three the table stores the other way round are flipped
 * through `toStored`/`toShown`, imported from `logs.js`. Getting that wrong is
 * the worst bug available on this screen: it does not throw, it quietly files
 * the good mornings as the bad ones.
 */
function checkinLog() {
  return {
    id: "checkin",
    label: "check-in",
    render() {
      const held = draft();
      const saved = panel?.readiness_today;
      const shownFor = (key) => {
        const stored = held[key] ?? saved?.[key];
        return stored ? toShown(key, stored) : null;
      };

      // Tapping an answer only ever records it locally. It used to post as soon
      // as the fifth was filled, which meant the morning was written by a tap
      // that looked exactly like the four before it — no confirmation, nothing
      // on screen that said the row had gone, and no way to change your mind
      // about question three without writing again.
      const answer = (key, shown) => {
        setDraft({ ...held, [key]: toStored(key, shown) });
        settle("checkin", key);
        draw();
      };

      // The night, in the order that has the better claim: what you have just
      // turned the wheels to, then what the morning already saved, then this
      // phone's defaults. Which of the three answered is kept, because a
      // default and an answer look identical once they are in the box.
      const defaults = nightDefaults();
      const pick = (key) =>
        held[key] != null ? [held[key], "draft"]
          : saved?.[key] != null ? [saved[key], "saved"]
            : defaults ? [defaults[key], "default"]
              : [null, null];
      const [bed, bedFrom] = pick("bed");
      const [woke, wokeFrom] = pick("woke");
      const span = bed && woke ? spanHours(bed, woke) : null;
      const nightOk = span != null && span > 0 && span <= MAX_IN_BED;

      const answered = CHECKIN_ITEMS.filter(([k]) => (held[k] ?? saved?.[k]) != null);
      const ready = answered.length === CHECKIN_ITEMS.length && nightOk;
      // Whether anything on screen differs from the row in the database. This
      // is the question the panel was silent about: with a morning already
      // logged, every answer is *shown* as selected whether it came from the
      // table or from a tap ten seconds ago, and the two are not the same thing.
      const dirty = CHECKIN_ITEMS.some(([k]) => held[k] != null && held[k] !== saved?.[k])
        || (nightOk && (bed !== (saved?.bed ?? null) || woke !== (saved?.woke ?? null)));

      const save = () => {
        if (!ready || !dirty) return;
        const body = {};
        for (const [k] of CHECKIN_ITEMS) body[k] = held[k] ?? saved[k];
        body.bed = bed;
        body.woke = woke;
        setDraft({});
        resetLadder("checkin");
        // Done for the day, so off the bar — and off the screen with it,
        // onto the tab the rest of the day is spent on. The date in the
        // header is the way back for a correction, and the toast says so
        // once rather than a caption saying it every morning.
        const first = !checkedIn();
        if (first) tab = "eat";
        const entry = send("/api/readiness", body, "check-in");
        if (entry && first) toast("check-in saved — tap the date to change it");
      };

      const status = !ready
        ? answered.length < CHECKIN_ITEMS.length
          ? `${CHECKIN_ITEMS.length - answered.length + (nightOk ? 0 : 1)} still to answer`
          : "when were you in bed?"
        : dirty
          ? "not saved yet"
          : "saved";

      const weight = measured("weight").value;

      const ladder = [
        // Weight and nothing else. The hours-slept box was here beside it and
        // is gone: the watch measures that better at 6am than anyone types it,
        // and a box you fill in from a guess produces a series indistinguishable
        // from a measured one. Weight stays because nothing weighs him but a
        // scale — there is no watch reading to defer to.
        //
        // `sleep_quality` is still asked below, and that is not a duplicate:
        // one is how long the night was and the other is what it was like, and
        // the disagreement between them is the diagnostic.
        //
        // Skippable, because the check-in does not wait on it — a morning
        // away from the scale must not hold one of the three open slots all
        // day. `keep ›` once there is a reading, so closing a reopened row is
        // not a second write of the same number.
        {
          name: "weight",
          label: "weight",
          complete: weight != null,
          full: () => measure("weight", "lb", "weight", { after: () => settle("checkin", "weight") }),
          mini: () => `${weight} lb`,
          skip: weight != null ? "keep ›" : "skip ›",
        },
        // Done by itself only when nothing on it was touched this morning — a
        // saved night, or both wheels from this phone's defaults, which is the
        // ordinary morning and should cost nothing. Turned by hand, it waits
        // for `done ›`: the wheels are two answers, and closing on the first
        // would shut the row under the second.
        {
          name: "night",
          label: "in bed",
          complete: nightOk && bedFrom !== "draft" && wokeFrom !== "draft",
          full: () => night({
            bed,
            woke,
            span,
            nightOk,
            defaults,
            fromDefaults: bedFrom === "default" && wokeFrom === "default",
          }),
          mini: () => nightOk ? `${clockTime(bed)} – ${clockTime(woke)} · ${hoursLabel(span)}` : "not set",
          closer: { text: "done ›", disabled: !nightOk },
        },
        ...CHECKIN_ITEMS.map(([key, name, low, high]) => ({
          name: key,
          label: name,
          complete: shownFor(key) != null,
          full: () => scale(key, name, low, high),
          mini: () => String(shownFor(key)),
          meter: shownFor(key) ? [shownFor(key), 5] : null,
        })),
      ];

      function scale(key, name, low, high) {
        const shown = shownFor(key);
        return el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: name }),
            el("span.q-hint", { text: `1 ${low} · 5 ${high}` }),
          ),
          // Through `optsClass` like every other row in the app, rather
          // than naming a grid here. These are five one-character labels, so
          // it answers "five across" — and it answers it for the meal log's
          // quality row and the session log's RPE by the same measurement,
          // which is the point of asking rather than telling.
          el(optsClass([1, 2, 3, 4, 5]), null,
            ...[1, 2, 3, 4, 5].map((n) =>
              el("button.opt.num", {
                class: shown === n ? "on" : "",
                "aria-pressed": shown === n,
                text: String(n),
                onclick: () => answer(key, n),
              })),
          ),
        );
      }

      return [
        ...ladderRows("checkin", ladder),
        // The whole row goes at once — the request model wants all five,
        // because a `score` summed over three of them is a number that means
        // nothing. So this is one button rather than five, and it says which of
        // the three states the morning is in.
        el("button.go.commit", {
          class: !ready || !dirty ? "quiet" : "",
          text: ready ? (dirty ? "save check-in" : "saved") : status,
          disabled: !ready || !dirty,
          onclick: save,
        }),
      ];
    },
  };
}

/**
 * Into bed and out of it — the night as you lived it, not as the watch scored it.
 *
 * The watch knows when you fell asleep and when you woke; it cannot know when
 * you lay down, so the half hour spent waiting for sleep is in neither of its
 * numbers. This is that half hour's only record, and it is asked here because
 * it is answered getting out of bed, like the rest of the morning.
 *
 * Two wheels and a line. The line says what this phone's defaults are and
 * offers to make what is on screen the new ones — the one control on the
 * check-in that writes nothing to the laptop.
 */
function night({ bed, woke, span, nightOk, defaults, fromDefaults }) {
  const wheel = (key, value, label) => {
    const box = el("input.amount", {
      type: "time",
      value: value ?? "",
      "aria-label": label,
      class: bed && woke && !nightOk ? "bad" : "",
    });
    // Stored on `input`, drawn on `change`: a redraw rebuilds the element, and
    // doing it while the wheel is turning closes the wheel under the thumb.
    box.addEventListener("input", () => setDraft({ ...draft(), [key]: box.value || null }));
    box.addEventListener("change", () => draw());
    return box;
  };

  const same = Boolean(defaults && defaults.bed === bed && defaults.woke === woke);
  const hint = !bed || !woke
    ? "into bed · up"
    : !nightOk
      ? "that is not a night — check am and pm"
      : fromDefaults
        ? `${hoursLabel(span)} · from your defaults`
        : `${hoursLabel(span)} in bed`;

  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: "in bed · up" }),
      el("span.q-hint", { text: hint }),
    ),
    el("div.row", null, wheel("bed", bed, "into bed"), wheel("woke", woke, "up")),
    el("div.row.night-foot", null,
      el("span.q-hint", {
        text: defaults
          ? `defaults ${clockTime(defaults.bed)} · ${clockTime(defaults.woke)}`
          : "no defaults on this phone",
      }),
      el("button.go.small", {
        text: same ? "default" : "make default",
        disabled: !nightOk || same,
        onclick: () => {
          setNightDefaults({ bed, woke });
          // Pinned into the draft as well, so the check-in keeps what is on
          // screen even if the defaults move again before it is saved.
          setDraft({ ...draft(), bed, woke });
          toast(`defaults ${clockTime(bed)} · ${clockTime(woke)}`);
          draw();
        },
      }),
    ),
  );
}

/**
 * The one scalar the check-in takes: the morning weight.
 *
 * There were two. Hours slept was beside it and came out when the watch
 * started reporting them — a box you fill in from a guess produces a series
 * indistinguishable from a measured one, and at 6am the guess is what you get.
 * Weight has no such alternative: nothing weighs him but a scale.
 *
 * Still parameterised, because the shape is right and the next hand-entered
 * scalar (the vertical jump, on testing days) will want exactly this and
 * nothing else — a number box rather than buttons, because there is no set of
 * five answers to "what do you weigh". `decimal` rather than `number` on the
 * input: a `type=number` on iOS is a text keyboard with a spinner nobody can
 * hit, and `inputmode` gets the keypad without the spinner.
 */
function measured(metric) {
  const series = panel?.series?.find((s) => s.metric === metric);
  const last = series?.points?.[series.points.length - 1];
  const logged = last && last[0] === today() ? last[1] : null;
  const queued = pendingOn().find((e) => e.path === "/api/health" && e.body.metric === metric);
  return { logged, queued, value: queued?.body.value ?? logged ?? null };
}

/** `after` runs before the write's own draw, which is how the check-in's
 *  ladder closes the row in the same frame the reading lands. */
function measure(metric, unit, name, { after } = {}) {
  const { logged, queued } = measured(metric);

  const box = el("input.amount", {
    type: "text",
    inputmode: "decimal",
    value: queued?.body.value ?? logged ?? "",
    placeholder: unit,
    "aria-label": name,
  });
  const commit = () => {
    const value = Number(String(box.value).trim());
    if (!Number.isFinite(value) || value <= 0) return void box.classList.add("bad");
    after?.();
    send("/api/health", { metric, value }, name);
  };
  box.addEventListener("input", () => box.classList.remove("bad"));

  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: name }),
      logged != null && !queued ? el("span.q-hint", { text: "logged today" }) : null,
      queued ? el("span.q-hint.queued", { text: "queued" }) : null,
    ),
    el("div.row", null, box, el("button.go", { text: "save", onclick: commit })),
  );
}

/**
 * What to call a scalar a block asks you to measure, and what unit it is in.
 *
 * One entry, and a table anyway. The alternative is `measure("vert_jump",
 * "in", "vertical jump")` written into the session card, which makes adding a
 * second test — a broad jump, a bench pin press, whatever February wants — an
 * edit to the phone rather than a line in `training.toml`. The whole point of
 * `PrescribedBlock.tests` naming a `HealthMetric` is that the plan gets to
 * decide; hardcoding the one metric here would hand that back.
 */
const TESTED = {
  vert_jump: { unit: "in", name: "vertical jump" },
};

/**
 * The boxes for whatever today is supposed to produce a reading of.
 *
 * Off the *whole* day's blocks rather than the open ones, because the reading
 * and the session are two separate writes and the wrong order must not lose
 * one. You log the lift, the block closes and leaves `openBlocks`, and the
 * number you were about to type would have had its box disappear out from
 * under it — on the one screen whose entire job is to be usable while standing
 * up holding a phone.
 *
 * Empty on every other day, which is the point and is `HealthMetric.vert_jump`'s
 * own standing instruction: it is not on the daily form because an empty box
 * every morning reads as a log you failed to keep, and a Tuesday is not a day
 * you forgot to jump on.
 */
function tests() {
  const metrics = [...new Set((panel?.plan?.today ?? []).map((b) => b.tests).filter(Boolean))];
  return metrics
    .filter((metric) => TESTED[metric])
    .map((metric) => measure(metric, TESTED[metric].unit, TESTED[metric].name));
}

/**
 * The plan's own notation for a session that answered it — `LFC`, `A3`.
 *
 * Out of the key rather than looked up in the plan, so it reads the same for a
 * row still sitting in the queue as for one the laptop has taken. Empty for a
 * freehand session, which answered nothing.
 */
const planToken = (entry) => String(entry.plan_key ?? "").split(":")[1] ?? "";

/**
 * A training session: what, how hard, how long.
 *
 * --- answering the plan -----------------------------------------------------
 *
 * The first question is which of today's prescribed blocks this was, and it is
 * the whole reason this card was changed. Without it every session logged here
 * goes up with no `plan_key` — a load the day counts, and a block the plan
 * goes on treating as owed, because a block is only closed by a session
 * carrying its key and matching them up by kind afterwards would be the load
 * model guessing.
 *
 * That matters more than it did. The health tab's load score now reads the
 * week's prescribed blocks against the ones answered, so a session logged as
 * `something else` is not merely untidy — it is a week's training that the
 * panel cannot see was the training the plan asked for.
 *
 * A block per button rather than one card per outing, which is how the laptop
 * used to ask: there the rows of an outing were one form with one submit,
 * because the desktop was filling in an evening's training at a keyboard. Here
 * you are standing in a doorway having just done one of them, and the parts of
 * a practice are logged as you finish them — so each is its own entry, and the
 * week's compliance figure ticks up a block at a time.
 *
 * `something else` is the way back to what this card always was: a pickup
 * session, a walk, a day the schedule got abandoned. That form is unchanged and
 * is still what you get on a day the plan asks for nothing.
 */
function sessionLog() {
  return {
    id: "session",
    label: "session",
    render() {
      const open = openBlocks(panel?.plan, sessionRows());
      // A block answered while this form sat half-filled — by a flush landing
      // rows logged earlier, or by this app open on a second device — loses its
      // button, and a pick with no button behind it would post a key the unique
      // index refuses and the queue then drops as a 4xx. Dropping the pick
      // instead costs one tap and cannot lose the entry.
      if (session.pick && session.pick !== FREEHAND
          && !open.some((b) => b.key === session.pick)) {
        session = { ...session, pick: null };
      }
      const block = open.find((b) => b.key === session.pick) ?? null;

      // What is left to ask about *what this was*, in three cases.
      //
      //   A block that names a focus has answered all of it. `LFC` is
      //   `strength.vjp.lower_french_contrast` and there is nothing to say, so
      //   the rows disappear entirely rather than appearing pre-answered —
      //   the same rule that already removed the kind row for a block that
      //   named its kind, and for the same reason: three questions with one
      //   legal answer each are three taps that mean nothing. The hint line
      //   above prints what it is, and `something else` is still the way out.
      //
      //   `library.A` names a shortlist instead, because that is the one token
      //   whose whole point is that what you did is a real question —
      //   shadowboxing and a run are different training and the difference is
      //   only recoverable now. Its three kinds prune the tree to cardio and
      //   boxing, which is that question asked in the tree's own words.
      //
      //   Off-plan gets the whole thing.
      const allowed = block?.choose?.length ? block.choose : block?.kind ? [block.kind] : null;
      const tree = prunedTree(allowed, SESSION_TREE);
      // A level with one option is not a question — see `forcedPath`. Filtered
      // here as well as seeded at `choose`, because the seed only runs when a
      // block is picked and this also has to hold for the row a stale path
      // leaves behind.
      const rows = block?.focus ? [] : focusLevels(session.path, tree).filter(
        (row) => row.options.length > 1,
      );
      const kind = sessionKind(session.path, tree) ?? block?.kind ?? null;

      const minutes = el("input.amount", {
        type: "text",
        inputmode: "numeric",
        value: session.minutes,
        placeholder: "minutes",
        "aria-label": "minutes",
      });
      minutes.addEventListener("input", () => {
        session.minutes = minutes.value;
        minutes.classList.remove("bad");
      });

      // A native time wheel rather than a typed box: `type=time` on iOS is the
      // system picker, and its value is `HH:MM` whatever the phone's 12/24h
      // setting — exactly the form the server takes. Held on `input` without
      // a redraw, because a redraw rebuilds this element and closes the wheel
      // under the thumb that is turning it.
      const started = el("input.amount", {
        type: "time",
        value: session.started,
        "aria-label": "started",
      });
      started.addEventListener("input", () => {
        session.started = started.value;
        started.classList.remove("bad");
      });

      // The column has been on the table since the first migration and has
      // never had a box: the laptop's card could write it and the laptop's
      // card is gone, so `note` has zero rows across every session ever
      // logged. It is here and not on a screen of its own because the thing
      // worth writing down is the thing that made *this* session unusual, and
      // that is a sentence you have thirty seconds of wanting to write.
      //
      // No `bad` class and no validation. The server caps it at 200 and the
      // commit slices to the same number, so there is no length a person can
      // type that this has to refuse — and a free-text box that rejects what
      // you wrote is a box you stop using.
      const note = el("input.amount", {
        type: "text",
        value: session.note,
        placeholder: "anything worth remembering",
        "aria-label": "note",
        enterkeyhint: "done",
        autocapitalize: "sentences",
        autocomplete: "off",
      });
      note.addEventListener("input", () => { session.note = note.value; });

      const commit = () => {
        const mins = Number(String(session.minutes).trim());
        // `kind` rather than the path, because the path is allowed to stop
        // early everywhere except under cardio — which carries no kind of its
        // own, so "cardio" alone is the one selection that looks answered and
        // is not. This is the only place that difference is visible.
        if (!kind || !session.rpe) return void toast("pick a kind and an RPE");
        if (!HHMM.test(session.started)) {
          started.classList.add("bad");
          return void toast("when did it start?");
        }
        if (!Number.isInteger(mins) || mins < 1 || mins > 600) {
          return void minutes.classList.add("bad");
        }
        // Dated by the start, not the clock: a session begun at 11:15pm and
        // logged after midnight is yesterday's. See `dateForTime`.
        const body = {
          date: dateForTime(session.started),
          kind,
          rpe: session.rpe,
          started: session.started,
          minutes: mins,
        };
        // Both omitted when unanswered rather than sent as null, so the body
        // this card posts on a day you say nothing extra is byte-for-byte the
        // body it has always posted. An empty note is not a note.
        if (session.quality) body.quality = session.quality;
        const written = session.note.trim().slice(0, 200);
        if (written) body.note = written;
        // Only where the walk reached a leaf. A path that stopped short is a
        // kind with no focus, and posting the half of it that was answered
        // would invent a category meaning "they stopped here" — see
        // `sessionFocus`, and `SessionRequest`, which refuses one.
        const focus = sessionFocus(session.path, tree);
        if (focus) body.focus = focus;
        // The one line the laptop's plan card is waiting for.
        if (block) body.plan_key = block.key;
        session = { pick: null, path: [], rpe: null, started: "", minutes: "", quality: null, note: "" };
        send("/api/session", body, "session");
      };

      /** Answer "what did you do" with one of today's prescribed blocks.
       *
       *  The block prefills the whole path where the plan knows it — `library.LFC`
       *  is `strength.vjp.lower_french_contrast` and there is nothing left to
       *  ask, so all three rows disappear rather than being three taps that
       *  can only be answered one way. Where it does not (`library.A`, the one
       *  token whose whole point is that what you did is a real question) the
       *  path starts empty and the pruned tree asks it.
       *
       *  Cleared either way, because a path walked freehand a moment ago may
       *  not exist at all in the tree this block leaves standing. */
      const choose = (chosen) => {
        const branch = prunedTree(
          chosen.choose?.length ? chosen.choose : chosen.kind ? [chosen.kind] : null,
          SESSION_TREE,
        );
        session = {
          ...session,
          pick: chosen.key,
          path: chosen.focus?.split(".") ?? forcedPath(branch),
        };
        draw();
      };

      return [
        // Above the plan row, because on a testing day it is the thing you
        // have just done and are holding a number for. It is also the only
        // control on this card that does not post a session: it writes a
        // health row on its own save button, so it neither waits for the form
        // below it nor is lost by it.
        ...tests(),
        // Absent on a rest day, on the eleven unwritten weeks, and once every
        // block has been logged — all three are days on which there is nothing
        // to answer, and a row of no buttons above the form would be the card
        // asking a question with no answers in it.
        open.length
          ? el("div.q", null,
              el("div.q-head", null,
                el("span.q-name", { text: "on the plan" }),
                // The block's own words, once one is picked: what it actually
                // is, what it will be logged as, and how long it asked for.
                // `asks` is printed and never pre-filled, exactly as on the
                // laptop — load is rpe x minutes and both halves are things you
                // measure rather than things the plan decides.
                //
                // Nothing before one is picked. It read "what today asked for",
                // which is a caption restating its own heading: the row is
                // called `on the plan` and its buttons are the blocks, so the
                // sentence was a line of type that could only be read and
                // never acted on.
                block
                  ? el("span.q-hint", {
                      text: [block.name, block.kind, block.asks].filter(Boolean).join(" · "),
                    })
                  : null,
              ),
              el(optsClass([...open.map((b) => b.label), "something else"]), null,
                ...open.map((b) =>
                  el("button.opt", {
                    class: session.pick === b.key ? "on" : "",
                    "aria-pressed": session.pick === b.key,
                    text: b.label,
                    onclick: () => choose(b),
                  })),
                el("button.opt", {
                  class: session.pick === FREEHAND ? "on" : "",
                  "aria-pressed": session.pick === FREEHAND,
                  text: "something else",
                  onclick: () => { session = { ...session, pick: FREEHAND }; draw(); },
                }),
              ),
            )
          : null,
        // The tree, one row per level, each row the children of the answer
        // above it. Answering grows the form by a row and un-answering shrinks
        // it, so the depth on screen is the depth you have actually reached.
        //
        // Gone entirely when the plan has answered it — a block that names its
        // kind and its focus prunes to one path with no choices in it, and a
        // row of one button you have to press anyway is a question pretending
        // to be asked.
        //
        // There is no `other` on any row and that is the design: stopping is
        // what "other" would have meant, it costs no tap, and a button saying
        // the same thing as not pressing a button is one you have to think
        // about every single time.
        ...rows.map((row, depth) =>
          el("div.q", null,
            // No hint. It used to say "cardio is two kinds — say which" on the
            // first row until a kind was reached, which is a sentence about
            // one of six buttons printed above all six of them — and the row
            // it is about answers the question by simply appearing underneath
            // the moment cardio is tapped. The commit button already refuses
            // an unfinished path, which is the same fact said at the moment it
            // applies rather than the whole time it does not.
            el("div.q-head", null,
              el("span.q-name", { text: row.depth ? "which" : "what" }),
            ),
            el(optsClass(row.options.map((node) => node.label)), null,
              ...row.options.map((node) =>
                el("button.opt", {
                  class: row.chosen === node.value ? "on" : "",
                  "aria-pressed": row.chosen === node.value,
                  text: node.label,
                  onclick: () => {
                    // The path is truncated to this level before the tap is
                    // appended, so re-answering the first row drops the rows
                    // under it rather than leaving a stale leaf beneath a new
                    // branch. Tapping the answer already on takes it off,
                    // which is how you go back up a level.
                    const head = session.path.slice(0, row.depth);
                    session.path = row.chosen === node.value ? head : [...head, node.value];
                    draw();
                  },
                })),
            ),
          ),
        ),
        el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: "rpe" }),
            // The anchor for whatever is selected. The ten of them at once is
            // a wall of words; the one you have chosen is the thing that stops
            // RPE 7 drifting into RPE 5 over a long camp.
            //
            // Nothing before one is. The placeholder was "1 nothing · 10
            // everything", which is what a row of caps numbered 1 to 10
            // already says, and it was on screen for the whole of every
            // session that had not reached this row yet.
            session.rpe ? el("span.q-hint", { text: RPE_ANCHOR[session.rpe] }) : null,
          ),
          el(optsClass(Array.from({ length: 10 }, (_, i) => i + 1)), null,
            ...Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
              el("button.opt.num", {
                class: session.rpe === n ? "on" : "",
                "aria-pressed": session.rpe === n,
                text: String(n),
                onclick: () => { session.rpe = n; draw(); },
              })),
          ),
        ),
        // How well it went, under how hard it was, and the order is the
        // argument. Asked before RPE it would be answered with RPE — "how was
        // it" straight after a hard session means "it was hard" — and the
        // effort is already written down one row up, better. Underneath, with
        // the effort just recorded, the question that is left is the one this
        // row is for: did the body do what it was told.
        //
        // Optional, and it says so out loud rather than growing a `skip`
        // button. Tapping the answer already on takes it off, which is the
        // same gesture the tree rows use to go back up a level.
        el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: "how it went" }),
            el("span.q-hint", {
              text: session.quality ? QUALITY_ANCHOR[session.quality] : "optional",
            }),
          ),
          el(optsClass([1, 2, 3, 4, 5]), null,
            ...[1, 2, 3, 4, 5].map((n) =>
              el("button.opt.num", {
                class: session.quality === n ? "on" : "",
                "aria-pressed": session.quality === n,
                text: String(n),
                onclick: () => { session.quality = session.quality === n ? null : n; draw(); },
              })),
          ),
        ),
        el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: "note" }),
            el("span.q-hint", { text: "optional" }),
          ),
          el("div.row", null, note),
        ),
        // Start and length side by side, because they are one fact — the
        // stretch of the clock this session occupied — and both count from the
        // same moment: the end of the warm-up.
        el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: "started · minutes" }),
            el("span.q-hint", { text: "after the warm-up" }),
          ),
          el("div.row", null, started, minutes, el("button.go", { text: "log", onclick: commit })),
        ),
        entryList(
          sessionRows(),
          // The token, where there is one. It is the receipt for the thing this
          // card was changed to do: it says the block was answered and not
          // merely that some training happened, which is the difference the
          // laptop's plan card turns on and the one you cannot otherwise see
          // from here.
          // The note is truncated and the full one is not shown anywhere here,
          // which is deliberate: this line is a receipt saying the thing you
          // typed went up, not the place the note is read. It is read in
          // February, on a laptop, next to a series.
          (e) => [
            e.started ? clockTime(e.started) : null,
            focusLabel(e.focus),
            `rpe ${e.rpe} · ${e.minutes}m`,
            e.quality ? QUALITY_ANCHOR[e.quality] : null,
            planToken(e),
            e.note ? (e.note.length > 60 ? `“${e.note.slice(0, 59)}…”` : `“${e.note}”`) : null,
          ]
            .filter(Boolean)
            .join(" · "),
          "nothing trained yet today",
          {
            // A session has no hour — the table records the day and the load,
            // not the clock — so the left column says which session it was
            // rather than printing every one of them as midnight.
            at: (e) => e.kind,
            byKind: true,
            remove: { path: "/api/session/delete", field: "session_id" },
          },
        ),
      ];
    },
  };
}

// --- the shell --------------------------------------------------------------------

// --- the day ------------------------------------------------------------------
//
// The app's other half, and the only part of it that is mostly for reading.
//
// The five logs answer "what have I already put down?"; these three answer
// "what is still owed?", from three directions that do not overlap:
//
//   todo    the list — overdue, due, and the ones with no day on them at all.
//           The one screen here you can write to, and both writes go through
//           the same queue everything else does.
//   owed    what *today* asks for. `dayTasks` off `day.state`, which is the
//           desktop's own list rather than a second opinion about it.
//   agenda  the calendar. Today's events, and tomorrow's once the day has
//           turned over and the laptop has not been reachable since.
//
// All three draw from one cached `DayPanel` and one request. See the block
// at `stillTrue` in `held.js` for what survives a rollover and what does not.

/**
 * A tap that a scroll cannot become.
 *
 * The todo rows are the first controls in this app where a mis-hit costs
 * something you have to go and undo, and they are also the first ones in a list
 * long enough to need scrolling — which is the combination that produces the
 * complaint. A finger that starts on a row and drags to scroll fires `click` on
 * that row in Safari more often than it should, and a "sloppy tap" that moves
 * eight pixels fires it everywhere.
 *
 * So the row's own `click` is gated on the gesture having been a tap: a pointer
 * that moved more than `SLOP` or was cancelled by the scroller taking the
 * gesture over does not count. `pointercancel` is the important half — on a
 * touchscreen the browser hands the gesture to the scroller and stops sending
 * moves, so distance alone would not notice.
 *
 * `click` and not `pointerup`, deliberately: `click` is what a keyboard and a
 * screen reader produce, and neither of those sends a pointer event at all —
 * `down` stays null for them and the guard passes.
 */
const SLOP = 10;

function safeTap(onTap) {
  let down = null;
  let dragged = false;
  return {
    onpointerdown: (e) => { down = [e.clientX, e.clientY]; dragged = false; },
    onpointermove: (e) => {
      if (!down) return;
      if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > SLOP) dragged = true;
    },
    onpointercancel: () => { dragged = true; },
    onclick: (e) => {
      const missed = dragged;
      down = null;
      dragged = false;
      if (!missed) onTap(e);
    },
  };
}

/** Ticks the queue is still carrying, as `{ [todo_id]: done }`. */
const queuedTicks = () =>
  Object.fromEntries(
    pending()
      .filter((e) => e.path === "/api/todo/done")
      .map((e) => [e.body.todo_id, e.body.done]),
  );

/** Lines typed into the box and not yet sent, as rows the list can draw.
 *
 *  Given negative ids so nothing can collide with a real `todo.id`, and so the
 *  tick handler can refuse them: an item the laptop has never heard of cannot
 *  be marked done, and offering the gesture would queue a write against an id
 *  that does not exist. */
const queuedAdds = () =>
  pending()
    .filter((e) => e.path === "/api/calendar/add")
    .map((e, i) => ({
      id: -(i + 1),
      title: String(e.body.text ?? "").replace(/^todo\s+/i, ""),
      subject: null,
      due_date: null,
      start: null,
      done: false,
      done_at: null,
      event_deleted: true,
      queued: true,
    }));

/**
 * The list as it will be once the queue has drained.
 *
 * The same promise the five logs make: what is on screen is what the day will
 * look like when the laptop next hears about it, so a tick greys its row at
 * once rather than at the next successful send. A tap that appears to have
 * missed gets tapped again, and on a list of things you are crossing off that
 * is how one reading gets ticked twice.
 */
function todoRows() {
  const ticks = queuedTicks();
  const rows = (day?.todo?.items ?? []).map((item) =>
    item.id in ticks
      ? { ...item, done: ticks[item.id], done_at: ticks[item.id] ? new Date().toISOString() : null,
          queued: true }
      : item,
  );
  return [...queuedAdds(), ...rows];
}

/** Tick, or untick. Everything the row needs to know about writing. */
function tickTodo(item) {
  if (item.id < 0) {
    toast("that one has not reached the laptop yet — send it first");
    return;
  }
  sendDay("/api/todo/done", { todo_id: item.id, done: !item.done }, "tick");
  // Named rather than merely counted, because the whole risk on this screen is
  // ticking something you did not mean to and not noticing which. Saying it
  // back is most of the undo.
  toast(item.done
    ? `“${shorten(item.title, item.subject)}” put back`
    : `“${shorten(item.title, item.subject)}” ticked — it is under “done”`);
}

/**
 * One row of the list. A button, so the keyboard and a screen reader get it
 * for free, with the tap guard over the top.
 *
 * --- the subject, as colour -------------------------------------------------
 *
 * `hues` is the laptop's own subject colouring, off the same `hueMap` the week
 * grid gives its calendars — one scheme across both devices and both screens,
 * rather than three.
 *
 * It replaces a rail drawn in the tab's hue, which was orchid on every row
 * because orchid is what `todo` means in the bar. That was a defensible rule
 * and the wrong one here: every other screen in this app asks one question, so
 * painting the whole of it one colour says which question you are answering.
 * A todo list is the exception — it is six classes and a chore, and the thing
 * you need to see from the doorway is *which* of them the next four rows are.
 * A list where every row is the same colour is a list you have to read; a
 * list where 380 is blue and Homework is green is one you skim.
 *
 * The alias key beside the title takes the same hue, so the rail and the three
 * letters you actually typed say the same thing twice — which is what makes
 * the colour learnable rather than decorative.
 */
function todoRow(item, shortKey, hues) {
  const hue = item.subject ? hues.get(item.subject) : null;
  return el("button.todo", {
    class: `${item.done ? "done" : ""}${item.queued ? " queued" : ""}`
      + `${hue == null ? "" : " has-subject"}`,
    style: hue == null ? null : `--hue:${hue}`,
    // The stored title, which is the one on the Google event, so what the row
    // is named after survives having its subject taken off the front.
    "aria-label": `${item.title} — ${item.done ? "mark as still to do" : "mark done"}`,
    ...safeTap(() => tickTodo(item)),
  },
    el("span.todo-box", { "aria-hidden": true, text: item.done ? "×" : "" }),
    el("span.todo-title", { text: shorten(item.title, item.subject) }),
    item.subject ? el("span.todo-key", { text: shortKey(item.subject) }) : null,
    item.start ? el("span.todo-at", { text: dueClock(item.start) }) : null,
    // Its Google event is still standing. Only ever shown after a delete that
    // *failed* — an undated item never had an event, so it can never say this,
    // and neither can a row ticked on this phone a moment ago: the laptop has
    // not been asked to take the event down yet, so "still on the calendar" is
    // true and means nothing. Saying it there would put a warning on every
    // tick made away from wifi, which is most of them.
    item.done && !item.event_deleted && !item.queued
      ? el("span.todo-stuck", { text: "on cal" })
      : null,
  );
}

/**
 * The box, and the one thing it writes.
 *
 * Every line is prefixed `todo `, which is the desktop box's own grammar for
 * "this is work, and it is not on a day". That is the whole feature: the phone
 * has no calendar picker, no preview and no date parsing on screen, and a line
 * typed here can therefore only ever produce the one kind of row that needs
 * none of them. Anything with a day in it is a thing you are better off adding
 * from the week grid, where you can see what it collides with.
 *
 * A `todo ` already typed at the front is dropped rather than doubled, because
 * knowing the desktop's grammar should not be punished.
 *
 * Note that `calparse` still *reads* a day out of the line and then discards
 * it, since `todo` forces undated — "laundry friday" is stored as "Laundry".
 * The placeholder says undated for that reason.
 */
function addBox() {
  const field = el("input.amount", {
    type: "text",
    placeholder: "something to do",
    enterkeyhint: "done",
    autocapitalize: "sentences",
    autocomplete: "off",
  });
  const commit = () => {
    const text = field.value.trim().replace(/^todo\s+/i, "");
    if (!text) return;
    field.value = "";
    // Blurred first: the row it adds appears below the keyboard otherwise, and
    // on a list you are adding several things to that reads as nothing having
    // happened.
    field.blur();
    sendDay("/api/calendar/add", { text: `todo ${text}` }, "todo");
  };
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
  });
  // `.addbox` is the gap under it and nothing else. The box is a control and
  // the heading below it is the top of the list — two different kinds of
  // thing, an 8px `.row` margin apart, which read as one block with a line
  // through it.
  return el("div.row.addbox", null, field, el("button.go.small", { text: "add", onclick: commit }));
}

/** The todo list. */
function todoScreen() {
  if (!day) return [notRead("the list")];
  const rows = todoRows();
  const shortKey = keyFor(day.todo.aliases ?? []);
  // Over every subject in the list rather than over the aliases in config, so
  // an item whose class has since been removed from `[todo.subjects]` still
  // gets a colour instead of falling to the default. Same rule the laptop's
  // panel follows, for the same reason.
  const hues = hueMap([...new Set(rows.map((i) => i.subject).filter(Boolean))]);
  const row = (item) => todoRow(item, shortKey, hues);
  // Today's date and not the panel's, so a list cached yesterday re-groups
  // against the day you are standing in: what was "today" becomes "overdue"
  // on its own, which is the honest reading and the one that keeps a late
  // reading visible.
  const groups = groupByDay(rows, today());
  const undated = undatedItems(rows);
  const finished = rows.filter((i) => i.done);

  return [
    addBox(),
    ...groups.map((group) =>
      section(
        group.overdue ? `overdue · ${group.items.length}` : dueLabel(group.date, today()),
        group.items.map(row),
        group.overdue ? "late" : "",
      )),
    undated.length ? section(`no date · ${undated.length}`,
      undated.map(row)) : null,
    !groups.length && !undated.length
      ? el("p.empty", { text: "nothing owed — add a line above" })
      : null,
    // Collapsed, and it is the undo. A tick is one tap and this is where the
    // row it moved went, so "I did not mean that" is two taps rather than a
    // trip to the laptop.
    finished.length
      ? el("details.done-box", null,
          el("summary", { text: `${finished.length} done` }),
          ...finished.map(row))
      : null,
  ];
}

/** What today asks for: the required five and the optional rest. */
function owedScreen() {
  if (!day) return [notRead("what today asks for")];
  // Dropped rather than shown stale — see `stillTrue` in `held.js`. Yesterday is
  // not a worse answer to this question, it is a wrong one.
  if (!left().owed) {
    return [el("p.empty.cold", {
      text: "this is yesterday's copy — what today asks for has not been read yet",
    })];
  }
  const list = dayTasks(day.state);
  const core = required(list);
  const rest = optional(list);
  const done = core.filter((t) => t.done).length;
  const { earned, available } = score(list);

  // A mark, not a box. The todo rows next door wear a box and it is a control
  // you tap; nothing on this screen is, and two things that look the same and
  // answer differently to a finger is the worst kind of small frustration. A
  // row here is a report about somewhere else in the app.
  const line = (task) =>
    el("div.owed", { class: task.done ? "done" : "" },
      el("span.owed-mark", { "aria-hidden": true, text: task.done ? "✦" : "·" }),
      el("span.todo-title", { text: task.label }),
      task.points ? el("span.todo-key", { text: `${task.points}` }) : null,
    );

  return [
    section(
      done === core.length ? `required · all ${core.length} ✦` : `required · ${done} of ${core.length}`,
      core.map(line),
      done === core.length ? "" : "late",
    ),
    rest.length
      ? section(`optional · +${earned} of ${available} pts`, rest.map(line))
      : null,
    // Read-only, and it says so once rather than beside every row. Four of
    // these are answered on this phone and the rest are answered at the
    // laptop; none of them is answered here.
    el("p.heard", { text: "read only — the logs tab is where the phone answers these" }),
  ];
}

// --- the schedule, drawn to scale ---------------------------------------------
//
// This was a list: one line per event, in order, every line the same height.
// Which answers "what is on today" and refuses the question actually being
// asked, which is *when the gaps are*. Two lines reading `9am seminar` and
// `7pm practice` look exactly like two lines reading `9am seminar` and `10am
// practice`, and the difference between those two days is the whole day.
//
// So it is the laptop's week grid with six of the columns taken away. Same
// module doing the arithmetic — `dayplan.js`, which is where that code moved
// so this screen could have it — same packing for events that collide, same
// hue per calendar, same line across the current time. What is different is
// the policy, and there are two differences:
//
//   **The window fits the day.** The laptop clamps to 8am–8pm and exiles
//   anything outside into edge strips, because a single 11:30pm event was
//   making the whole home tab sixteen hours tall. Nothing here is competing
//   for the space — it is one column in a screen you scroll — and an event in
//   a strip is an event whose position has stopped meaning anything, which on
//   a screen whose entire job is skimming is the thing it was there to say.
//
//   **One day, and it is today.** Tomorrow's events still arrive with the
//   payload and are still *kept* — that is what makes the schedule survive a
//   phone that has not reached the laptop since last night, and it is why the
//   route sends two days at all. They are simply not drawn. A schedule you
//   scroll past the end of to find tomorrow is a schedule with tomorrow in it;
//   the question this screen is opened to answer is about the next few hours.

/** How tall an hour is. The laptop's 0.42 gives 25px, which is right for
 *  seven columns beside a year heatmap and is two-thirds of a line of text
 *  here — a 50-minute class would have had nowhere to put its own name. At
 *  0.8 an hour is 48px, a 50-minute block is 40px and holds a time and a
 *  title, and a twelve-hour day is 576px: one long thumb-scroll. */
const AG_PX_PER_MIN = 0.8;

/** The height at which a block has room for a second line, derived from the
 *  scale rather than fixed in minutes — the question is how many pixels the
 *  block has, and a minutes threshold quietly means something else every time
 *  the scale moves. */
const AG_TWO_LINE_PX = 34;

/**
 * How many lines of title a block of a given height can hold.
 *
 * Clipping is what a fixed-height box does to text by default, and what it
 * clips is a *fraction of a line* — a 40-minute block gives its title 1.6
 * lines, so you get one line and the top third of the letters of the next,
 * which reads as a rendering fault rather than as a title that was too long.
 * An ellipsis on a whole line reads as what it is.
 *
 * The two constants are the block's own type: `.ag-event` is 12px on 1.25, so
 * a line is 15px, and above the title sit 4px of padding and the 9px time
 * stamp on the same leading — call it 16px before the first line starts. Both
 * are here rather than measured because measuring means a layout read per
 * block per redraw, and these move only when the stylesheet does.
 */
const AG_LINE_PX = 15;
const AG_TITLE_TOP_PX = 16;
const agLines = (px) => Math.max(1, Math.floor((px - AG_TITLE_TOP_PX) / AG_LINE_PX));

/** The shortest window worth drawing, in minutes.
 *
 *  A day with one half-hour appointment spans one hour once it is rounded out,
 *  and a 48px grid with a 24px block in it does not read as a day at all —
 *  it reads as a mistake. Four hours is enough for the block to sit in
 *  something, and it is what the extra empty grid is *for*: the emptiness is
 *  the information. */
const AG_MIN_SPAN = 4 * 60;

/** `HH:MM` now, for the one comparison the past-dimming makes. */
const nowHM = (d = new Date()) =>
  `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** One calendar's hue, or the first of the twelve for a row that came back
 *  without a calendar column. */
const hueOf = (hues, event) => hues.get(event.calendar) ?? HUES[0];

/**
 * The all-day row, above the grid.
 *
 * Kept, though it is the part of this screen that has the weakest claim to
 * the space: a holiday and a homework deadline have no hour, so they cannot
 * be drawn to scale, and most of what lands here is already a row on the todo
 * tab. It stays because *most* is not all — a shared course calendar's
 * all-day rows never become todos — and one wrapped line of chips costs the
 * grid under it nothing.
 */
function allDayRow(rows, hues) {
  if (!rows.length) return null;
  return el("div.ag-allday", null,
    el("span.ag-allday-at", { text: "all day" }),
    el("div.ag-chips", null,
      ...rows.map((e) =>
        el("span.ag-chip", { style: `--hue:${hueOf(hues, e)}`, text: e.title })),
    ),
  );
}

/** The hour labels down the left, one per hour of the drawn window. */
const agRuler = (open, close) => {
  const rows = [];
  for (let m = open; m <= close; m += 60) {
    rows.push(el("div.ag-hour", {
      style: `top:${(m - open) * AG_PX_PER_MIN}px`,
      text: atMinute(m),
    }));
  }
  return rows;
};

/**
 * The line across now.
 *
 * Only when it falls inside the drawn window, on the laptop's argument: a line
 * pinned to the top edge at seven in the morning is a line claiming it is
 * eight. Since the window here fits the day's own events, that means it is
 * absent before the first thing and after the last, which is correct — at
 * those hours "where am I in the day" has an answer the grid cannot draw and
 * the empty screen above or below it already gives.
 */
function agNow(open, close) {
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  if (mins < open || mins > close) return null;
  return el("div.ag-now", { style: `top:${(mins - open) * AG_PX_PER_MIN}px` });
}

/** Today's schedule, drawn against the clock. */
function agendaScreen() {
  if (!day) return [notRead("the schedule")];
  const { events, stale } = left();
  // Today's alone. After a rollover this is what the route sent as *tomorrow*
  // — which is the whole reason it sends two days, and the reason the phone
  // does not have to reach the laptop to know what its morning looks like.
  const mine = events.filter((e) => e.date === today());
  const allDay = mine.filter((e) => !e.start);
  const timed = mine.filter((e) => e.start).map(timedEvent);
  // One hue per calendar, over today's list. Assigned by the same function the
  // laptop's week grid uses, so a course is the same colour on both devices
  // without either of them being told what the colour is.
  const hues = hueMap([...new Set(mine.map((e) => e.calendar).filter(Boolean))]);

  if (!mine.length) {
    return [
      el("p.empty", {
        // The one distinction the phone cannot guess and the route can. An
        // empty day and an unreadable calendar are the same list.
        text: day.calendar_reached
          ? "nothing on the calendar today"
          : "the laptop could not read the calendar",
      }),
      stale ? el("p.heard.cold", { text: "yesterday's copy of today" }) : null,
    ];
  }

  // Fit the day, then give it a floor. `dayWindow` is seeded with the first
  // event and widened by the rest, which for an empty `timed` would leave the
  // seed as the answer — hence the guard: a day of nothing but all-day rows
  // draws the chips and no grid at all, rather than a grid of one arbitrary
  // hour.
  const grid = [];
  if (timed.length) {
    const first = Math.min(...timed.map((e) => e.from));
    const [open, wanted] = dayWindow(timed, { open: first, close: first });
    // Stretched downward to the floor, and pulled back up only if that would
    // run past midnight — the bottom of the window is the end of the day and
    // there is nothing under it to borrow.
    let close = Math.max(wanted, open + AG_MIN_SPAN);
    let top = open;
    if (close > 24 * 60) {
      top = Math.max(0, 24 * 60 - AG_MIN_SPAN);
      close = 24 * 60;
    }
    const hm = nowHM();

    const blocks = pack(timed).map((event) => {
      const width = 100 / event.of;
      const tall = (event.to - event.from) * AG_PX_PER_MIN;
      const short = tall < AG_TWO_LINE_PX;
      return el(`div.ag-event${short ? " is-short" : ""}`, {
        // Been and gone. Dimmed rather than dropped: what has already happened
        // is half of what makes a schedule readable, because it is how you
        // know where in the day you are without reading a clock.
        class: (event.end ?? event.start) < hm ? "past" : "",
        style: [
          `top:${(event.from - top) * AG_PX_PER_MIN}px`,
          `height:${tall}px`,
          `left:${event.column * width}%`,
          `width:calc(${width}% - 2px)`,
          `--hue:${hueOf(hues, event)}`,
          // How many whole lines of title fit. See `agLines`: without it the
          // block clips a fraction of a line, which looks like a bug rather
          // than like a title that did not fit.
          `--ag-lines:${agLines(tall)}`,
        ].join(";"),
      },
        el("span.ag-event-at", { text: atMinute(event.from) }),
        el("span.ag-event-title", { text: event.title }),
      );
    });

    grid.push(el("div.ag", { style: `--ag-height:${(close - top) * AG_PX_PER_MIN}px` },
      el("div.ag-ruler", null, ...agRuler(top, close)),
      el("div.ag-col", {
        // The hour gridlines are a painted gradient rather than a stack of
        // empty divs, so the scale has to be handed to CSS or the lines and
        // the blocks drift apart the moment one of them is edited.
        style: `--ag-hour:${60 * AG_PX_PER_MIN}px`,
      }, ...blocks, agNow(top, close)),
    ));
  }

  return [
    allDayRow(allDay, hues),
    ...grid,
    // The panel is yesterday's and what is drawn above is what it called
    // tomorrow. Worth saying: the events are right, and anything moved since
    // last night is not in them.
    stale
      ? el("p.heard.cold", { text: "yesterday's copy — today as it looked last night" })
      : null,
  ];
}

/** A heading and its rows. The day tabs' only layout. */
function section(name, rows, kind = "") {
  return el("div.q", null,
    el("div.q-head", null, el("div.q-name", { class: kind, text: name })),
    ...rows,
  );
}

/** Nothing cached and nothing reachable, said about the thing that is missing. */
const notRead = (what) =>
  el("p.empty.cold", { text: `${what} has not been read from the laptop yet` });

/**
 * The bar: every tab in the app, in the order it draws them.
 *
 * One bar since 2026-09-23. There were two roots — the five logs and the day's
 * two lists — behind an entrance screen with a card for each, and every open
 * of the app went title screen, entrance, log, then the tab you wanted: the
 * check-in, every time, whatever the hour. Two things made one bar possible:
 * meal and drink are one tab (`eat`, see `INTAKE`), and the check-in leaves
 * the bar once it is done (`shown`). That leaves five caps, which is what the
 * log root's bar already held, and one fewer tap on every open.
 *
 * The order is the order of a day — check in, eat, the bathroom, train — then
 * the day's two lists after a rule, because they are a different kind of
 * screen: one you read and tick rather than one you file into. The bar is read
 * as well as pressed; a map whose order means nothing is one you re-read every
 * time.
 *
 * What the entrance said moved rather than went: the queue count was already
 * on the send button, and what the day is waiting on is in the header under
 * the date — see `drawHeader`.
 *
 * **`owed` is parked, not deleted.** `owedScreen` is whole, its rules
 * (`stillTrue().owed`, the mark rather than a box, the read-only footnote) are
 * intact, its hue and its `.owed` styles are still in `style.css`, and a row
 * below with `day: true` is the entire cost of reinstating it. It came off the
 * bar because what it reports is a line — `3/5 done · 2 due` — now in the
 * header, and a whole screen that restates a line is a tab you open once to
 * check it still works.
 */
const TABS = [
  {
    id: "checkin",
    label: "check-in",
    render: () => CHECKIN.render(),
    // Kept while it is the tab on screen, so reopening it from the date to
    // fix an answer puts a lit cap under it; leaving it is what lets it go.
    shown: () => !checkedIn() || tab === "checkin",
  },
  { id: "eat", label: "eat", render: eatScreen },
  { id: "bathroom", label: "visit", render: () => BATHROOM.render() },
  { id: "session", label: "session", render: () => SESSION.render() },
  { id: "todo", label: "todo", render: todoScreen, day: true },
  { id: "agenda", label: "agenda", render: agendaScreen, day: true },
];

const onDayTab = (id) => Boolean(TABS.find((one) => one.id === id)?.day);

/**
 * Open a tab, from the bar or from the header.
 *
 * Counted, for `tally.js`. And the day's panel is fetched on the way *into*
 * the day's two tabs rather than on every tap between them, which is what the
 * old day root's card did: it is the heavy request, and moving from the todo
 * list to the agenda is not a reason to ask gcalcli again.
 */
function openTab(id) {
  if (id === tab) return;
  const from = tab;
  tab = id;
  count(id);
  if (onDayTab(id) && !onDayTab(from)) loadDay();
  draw();
}

/**
 * What the day is waiting on, as one line under the date.
 *
 * The entrance's day card, moved: the required tasks done of the day's five,
 * the todos due today, and the check-in's tick once it is in — which is also
 * what says the date is a button. Nothing here waits on the laptop; a part
 * that cannot be known is left out rather than printed as a zero.
 */
function dayState() {
  const stale = dayStale();
  const core = day && !stale ? required(dayTasks(day.state)) : null;
  const due = day
    ? todoRows().filter((i) => !i.done && i.due_date && i.due_date <= today()).length
    : 0;
  return [
    checkedIn() ? "check-in ✓" : null,
    // No "done" after it: with the check-in's tick and a due count beside it,
    // the word is what pushed the line past a 375px screen's edge.
    core ? `${core.filter((t) => t.done).length}/${core.length}` : null,
    due ? `${due} due` : null,
  ].filter(Boolean).join(" · ");
}

/**
 * The header's right-hand side: the date, what the day is waiting on, and the
 * way back to the check-in.
 *
 * A button because the check-in has no cap once it is done, and a morning
 * answer tapped wrong still needs a way back. The date is where the day is
 * named, so it is where the day's own questionnaire is reopened.
 */
function drawHeader() {
  const node = $("#day");
  const state = dayState();
  node.replaceChildren(
    el("span.day-date", { text: dayLabel() }),
    state ? el("span.day-state", { text: state }) : null,
  );
  node.onclick = () => openTab("checkin");
}

/**
 * Which build of this app is running, printed on the title screen.
 *
 * The one question the phone could not answer about itself. Everything here is
 * cached by a service worker so the app opens with the laptop off, which means
 * "is the fix I just made actually on this phone" has no answer you can read —
 * a stale shell and a fresh one look identical, and the way you find out is by
 * hitting the bug again.
 *
 * It is `sw.js`'s `VERSION` verbatim, because that string is what decides which
 * cache the shell is served from, and a version stamp that could disagree with
 * the thing it is stamping is worse than none. Two declarations, since `sw.js`
 * is a classic worker and cannot import a module — so `shell.test.js` fails if
 * they ever drift, which makes the bump a thing you cannot half-do.
 */
const BUILD = "log-v21";

/** How long the title screen stays when nothing lifts it: the wordmark has
 *  landed by 800ms on this screen and the build stamp by 500ms. */
const SPLASH_MS = 1500;

/**
 * The build, along the foot of the title screen.
 *
 * It takes the slot the fight date has on the laptop. That date is on the
 * dashboard, on the health tab and in the camp plan; this is the only surface
 * that can say which code you are looking at, and it is the one place the
 * answer matters — you read it standing somewhere with no laptop, wondering
 * whether the phone caught the change.
 *
 * Painted at boot rather than after the panel lands, so it is there with the
 * laptop off, which is exactly when it is worth reading. Re-applied after
 * `paintSplash`, which is shared with the dashboard and writes the fight date
 * into this element on its way past.
 */
function paintBuild() {
  const foot = $("#sp-fight");
  if (foot) foot.textContent = BUILD;
}

function drawHour() {
  const button = $("#hour");
  // An hour ahead of the clock is last night's, and the button has to say so
  // out loud: it is the difference between a meal filed on the day it was
  // eaten and one filed nineteen hours into the future, and it is decided by a
  // control whose label would otherwise read the same either way.
  const yesterday = dateForHour(hour) !== today();
  button.textContent = yesterday ? `${clock(hour)} yest` : clock(hour);
  button.classList.toggle("open", hourOpen);
  button.classList.toggle("back", yesterday);
  button.setAttribute(
    "aria-label",
    yesterday
      ? `the hour being logged against — ${clock(hour)} yesterday`
      : "the hour being logged against",
  );
  button.onclick = () => { hourOpen = !hourOpen; draw(); };

}

/**
 * The hour picker: twenty-four chips on one scrolling row.
 *
 * A `<select>` would be one line of markup and the native wheel on iOS, and it
 * is the wrong control — the answer is almost always within two of now, and a
 * wheel makes "an hour ago" the same three gestures as "eleven hours ago". A
 * row scrolled to the current hour makes the common answer a tap on a
 * neighbour.
 */
function hourStrip() {
  if (!hourOpen) return null;
  const strip = el("div.hours", null,
    ...Array.from({ length: 24 }, (_, h) =>
      el("button.hourchip", {
        class: h === hour ? "on" : "",
        text: clock(h),
        onclick: () => { hour = h; hourOpen = false; draw(); },
      })),
  );
  // After it is in the document, or there is nothing to scroll.
  queueMicrotask(() => strip.querySelector(".on")
    ?.scrollIntoView({ inline: "center", block: "nearest" }));
  return strip;
}

/**
 * The send button, and the only control here that is about the server.
 *
 * It counts `pending()` and not `pendingOn()`. Everything else on this screen
 * is scoped to the day being logged, correctly — a meal from Tuesday has no
 * business in Wednesday's list. This is the exception, and it was a real bug
 * while it was not: log a day's worth in a gym, open the app the next morning,
 * and the counter read zero with fourteen entries still stranded, because they
 * were yesterday's. The queue is not a view of a day. It is everything still
 * owed, and the button that sends it has to say so.
 *
 * Disabled with an empty queue rather than hidden. A control that appears only
 * when there is something to do is a control you have to go looking for the
 * first time, and its absence is indistinguishable from the entries having
 * gone somewhere.
 */
function drawNet() {
  const waiting = pending().length;
  const node = $("#net");
  node.className = `net${waiting ? " waiting" : ""}${sending ? " sending" : ""}`;
  node.disabled = !waiting || sending;
  node.textContent = sending
    ? "sending…"
    : waiting
      ? `send ${waiting}`
      : "nothing to send";
  node.setAttribute(
    "aria-label",
    waiting ? `send ${waiting} queued ${waiting === 1 ? "entry" : "entries"} to the laptop`
      : "nothing waiting to be sent",
  );
}

/**
 * The bar. See `TABS` for what is in it and why it is one bar.
 *
 * The first of the day's two caps carries a rule down its left-hand side,
 * which is the whole of how the bar says the caps after it are a different
 * kind of screen.
 */
function drawTabs() {
  const shell = $("#shell");
  const bar = $("#tabs");
  // The hue for the whole screen, set once at the top. Every rule that uses it
  // reads `--tab` off this element -- see the foot of `style.css` for why the
  // attribute is here rather than on the bar itself: most of the colour is
  // carried by the answer caps, which are three elements away from the tab
  // that decides it. The eat tab wears the hue of whichever ladder is up.
  shell.dataset.tab = tab === "eat" ? intake : tab;
  const shown = TABS.filter((one) => !one.shown || one.shown());
  bar.replaceChildren(
    ...shown.map((one, i) =>
      el("button.tab", {
        class: [
          one.id === tab ? "on" : "",
          one.day && !shown[i - 1]?.day ? "day-first" : "",
        ].join(" "),
        // Which hue this *cap* wears, which is not the same question as which
        // hue the screen wears. Every cap is coloured, lit or not -- see the
        // foot of `style.css` -- so each one has to name its own log rather
        // than reading the one `#shell` has selected.
        "data-log": one.id,
        "aria-current": one.id === tab,
        text: one.label,
        onclick: () => openTab(one.id),
      })),
  );
}

/**
 * One line under the log saying when the rows above it were last checked.
 *
 * The cost of remembering the laptop's answer is that the answer can be old,
 * and a list that cannot say how old is a list you have to trust completely or
 * not at all. What is stale is only ever the far half of it: anything logged on
 * this phone is on screen the instant it is tapped, and the rows this stamp
 * qualifies are the ones the laptop knows about — an entry made *there*, or one
 * deleted there, which will not appear or disappear here until the next time
 * these two speak.
 *
 * Always on, including a second after a successful load, when it reads as the
 * current time. A line that appears only once the day is stale is a line you
 * have to notice the absence of, and the whole point is to be able to glance at
 * the bottom of a list you are already reading.
 *
 * With no panel at all it says something different and more important. The
 * empty states above it — "nothing eaten yet today" — are written as statements
 * about the day, and on a phone that has not reached the laptop since midnight
 * they are statements it is in no position to make.
 */
function heardLine() {
  if (!panel) {
    return el("p.heard.cold", { text: "not heard from the laptop today — this is the phone's own copy" });
  }
  const at = heard ? new Date(heard) : null;
  return el("p.heard", {
    text: at && !Number.isNaN(at.valueOf())
      ? `laptop last heard from ${timeLabel(at)}`
      : "laptop last heard from earlier today",
  });
}

/**
 * When the day screen last heard, in the same words the log screen uses.
 *
 * Separate from `heardLine` because the two cache the same laptop at different
 * moments — the logs are refreshed on every boot, the day only when its screen
 * is opened — and one stamp covering both would be wrong about whichever of
 * them was older.
 */
function dayHeardLine() {
  if (!day) return null;
  const at = dayHeard ? new Date(dayHeard) : null;
  const when = at && !Number.isNaN(at.valueOf()) ? timeLabel(at) : "earlier";
  if (dayStale()) {
    // `dueLabel` and not `dayLabel`: this is an ISO date being described
    // relative to today, which is the todo list's question rather than the top
    // bar's, and it prints "yesterday" where the other would print the date.
    return el("p.heard.cold", { text: `read ${when} — ${dueLabel(dayFor(), today())}'s copy` });
  }
  return el("p.heard", { text: `laptop last heard from ${when}` });
}

function draw() {
  // Before anything can return: a draw of the day's tabs has to count as "not
  // the ladder", or coming back to a log would replay its motions.
  ladderWas = ladderNow;
  ladderNow = null;
  const screen = TABS.find((one) => one.id === tab) ?? TABS[1];
  // `root` survives as an attribute only: the stylesheet hides the hour on
  // the day's tabs, where there is nothing to file against it.
  $("#shell").dataset.root = screen.day ? "day" : "log";
  drawHeader();
  drawNet();
  drawTabs();
  const view = $("#view");
  view.replaceChildren();

  if (screen.day) {
    for (const node of [screen.render()].flat()) if (node) view.append(node);
    const line = dayHeardLine();
    if (line) view.append(line);
    return;
  }

  drawHour();
  const strip = hourStrip();
  if (strip) view.append(strip);
  for (const node of [screen.render()].flat()) if (node) view.append(node);
  view.append(heardLine());
}

let toastTimer = null;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("up");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("up"), 3200);
}

/**
 * The tab bar, held against the bottom of the *screen* rather than the bottom
 * of the layout.
 *
 * The bar is `position: fixed; bottom: 0`, which reads as "the bottom of the
 * screen" and is not what it means. It means the bottom of the **layout
 * viewport** — the box the page is laid out in — and a soft keyboard does not
 * shrink that box. It covers the lower third of it with a keyboard and slides
 * the **visual viewport** up, which is the window you can actually see. The
 * bar stays at the bottom of the box: a strip of furniture stranded in the
 * middle of the display with the page still scrolling behind it, which is what
 * "the bottom bar detaches from the bottom of the screen" is.
 *
 * It shows up on the session log and nowhere else, and the reason is worth
 * writing down because it is not about sessions. The minutes field is the only
 * typed answer in the whole app that sits **below the fold**: on a 375×812
 * screen with a focus chosen it lands at y=754 against a bar whose top edge is
 * at 753, so tapping it is the one time the browser has to scroll the page to
 * bring a field into view. Every other field — the check-in's measures at
 * y=158, the drink's amount at y=493 — is already on screen, the page does not
 * move, and a bar sitting still on a page that is also sitting still looks
 * exactly like a bar that is behaving. The keyboard was always doing this. The
 * session log is where the page moves enough to see it.
 *
 * So: measure how much of the layout viewport is hidden below the visual one
 * and hand it to the stylesheet as `--lift`. `visualViewport` is the only
 * honest source for this — the keyboard fires no event of its own on either
 * platform, and guessing its height from `innerHeight` deltas is the thing
 * that breaks on the next phone.
 *
 * Doing the arithmetic rather than naming a platform is the point: where the
 * browser *does* shrink the layout viewport for the keyboard — Chromium with
 * `interactive-widget=resizes-content`, and a standalone Android window that
 * resizes — the two viewports agree, the difference is zero, and this sets a
 * lift of nothing. Nothing to detect, nothing to undo, no user-agent test.
 */
const KEYBOARD_MIN = 120;

function holdTheFoot() {
  const seen = window.visualViewport;
  if (!seen) return;

  const measure = () => {
    // The layout viewport, which is what `bottom: 0` is measured against, and
    // `clientHeight` rather than `innerHeight` — the latter includes a
    // horizontal scrollbar's gutter and is a float on a pinched page.
    const box = document.documentElement.clientHeight;
    const hidden = box - (seen.offsetTop + seen.height);
    // A pinch is the other way the two viewports disagree, and lifting the bar
    // for it would be wrong — zoomed in, the bar belongs where the page put
    // it. Only a keyboard is worth correcting, and `KEYBOARD_MIN` is the floor
    // under "a keyboard and not a rounding error": the smallest phone keyboard
    // is a little over 200px and the disagreements that are not keyboards are
    // a pixel or two of rubber band.
    const keyboard = seen.scale <= 1.01 && hidden > KEYBOARD_MIN;
    document.documentElement.style.setProperty(
      "--lift", keyboard ? `${Math.round(hidden)}px` : "0px");
  };

  // `scroll` as well as `resize`: on iOS the keyboard's arrival is a resize,
  // but scrolling afterwards moves the visual viewport within the layout one
  // without resizing anything, and the offset changes under us.
  seen.addEventListener("resize", measure);
  seen.addEventListener("scroll", measure);
  measure();
}

// --- boot ---------------------------------------------------------------------

onQueueChange(drawNet);

$("#net").addEventListener("click", sendNow);

// Bound once, for the life of the app: the bar it corrects is furniture in
// index.html rather than anything `draw` replaces, so there is nothing to
// re-bind when the screen changes.
holdTheFoot();

// Coming back to the front means the hour has moved, and nothing else. It used
// to also flush and reload, on the theory that a phone returning to the front
// is a phone that may be reachable again — which is true, and is still not a
// reason to touch the network. The queue is drained when you say so; a screen
// that quietly tries every time you unlock your phone is the thing that made
// "did that send?" unanswerable.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  hour = nowHour();
  // The other thing that moves while the app is in the background, and the one
  // the panel is not allowed to survive. A phone put down at eleven and picked
  // up at half past midnight comes back to a new heading over the old day's
  // rows, and on this screen those rows have delete buttons — you would be
  // deleting last night's dinner off a list that claims to be tonight's. The
  // queue is untouched by this: it is not a view of a day and never was.
  if (panelDay && panelDay !== today()) {
    panel = null;
    panelDay = null;
    heard = null;
    forget();
  }
  // The day panel is deliberately not dropped alongside it. It is the one thing
  // here built to outlive a rollover — see `stillTrue` in `held.js` — and the
  // three screens that draw it each decide for themselves what is still true.
  // What does have to happen is that a copy from *before* yesterday goes: one
  // day of lookahead is all the route sends, so anything older has no schedule
  // left in it and is only a todo list wearing a stale date.
  if (day && day.date < yesterdayISO()) {
    day = null;
    dayHeard = null;
    forgetDay();
  }
  // The first look at the app on a new day is the morning's, and the morning
  // starts with the check-in if it is still owed — the same rule a cold open
  // follows, for the phone that was only ever put down overnight.
  if (openedOn !== today()) {
    openedOn = today();
    if (!checkedIn()) tab = "checkin";
  }
  count("open");
  count(tab);
  draw();
});

// `online` is deliberately not listened for. The browser's idea of online is
// "this interface has a route", which on a phone means a coffee shop portal it
// has not logged into and a tailnet whose other end is asleep both count. It
// was never the question this app has — the question is whether one specific
// laptop is awake — and the honest way to answer it is to press the button and
// be told.

// The persistence request. `localStorage` is not durable storage: it is subject
// to eviction under pressure, and on iOS to a seven-day cap on script-writable
// storage for sites that are merely visited. A home-screen web app is outside
// that cap, and this call asks for the rest — it is the difference between a
// queue that is a record and a queue that is a cache. Granted silently or
// refused silently; there is nothing to say about it on screen and nothing to
// do about a refusal except be glad the button exists.
navigator.storage?.persist?.().catch(() => {});

// The service worker is what makes this survive being opened with no server —
// it serves the shell from cache so the queue is reachable and writes can still
// be made. It needs a secure context, which `localhost` is and a plain LAN or
// tailnet *address* is not: over `http://192.168.x.x` or `http://100.x.y.z`
// this registration simply does not happen, and the app is then a URL that
// requires a live laptop to produce a screen at all — the queue underneath it
// is perfect and unreachable. Behind `tailscale serve`, which is HTTPS on a
// real `*.ts.net` name, it registers and the app opens from cache forever.
//
// So this is not a nicety. It is the difference between the feature working and
// not, and it is worth one line on screen when it has not happened — unlike
// almost everything else here, the user can fix it, and cannot otherwise know
// it is wrong until the morning the laptop is off.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {
    toast("no offline copy — open this over https to install it");
  });
} else if (!self.isSecureContext) {
  toast("no offline copy — open this over https to install it");
}

// The title screen goes up first — `initSplash` builds the starfield, binds the
// dismissal and raises it. Everything below carries on behind it: the shell is
// hidden rather than absent, so the first log is already drawn when the
// curtain lifts.
//
// Run the phone's way rather than the dashboard's, since 2026-09-23 when he
// said it "sometimes feels like it takes forever": a tap lifts it from the
// first frame, it lifts by itself after `SPLASH_MS` if nothing does, and it
// is never raised again after boot. See `initSplash` for why each of the
// dashboard's three answers was wrong here. Its opening animation is also played
// faster on this screen — see `#splash` in `style.css` — so the build stamp
// along the foot has arrived well before the curtain goes.
initSplash({ returns: false, armMs: 0, liftMs: SPLASH_MS });
paintBuild();
count("open");
count(tab);

// Drawn from the queue and the remembered panel, immediately, with no network
// in the way. That is the cold-start contract: whatever the laptop knows *now*
// arrives later or not at all, and either way the day it knew about the last
// time it was reachable is on screen first, with everything logged since
// sitting in the same list.
draw();

// The title screen, with the laptop off, out of the same remembered panel. The
// call after `load` below is the live one and runs a second time; this is the
// copy, and it is now the one usually seen — the title screen is up for
// `SPLASH_MS`, and even a live `load` rarely lands inside that. FILE 01 · DAY
// 172 either way. `paintBuild` goes back after it for the reason spelled out below.
if (panel?.plan) {
  paintSplash(panel.plan);
  paintBuild();
}

// One read on the way up, and only a read — the queue is not touched. It is
// worth doing unasked in a way that sending is not: `GET /api/health` is
// idempotent, it is what fills in the rows logged from the laptop, and it is
// bounded by `REACH_MS`, so the cost of it failing is four seconds of nothing
// happening in the background.
const landedOn = tab;
load().then((reached) => {
  // Opened on the check-in because this phone did not know it was done — a
  // cleared phone, or one that has not heard the laptop since it was sent —
  // and the laptop has just said it is. Moved on only if no answer has been
  // started, since that is someone correcting the morning.
  if (tab === "checkin" && landedOn === "checkin" && checkedIn() && !Object.keys(draft()).length) {
    tab = "eat";
  }
  draw();
  // FILE 01 · DAY 172 across the top of the bar. `HealthPanel` already
  // carries the plan, so the phone's title screen says the same thing the
  // laptop's does without a second request for it. Absent when there is no
  // camp on, which `paintSplash` renders as silence.
  //
  // The build goes back afterwards: `paintSplash` is the dashboard's own
  // module and writes the fight date into the foot, and on this screen that
  // slot is spent on the one fact only this screen can tell you.
  if (panel?.plan) {
    paintSplash(panel.plan);
    paintBuild();
  }
  // The day, after the logs and only if the logs got through.
  //
  // Chained rather than fired alongside, which is the whole of the ordering
  // argument: the log screens are the hot path and this request is an order of
  // magnitude heavier — the whole boot payload plus a gcalcli call — so it must
  // never be the thing a meal is waiting behind. And it is skipped outright
  // when `load` could not reach the laptop, because the answer is already known
  // and the cost of asking anyway is nine seconds of a dead socket.
  //
  // Unasked, unlike everything else the phone sends, and for the same reason
  // `load` is: it is a GET, it changes nothing, and the screen you land on says
  // what the day is waiting on — which it cannot do out of a cache that is
  // never refilled.
  if (reached) loadDay();
});
