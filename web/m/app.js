// The phone log.
//
// The dashboard's logging cell — the five cards that take turns in one slot on
// the health tab — as something you can use standing up, one-handed, in a
// kitchen or a gym, with the laptop shut.
//
// --- what it is not ---------------------------------------------------------
//
// It is not the desktop cell made narrow. That cell is a *keyboard ladder*:
// `m` `l` `4` is a large, fairly clean meal, and the whole design of it — one
// keystroke per question, the last one commits — is an argument about
// keystrokes. None of that survives contact with a touchscreen, where there is
// no keyboard until you ask for one and asking for one covers half the screen.
//
// So the ladder is unrolled. Every question a log asks is on screen at once as
// a row of buttons big enough to hit without looking, you answer them in any
// order, and the entry commits when the last one is filled. Same questions,
// same legal answers, same request bodies — those come from `app/logs.js`,
// which both clients import, so a step added there grows on both. What differs
// is only how it is asked.
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
  applicableSteps, BATHROOM_STEPS, CHECKIN_ITEMS, DRINK_STEPS, MEAL_STEPS,
  parseAmount, RPE_ANCHOR, SESSION_KINDS, toShown, toStored,
} from "../app/logs.js";
// The dashboard's own title screen, not a copy of it: same module, same
// markup in index.html, same stylesheet. It imports nothing but `dom.js`,
// which imports nothing at all — so bringing it here costs exactly the splash
// and none of the store, the panel harness or the nav ring.
import { initSplash, paintSplash } from "../app/splash.js";
import { enqueue, flush, isPanel, onQueueChange, pending, pendingOn } from "./queue.js";
import { clock, dateForHour, dayLabel, nowHour, today } from "./when.js";

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

/** The last health panel the server gave us, or null before the first fetch
 *  and after a failed one. Null is drawn as "what the queue holds, and nothing
 *  else", which on a phone that has been offline all morning is the truth. */
let panel = null;

/** Which log is open. */
let tab = "meals";

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

/** The session being built. Not persisted: it is answered in one sitting on the
 *  way out of a gym, and a stale kind from Tuesday is worse than an empty form. */
let session = { kind: null, rpe: null, minutes: "" };

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
    if (isPanel(body)) panel = body;
  } catch {
    /* Offline, or slow enough to count as offline. `panel` stays as it was — or
       null on a cold start, which draws the queue alone rather than an error
       page. There is nothing to do about an unreachable server on a screen
       whose whole job is to work without one. */
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
    if (got && panelDate === today()) panel = got;
    // Only worth a round trip if something actually went; otherwise the panel
    // we have is as good as the one we would get, and this is a request that
    // will hang for `REACH_MS` on the connection that just failed.
    if (sent) await load();

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

const rowsFor = (path, fromPanel, deletePath) => {
  const going = deletePath ? deletedBy(deletePath) : new Set();
  return [
    ...(fromPanel ?? []).filter((row) => !going.has(row.id)),
    ...queuedFor(path),
  ].sort((a, b) => (a.hour ?? 0) - (b.hour ?? 0));
};

// --- the logs -------------------------------------------------------------------
//
// Each is `{ id, label, render }`. `render` returns the body of the screen; the
// tab bar, the day and the hour are the shell's and are the same for all five.

/**
 * A row of answer buttons for one step of a quick log.
 *
 * The unrolled ladder: every question visible, answered in any order, and the
 * entry posts when the last one is filled. On the desktop the equivalent is
 * three keystrokes and this would be a waste of a card; here there is no
 * keyboard and a wizard that asks one question per screen is three taps and two
 * animations to say "a large clean meal".
 */
function stepRow(step, draftValues, answer) {
  const chosen = draftValues[step.name];
  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: step.name }),
      step.hint ? el("span.q-hint", { text: step.hint }) : null,
    ),
    el("div.opts", null,
      ...step.options.map(([, value, label]) =>
        el("button.opt", {
          class: chosen === value ? "on" : "",
          "aria-pressed": chosen === value,
          text: label,
          onclick: () => answer(step.name, value),
        }),
      ),
    ),
  );
}

