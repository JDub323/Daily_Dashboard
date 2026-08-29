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

import {
  BATHROOM_STEPS, CHECKIN_ITEMS, DRINK_STEPS, MEAL_STEPS,
  RPE_ANCHOR, SESSION_KINDS, toShown, toStored,
} from "../app/logs.js";
import { applicableSteps, parseAmount } from "../app/quicklog.js";
import { enqueue, flush, onQueueChange, pendingOn } from "./queue.js";
import { clock, dayLabel, nowHour, today } from "./when.js";

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
 * One write: queued first, then flushed.
 *
 * Never `fetch` directly, even when the server is plainly reachable. Queue-then-
 * flush is one path with one set of failure modes; posting directly when online
 * and queueing when not is two paths, and the second one is exercised about
 * twice a year on a phone in a basement, which is exactly when you find out it
 * was never right.
 */
async function send(path, body, label) {
  enqueue(path, { date: today(), ...body }, label);
  draw();
  const got = await flush({
    onRefused: (entry, status) =>
      toast(`the server refused “${entry.label}” (${status}) — it has been dropped`),
  });
  if (got) panel = got;
  draw();
}

async function load() {
  try {
    const response = await fetch(`/api/health?date=${today()}`);
    if (response.ok) panel = await response.json();
  } catch {
    /* Offline. `panel` stays as it was — or null on a cold start, which draws
       the queue alone rather than an error page. There is nothing to do about
       an unreachable server on a screen whose whole job is to work without
       one. */
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

const rowsFor = (path, fromPanel) => [
  ...(fromPanel ?? []),
  ...queuedFor(path),
].sort((a, b) => (a.hour ?? 0) - (b.hour ?? 0));

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

      const commit = async () => {
        const body = { hour, ...answers };
        answers = {};
        await send(path, body, label);
      };

      const answer = (name, value) => {
        answers = { ...answers, [name]: value };
        // A branch can strip a question that was already answered — picking
        // `urine` after answering a solidity — and that answer must not ride
        // along into the body, because the table will refuse the row.
        const live = new Set(applicableSteps(steps, answers).map((s) => s.name));
        for (const key of Object.keys(answers)) if (!live.has(key)) delete answers[key];
        if (applicableSteps(steps, answers).every((s) => answers[s.name] != null)) commit();
        else draw();
      };

      return [
        ...asked.map((step) =>
          step.field
            ? amountRow(step, answers, answer)
            : stepRow(step, answers, answer)),
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
function amountRow(step, answers, answer) {
  const { unit, preset, max } = step.field;
  const box = el("input.amount", {
    type: "text",
    inputmode: "decimal",
    value: answers[step.name] ?? preset,
    "aria-label": unit,
  });
  const commit = () => {
    const value = parseAmount(box.value, max);
    if (value == null) return void box.classList.add("bad");
    answer(step.name, value);
  };
  box.addEventListener("input", () => box.classList.remove("bad"));

  return el("div.q", null,
    el("div.q-head", null,
      el("span.q-name", { text: step.name }),
      // The three spellings `parseAmount` actually takes, in its words rather
      // than in a paraphrase of them — a hint that promises a format the box
      // then refuses is worse than no hint.
      el("span.q-hint", { text: `${unit}, 12oz each · 1.5 · .5 · 1 1/2` }),
    ),
    el("div.opts", null,
      // The three that cover a can, a mug and a bottle. Each is one tap and
      // commits, so the box below is for the fourth case rather than the first.
      ...["0.5", "1", "1.5"].map((n) =>
        el("button.opt", { text: n, onclick: () => answer(step.name, Number(n)) })),
    ),
    el("div.row", null, box, el("button.go", { text: "log", onclick: commit })),
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
    rows: () => rowsFor("/api/meal", panel?.meals_today),
    describe: (e) => `${e.size} · quality ${e.quality}`,
    remove: { path: "/api/meal/delete", field: "entry_id" },
  }),
  quickLog({
    id: "drinks",
    label: "drink",
    steps: DRINK_STEPS,
    path: "/api/drink",
    empty: "nothing drunk yet today",
    rows: () => rowsFor("/api/drink", panel?.drinks_today),
    describe: (e) => `${e.kind} · ${say(e.amount)}`,
    remove: { path: "/api/drink/delete", field: "entry_id" },
  }),
  quickLog({
    id: "bathroom",
    label: "visit",
    steps: BATHROOM_STEPS,
    path: "/api/bathroom",
    empty: "nothing logged yet today",
    rows: () => rowsFor("/api/bathroom", panel?.bathroom_today),
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

      const answer = (key, shown) => {
        const next = { ...held, [key]: toStored(key, shown) };
        setDraft(next);
        const complete = CHECKIN_ITEMS.every(([k]) => next[k] ?? saved?.[k]);
        if (complete) {
          const body = {};
          for (const [k] of CHECKIN_ITEMS) body[k] = next[k] ?? saved[k];
          setDraft({});
          send("/api/readiness", body, "check-in");
        } else {
          draw();
        }
      };

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
          rowsFor("/api/session", panel?.sessions_today),
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
  button.textContent = clock(hour);
  button.classList.toggle("open", hourOpen);
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

function drawNet() {
  const waiting = pendingOn().length;
  const node = $("#net");
  node.className = `net${waiting ? " waiting" : ""}`;
  node.textContent = waiting ? `${waiting} waiting to send` : "";
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

// Coming back to the front is the moment everything here is stale: the hour has
// moved, the queue may have been flushed by another tab, and the laptop may
// have woken up since. Also the moment a phone in a pocket for four hours is
// most likely to be reachable again.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  hour = nowHour();
  flush().then((got) => { if (got) panel = got; load().then(draw); });
});

addEventListener("online", () => {
  flush().then((got) => { if (got) panel = got; draw(); });
});

// The service worker is what makes this survive being opened with no server —
// it serves the shell from cache so the queue is reachable and writes can still
// be made. It needs a secure context, which `localhost` is and a plain LAN
// address is not: over `http://192.168.x.x` this registration simply does not
// happen and everything above still works, minus offline *loading*. Behind
// `tailscale serve`, which is HTTPS on a real name, it does.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {
    /* Not a secure context, or the file is missing. Neither is worth a word on
       screen: nothing on this page depends on it having worked. */
  });
}

draw();
flush().then((got) => { if (got) panel = got; return load(); }).then(draw);
