// Writes that have not reached the server yet.
//
// The laptop is asleep about eighteen hours a day and the phone is in a
// pocket in a gym with no wifi, so "post it and show the response" is not a
// thing this client can be built on. Every write goes into this queue first
// and is drained whenever the server is reachable; the screen is drawn from
// the server's answer *plus* whatever is still in here, so an entry logged on
// a dead connection is on screen immediately and stays there.
//
// --- why this is not hard ---------------------------------------------------
//
// It would be, if a queued write meant "do this later". It does not. Every
// dated request in this app carries the browser's own date, and the three
// quick logs carry the hour as well — [Invariant 1], which exists because the
// server used to ask WSL what day it was. That invariant was written for a
// timezone bug and it pays for this feature outright: a meal logged at 13:00
// on Tuesday and flushed at 21:00 on Wednesday still lands at 13:00 on
// Tuesday, because the body says so and the server never consults its own
// clock. Nothing here has to reconstruct anything.
//
// So this file is a list, in order, with a lock.
//
// --- what it does not do ----------------------------------------------------
//
// There is no de-duplication. If the server commits a write and the response
// is lost on the way back, the flush retries and the row lands twice. That is
// the one failure this design has, it needs a network that dies inside a
// window of a few hundred milliseconds, and the cure — an idempotency key per
// row — is a column on four tables and a lookup on every insert, to protect
// against something whose manual fix is one tap on the entry you did not
// mean. Deliberately not built. It is written down here instead.
//
// It errs that way on purpose, and that is the whole of the safety argument
// here: this queue is allowed to send a meal twice and is never allowed to
// lose one. A duplicate is on screen, in a list you are already reading, one
// tap from gone. A lost write is a meal you remember logging and cannot find,
// discovered days later, unrecoverable — there is no second copy of it
// anywhere. Every judgement call below is settled by that asymmetry, and the
// one time it was settled the other way (see `flush`, "200 is not delivery")
// it cost a day of them.

import { localISO } from "./when.js";

const KEY = "dashboard.m.queue";

/** Bumped by every change, so the app can redraw without being told twice. */
const listeners = new Set();

export function onQueueChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function announce() {
  for (const fn of listeners) fn(pending());
}

/**
 * Everything still owed to the server, oldest first.
 *
 * Storage can throw — Safari's private mode used to, and a corrupted value
 * survives every reload — and a queue that throws on read is a log that
 * refuses to open. An unreadable queue is treated as an empty one, which
 * loses writes that were already lost.
 */
export function pending() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

/**
 * Persist the queue. Returns whether it actually got there.
 *
 * The old comment here said an entry survives a failed write "in the array the
 * caller is holding". It does not, and never did: every read goes back to
 * storage, so a `setItem` that throws — quota, or storage switched off —
 * destroys the write outright and the row does not even appear on screen. The
 * boolean is what lets `send` say so instead of pretending.
 */
function write(list) {
  let ok = true;
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    ok = false;
  }
  announce();
  return ok;
}

/**
 * Add one write. Returns the entry, or null if storage would not take it.
 *
 * `at` is when it was *logged*, not when it will be sent, and it is only ever
 * used for the "queued" line on screen — what the row means to the database is
 * entirely in `body.date` and `body.hour`.
 */
export function enqueue(path, body, label) {
  const entry = {
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    path,
    body,
    label,
    at: new Date().toISOString(),
    tries: 0,
  };
  return write([...pending(), entry]) ? entry : null;
}

export function drop(id) {
  write(pending().filter((e) => e.id !== id));
}

let draining = false;

/**
 * What a reply has to look like before a write is considered delivered.
 *
 * Not the `content-type` header, which is the obvious check and the weaker
 * one: a portal is free to answer `application/json` with its own object, and
 * a header proves what a middlebox is willing to claim rather than who wrote
 * the body.
 *
 * There are three shapes now, because the day screen writes too. The one that
 * matters is chosen by the *path*, below, and not by trying each in turn: a
 * test that accepts any of three shapes is three times as easy for a portal to
 * satisfy by accident, and the whole value of this check is how narrow it is.
 */
const SHAPES = {
  // The five logs. Every one of those paths answers with the whole
  // `HealthPanel`, and has since there were any.
  //
  // `some` rather than `every`, which is how this has always read: the panel is
  // assembled per-day and a key can be absent. It is the loosest of the three
  // and it is the one with a day of meals behind it, so it is left exactly as
  // it was.
  panel: {
    keys: ["meals_today", "drinks_today", "bathroom_today", "sessions_today", "derived"],
    all: false,
  },
  // `POST /api/todo/done` hands back the whole `TodoPanel`.
  //
  // `every`, not `some`. A body carrying `items` alone is a shape a portal
  // could produce by coincidence; one carrying `items` *and* the alias table
  // *and* the calendar defaults is the dashboard.
  todo: { keys: ["items", "aliases", "default_calendar"], all: true },
  // `POST /api/calendar/add` hands back a `CalendarWrite`. Both lists are
  // always present, empty or not, so `every` costs nothing here either.
  written: { keys: ["written", "failed"], all: true },
};

const shaped = (body, shape) =>
  Boolean(body) && typeof body === "object" && !Array.isArray(body)
  && (shape.all
    ? shape.keys.every((key) => key in body)
    : shape.keys.some((key) => key in body));