/**
 * A quick log — meals, drinks, in and out.
 *
 * All three are the same shape and always were; `quicklog.js` is the desktop's
 * version of this same observation. The branch (`when`) is honoured through
 * `applicableSteps`, imported rather than reimplemented, so the phone cannot
 * come to a different conclusion about whether a urine entry is asked for a
 * solidity than the card on the laptop does.
 */
function quickLog({ id, label, steps, path, describe, empty, rows, remove }) {
  let answers = {};

  return {
    id,
    label,
    render() {
      const asked = applicableSteps(steps, answers);

      const ready = asked.every((step) => answers[step.name] != null);

      const commit = async () => {
        if (!ready) return;
        // The date comes from the hour, not from the clock. `dateForHour` says
        // why: an hour later than now is yesterday's, and stamping it with
        // today's date files it in the future and loses it off the day it
        // happened on. `send` fills in today only when a body does not say.
        const body = { date: dateForHour(hour), hour, ...answers };
        answers = {};
        await send(path, body, label);
      };

      // Answering only ever changes the selection. It used to *post* the moment
      // the last question was filled, which made the fastest possible entry —
      // two taps — also the easiest thing to do by accident: a stray thumb on
      // `quality 3` while scrolling wrote a meal, and the only way to find out
      // was to notice the row appear. A wrong tap is now free, and the write is
      // a deliberate second thing.
      const answer = (name, value) => {
        answers = { ...answers, [name]: value };
        // A branch can strip a question that was already answered — picking
        // `urine` after answering a solidity — and that answer must not ride
        // along into the body, because the table will refuse the row.
        const live = new Set(applicableSteps(steps, answers).map((s) => s.name));
        for (const key of Object.keys(answers)) if (!live.has(key)) delete answers[key];
        draw();
      };

      return [
        ...asked.map((step) =>
          step.field
            ? amountRow(step, answers, answer)
            : stepRow(step, answers, answer)),
        // Disabled rather than hidden while the entry is incomplete: a button
        // that appears when you finish is a button you have to notice, and one
        // that is greyed the whole time you are answering is a progress
        // indicator you never have to read.
        el("button.go.commit", {
          text: ready ? `log ${label}` : `pick ${asked.filter((s) => answers[s.name] == null).length} more`,
          disabled: !ready,
          onclick: commit,
        }),
        entryList(rows(), describe, empty, { remove }),
      ];
    },
  };
}

/**
 * The one typed answer in the three quick logs: how many drinks.
 *
 * Argued for at length in `panels/drinks.js` — the number is printed on the
 * side of the bottle, and bucketing it into small/medium/large would be
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
 * for a row that is sitting right there on screen.
 */
function entryList(rows, describe, empty, { at = (e) => clock(e.hour), remove } = {}) {
  if (!rows.length) return el("p.empty", { text: empty });
  return el("ul.entries", null,
    ...rows.map((entry) =>
      el("li.entry", { class: entry.queued ? "queued" : "" },
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
              onclick: () => send(remove.path, { [remove.field]: entry.id }, "delete"),
            }),
      ),
    ),
  );
}

/** `1`, `1.5`, `0.5` — never `1.00`, and never `0.30000000000000004`. */
const say = (amount) => String(Math.round(amount * 100) / 100);

