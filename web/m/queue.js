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

function write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* Out of quota, or storage disabled. The entry is still in the array the
       caller is holding and will still be posted this session; what is lost is
       surviving a reload, and there is nothing useful to say about it here. */
  }
  announce();
}

/**
 * Add one write. Returns the entry, which carries the id the UI draws it by.
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
  write([...pending(), entry]);
  return entry;
}

export function drop(id) {
  write(pending().filter((e) => e.id !== id));
}

let draining = false;

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
 * Returns the last good panel the server sent back, or null.
 */
export async function flush({ onRefused } = {}) {
  if (draining) return null;
  draining = true;
  let panel = null;
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
        break; // unreachable — everything behind this one would fail too
      }
      if (response.ok) {
        panel = await response.json().catch(() => null);
        drop(entry.id);
        continue;
      }
      if (response.status >= 400 && response.status < 500) {
        drop(entry.id);
        onRefused?.(entry, response.status);
        continue;
      }
      break; // a 5xx is the server having a bad moment, not a bad body
    }
  } finally {
    draining = false;
  }
  return panel;
}

/** Queued writes for one day, which is all any screen here draws. */
export const pendingOn = (date = localISO(new Date())) =>
  pending().filter((e) => e.body?.date === date);
