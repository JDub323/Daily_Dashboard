// How old a feed panel's data is, and when to ask for it again.
//
// The server has always cached every feed and served the last good copy when
// the source was unreachable. That is the right behaviour and it was entirely
// invisible: a Yahoo outage put week-old prices on screen looking exactly like
// live ones, which is the same complaint that narrowed the cache's exception
// policy — a stale panel and a broken parser used to be indistinguishable, and
// so were a stale panel and a live one.
//
// `FeedMeta` carries the three facts across HTTP; these two functions are the
// only things that read them.

import { el } from "./dom.js";

/** Never poll tighter than this, whatever the server says. */
const MIN_REFRESH_MS = 60_000;

/** Aim just *past* the expiry rather than at it. */
const SETTLE_MS = 5_000;

/** Used only if a response somehow arrives without a `feed` block. */
const FALLBACK_TTL_SECONDS = 300;

/**
 * Milliseconds until this panel should refetch.
 *
 * Measured from the server's own TTL and the age the data already has, not
 * from a constant here: the cache windows live in `config.toml`, and a second
 * copy of them in JavaScript is a number that drifts. Polling sooner than the
 * expiry would only get the same cached answer handed back, so the useful
 * moment is the one just after it — which, for a panel mounted late in a TTL
 * window, is much sooner than a full TTL away.
 *
 * A stale feed lands on the floor, so an unreachable source is retried every
 * minute rather than once the expiry has long passed.
 */
export function refreshAfter(feed, now = Date.now()) {
  const ttl = (feed?.ttl_seconds ?? FALLBACK_TTL_SECONDS) * 1000;
  const age = feed?.fetched_at ? now - new Date(feed.fetched_at).getTime() : 0;
  return Math.max(MIN_REFRESH_MS, ttl - age + SETTLE_MS);
}

/**
 * The `as of 09:14` line under a feed panel.
 *
 * Deliberately always present, not only when something is wrong. A timestamp
 * that appears only on failure is one you have never learned to read, so the
 * one time it matters it reads as decoration.
 */
export function feedStamp(feed) {
  if (!feed?.fetched_at) return null;
  const at = new Date(feed.fetched_at);
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return el(`div.stamp${feed.stale ? ".stale" : ""}`, {
    text: feed.stale ? `stale — source unreachable, last good fetch ${time}` : `as of ${time}`,
    title: at.toLocaleString(),
  });
}