const LOGS = [
  quickLog({
    id: "meals",
    label: "meal",
    steps: MEAL_STEPS,
    path: "/api/meal",
    empty: "nothing eaten yet today",
    rows: () => rowsFor("/api/meal", panel?.meals_today, "/api/meal/delete"),
    describe: (e) => `${e.size} · quality ${e.quality}`,
    remove: { path: "/api/meal/delete", field: "entry_id" },
  }),
  quickLog({
    id: "drinks",
    label: "drink",
    steps: DRINK_STEPS,
    path: "/api/drink",
    empty: "nothing drunk yet today",
    rows: () => rowsFor("/api/drink", panel?.drinks_today, "/api/drink/delete"),
    describe: (e) => `${e.kind} · ${say(e.amount)}`,
    remove: { path: "/api/drink/delete", field: "entry_id" },
  }),
  quickLog({
    id: "bathroom",
    label: "visit",
    steps: BATHROOM_STEPS,
    path: "/api/bathroom",
    empty: "nothing logged yet today",
    rows: () => rowsFor("/api/bathroom", panel?.bathroom_today, "/api/bathroom/delete"),
    describe: (e) => `${e.kind} · ${e.solidity ?? e.color} · ${e.volume}`,
    remove: { path: "/api/bathroom/delete", field: "entry_id" },
  }),
  checkinLog(),
  sessionLog(),
];

/**
 * The morning check-in.
 *
 * Five questions on 1–5 plus the two measurements, and it is the one log with a
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
        draw();
      };

      const answered = CHECKIN_ITEMS.filter(([k]) => (held[k] ?? saved?.[k]) != null);
      const ready = answered.length === CHECKIN_ITEMS.length;
      // Whether anything on screen differs from the row in the database. This
      // is the question the panel was silent about: with a morning already
      // logged, every answer is *shown* as selected whether it came from the
      // table or from a tap ten seconds ago, and the two are not the same thing.
      const dirty = CHECKIN_ITEMS.some(([k]) => held[k] != null && held[k] !== saved?.[k]);

      const save = () => {
        if (!ready || !dirty) return;
        const body = {};
        for (const [k] of CHECKIN_ITEMS) body[k] = held[k] ?? saved[k];
        setDraft({});
        send("/api/readiness", body, "check-in");
      };

      const status = !ready
        ? `${CHECKIN_ITEMS.length - answered.length} still to answer`
        : dirty
          ? "not saved yet"
          : "saved";

      return [
        measure("weight", "lb", "weight"),
        measure("sleep_hours", "hours slept", "sleep"),
        ...CHECKIN_ITEMS.map(([key, name, low, high]) => {
          const shown = shownFor(key);
          return el("div.q", null,
            el("div.q-head", null,
              el("span.q-name", { text: name }),
              el("span.q-hint", { text: `1 ${low} · 5 ${high}` }),
            ),
            el("div.opts", null,
              ...[1, 2, 3, 4, 5].map((n) =>
                el("button.opt.num", {
                  class: shown === n ? "on" : "",
                  "aria-pressed": shown === n,
                  text: String(n),
                  onclick: () => answer(key, n),
                })),
            ),
          );
        }),
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
 * One of the two scalars the check-in takes: weight, and hours slept.
 *
 * A number box rather than buttons, because there is no set of five answers to
 * "what do you weigh". `decimal` rather than `number` on the input: a
 * `type=number` on iOS is a text keyboard with a spinner nobody can hit, and
 * `inputmode` gets the keypad without the spinner.
 */