/** The dashboard's health panel, and nothing else wearing a 200. */
export const isPanel = (body) => shaped(body, SHAPES.panel);

/** A `DayPanel` off `GET /api/day` — checked before it is cached or drawn, for
 *  the same reason `isPanel` is. `state` and `todo` are the two the whole
 *  screen is built out of; a portal answering with either by accident is not a
 *  thing that happens. */
export const isDay = (body) =>
  Boolean(body) && typeof body === "object" && !Array.isArray(body)
  && "state" in body && "todo" in body && "date" in body;

/**
 * Which reply shape proves a write to `path` was delivered.
 *
 * By path rather than by an `entry.shape` field, deliberately: a field would be
 * written by the caller at enqueue time and would then be *stored*, so an entry
 * queued by an older build would come back with a shape name this build has
 * never heard of, and the safe reading of "I do not know what proves this" is
 * to refuse the delivery forever. The path is in the entry either way and it is
 * the thing that actually decides.
 *
 * An unrecognised path falls back to the health panel, which is the strictest
 * thing to do with a write whose reply we cannot name.
 */
function proofFor(path) {
  if (path.startsWith("/api/todo/")) return SHAPES.todo;
  if (path.startsWith("/api/calendar/")) return SHAPES.written;
  return SHAPES.panel;
}

/**
 * Send what is waiting, oldest first.
 *
 * Strictly sequential, and it stops at the first entry that will not go. Both
 * halves matter. Sequential because two meals logged an hour apart should
 * reach the table in that order, and because a parallel flush of six writes is
 * six health panels coming back to redraw one screen. Stopping because the
 * usual reason a write fails is that the server is not there, and the next
 * five will fail the same way — draining them all just to watch them all fail
 * turns one dead connection into six.
 *
 * A 4xx is the exception and is dropped rather than retried. The server has
 * read the body and refused it; it will refuse it identically in an hour, and
 * an entry that can never be sent sits at the head of the queue blocking
 * everything logged after it. That is the one case where losing the write is
 * better than keeping it.
 *
 * --- 200 is not delivery ----------------------------------------------------
 *
 * This dropped a day of meals, so it is worth stating plainly: a 2xx says
 * something answered, not that the dashboard did. A gym's captive portal
 * answers every request on the network — POSTs included — with 200 and a login
 * page, and this loop used to read that as the write having landed: the JSON
 * parse failed, `.catch(() => null)` swallowed it, and `drop` destroyed the
 * entry. The queue emptied, the button said "sent 4", and the meals had never
 * left the phone. It is the worst failure available here, because every part
 * of the screen agrees the write succeeded.
 *
 * So delivery is now the dashboard's own answer and nothing else. Anything
 * else on a 2xx is `intercepted`, and it stops the drain the way an unreachable
 * server does: whatever is standing in the way will stand in the way of the
 * next five too, and the entries are kept.
 *
 * Returns `{ panel, panelDate, sent, refused, stopped }`. `sent` is entries the
 * dashboard actually accepted — counted rather than inferred from how much the
 * queue shrank, because a write logged *during* the flush makes that
 * subtraction lie. `panelDate` is the day the panel describes, which is the day
 * of the entry that produced it and not necessarily today.
 */
export async function flush({ onRefused } = {}) {
  if (draining) return { panel: null, panelDate: null, sent: 0, refused: 0, stopped: "busy" };
  draining = true;
  let panel = null;
  let panelDate = null;
  let sent = 0;
  let refused = 0;
  let stopped = null;
  try {
    for (const entry of pending()) {
      let response;
      try {
        response = await fetch(entry.path, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(entry.body),
        });
      } catch {
        stopped = "unreachable"; // everything behind this one would fail too
        break;
      }
      if (response.ok) {
        const body = await response.json().catch(() => null);
        if (!shaped(body, proofFor(entry.path))) {
          // Something answered; it was not the laptop. Keep the entry.
          stopped = "intercepted";
          break;
        }
        // Only a log write hands back a day's rows. A ticked todo comes back as
        // a `TodoPanel` and an added one as a `CalendarWrite`, and adopting
        // either as `panel` would draw an empty meal list over a day of meals —
        // the exact failure `isPanel` was written to prevent, arrived at from
        // the inside. The day screen is refreshed by `app.js` after the flush
        // instead, out of `GET /api/day`.
        if (isPanel(body)) {
          panel = body;
          panelDate = entry.body?.date ?? null;
        }
        sent += 1;
        drop(entry.id);
        continue;
      }
      if (response.status >= 400 && response.status < 500) {
        drop(entry.id);
        refused += 1;
        onRefused?.(entry, response.status);
        continue;
      }
      stopped = "server"; // a 5xx is the server having a bad moment, not a bad body
      break;
    }
  } finally {
    draining = false;
  }
  return { panel, panelDate, sent, refused, stopped };
}

/**
 * Queued log writes for one day, which is all the five logs ever draw.
 *
 * Keyed on `body.date`, and the day screen's writes deliberately do not have
 * one — `TodoDoneRequest` and `TodoWriteRequest` forbid unknown fields, and
 * their day is `today`. So a queued tick is invisible here, which is right: it
 * is not a row on any log and has no business in a meal list. `app.js` reads
 * those straight off `pending()` by path.
 */
export const pendingOn = (date = localISO(new Date())) =>
  pending().filter((e) => e.body?.date === date);