function measure(metric, unit, name) {
  const series = panel?.series?.find((s) => s.metric === metric);
  const last = series?.points?.[series.points.length - 1];
  const logged = last && last[0] === today() ? last[1] : null;
  const queued = pendingOn().find((e) => e.path === "/api/health" && e.body.metric === metric);

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

/** A training session: what, how hard, how long. */
function sessionLog() {
  return {
    id: "session",
    label: "session",
    render() {
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

      const commit = () => {
        const mins = Number(String(session.minutes).trim());
        if (!session.kind || !session.rpe) return void toast("pick a kind and an RPE");
        if (!Number.isInteger(mins) || mins < 1 || mins > 600) {
          return void minutes.classList.add("bad");
        }
        const body = { kind: session.kind, rpe: session.rpe, minutes: mins };
        session = { kind: null, rpe: null, minutes: "" };
        send("/api/session", body, "session");
      };

      return [
        el("div.q", null,
          el("div.q-head", null, el("span.q-name", { text: "kind" })),
          el("div.opts", null,
            ...Object.keys(SESSION_KINDS).map((kind) =>
              el("button.opt", {
                class: session.kind === kind ? "on" : "",
                "aria-pressed": session.kind === kind,
                text: kind,
                onclick: () => { session.kind = kind; draw(); },
              })),
          ),
        ),
        el("div.q", null,
          el("div.q-head", null,
            el("span.q-name", { text: "rpe" }),
            // The anchor for whatever is selected. The ten of them at once is
            // a wall of words; the one you have chosen is the thing that stops
            // RPE 7 drifting into RPE 5 over a long camp.
            el("span.q-hint", { text: session.rpe ? RPE_ANCHOR[session.rpe] : "1 nothing · 10 everything" }),
          ),
          el("div.opts.opts-10", null,
            ...Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
              el("button.opt.num", {
                class: session.rpe === n ? "on" : "",
                "aria-pressed": session.rpe === n,
                text: String(n),
                onclick: () => { session.rpe = n; draw(); },
              })),
          ),
        ),
        el("div.q", null,
          el("div.q-head", null, el("span.q-name", { text: "minutes" })),
          el("div.row", null, minutes, el("button.go", { text: "log", onclick: commit })),
        ),
        entryList(
          rowsFor("/api/session", panel?.sessions_today, "/api/session/delete"),
          (e) => `rpe ${e.rpe} · ${e.minutes}m`,
          "nothing trained yet today",
          {
            // A session has no hour — the table records the day and the load,
            // not the clock — so the left column says which session it was
            // rather than printing every one of them as midnight.
            at: (e) => e.kind,
            remove: { path: "/api/session/delete", field: "session_id" },
          },
        ),
      ];
    },
  };
}

// --- the shell --------------------------------------------------------------------

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
  $("#day").textContent = dayLabel();
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

function drawTabs() {
  const bar = $("#tabs");
  bar.replaceChildren(
    ...LOGS.map((log) =>
      el("button.tab", {
        class: log.id === tab ? "on" : "",
        "aria-current": log.id === tab,
        text: log.label,
        onclick: () => { tab = log.id; draw(); },
      })),
  );
}

function draw() {
  drawHour();
  drawNet();
  drawTabs();
  const log = LOGS.find((l) => l.id === tab) ?? LOGS[0];
  const view = $("#view");
  view.replaceChildren();
  const strip = hourStrip();
  if (strip) view.append(strip);
  for (const node of [log.render()].flat()) if (node) view.append(node);
}

let toastTimer = null;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("up");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("up"), 3200);
}

// --- boot ---------------------------------------------------------------------

onQueueChange(drawNet);

$("#net").addEventListener("click", sendNow);

// Coming back to the front means the hour has moved, and nothing else. It used
// to also flush and reload, on the theory that a phone returning to the front
// is a phone that may be reachable again — which is true, and is still not a
// reason to touch the network. The queue is drained when you say so; a screen
// that quietly tries every time you unlock your phone is the thing that made
// "did that send?" unanswerable.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  hour = nowHour();
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

// The title screen goes up first and comes down on a tap, exactly as it does
// on the dashboard — `initSplash` builds the starfield, binds the dismissal and
// raises it. Everything below carries on behind it: the shell is hidden rather
// than absent, so the first log is already drawn when the curtain lifts.
initSplash();

// Drawn from the queue alone, immediately, with no network in the way. That is
// the cold-start contract now: whatever the laptop knows arrives later or not
// at all, and either way there is a working screen first.
draw();

// One read on the way up, and only a read — the queue is not touched. It is
// worth doing unasked in a way that sending is not: `GET /api/health` is
// idempotent, it is what fills in the rows logged from the laptop, and it is
// bounded by `REACH_MS`, so the cost of it failing is four seconds of nothing
// happening in the background while the title screen is still up.
load().then(() => {
  draw();
  // FILE 01 · DAY 172, and the fight date along the foot. `HealthPanel`
  // already carries the plan, so the phone's title screen says the same
  // thing the laptop's does without a second request for it. Absent when
  // there is no camp on, which `paintSplash` renders as silence.
  if (panel?.plan) paintSplash(panel.plan);
});
