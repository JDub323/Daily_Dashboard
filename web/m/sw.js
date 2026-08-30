// Enough service worker to open the app with the laptop asleep.
//
// One job: serve the shell — the HTML, the three modules, the stylesheets, the
// icon — from a cache, so that tapping the icon on a phone with no route to the
// server still gets you a screen you can log into. The writes then sit in
// `queue.js` until there is somewhere to send them. Without this the app is
// online-only in the worst sense: the queue would work perfectly and you could
// never reach it.
//
// It deliberately does *not* cache `/api/*`. A cached `GET /api/health` is
// yesterday's meals presented as today's, and this screen already has an honest
// answer for "the server is unreachable" — draw the queue and nothing else. A
// stale-but-plausible list is worse than a short one.
//
// --- when this runs ---------------------------------------------------------
//
// Only in a secure context. `localhost` is one; `http://192.168.1.x:8502` is
// not, and there this file is never registered — see the note at the bottom of
// `app.js`. Everything except offline *loading* works either way. Behind
// `tailscale serve`, which is HTTPS on a real name, all of it does.
//
// --- the version ------------------------------------------------------------
//
// Bump `VERSION` when any file in `SHELL` changes. That is a manual step and it
// is the honest cost of caching a shell: the alternative is a build step, and
// this app is six static files served off a laptop. If you forget, the phone
// keeps the old copy until the cache is evicted — the symptom is an edit that
// does not appear, which is the same symptom `StaticAssets` was written to
// prevent on the desktop and is worth remembering here for the same reason.

const VERSION = "log-v3";

/** How long the network gets to beat the cache. Argued for in the fetch
 *  handler; it is the same reasoning as `REACH_MS` in `app.js` and a shorter
 *  number, because this one is spent before anything is on screen. */
const NETWORK_MS = 2000;

// Every file the app needs to open with nothing behind it.
//
// This list is the app's module graph plus its stylesheets, and it has to be
// *complete*: an ES module graph fails whole. One missing import is not a
// degraded screen, it is a blank one, and the miss does not show up until the
// first time the laptop is off — which is the one occasion the cache exists
// for. `../app/quicklog.js` was here and is not any more, and that is the
// interesting entry: the phone imported two pure functions from it, and it in
// turn imports `api.js`, `capture.js`, `nav.js`, `store.js` and `tabs.js`,
// none of which were ever listed. The graph was five files wider than the
// shell for as long as the shell existed, and it only ever loaded because the
// fetch handler below happened to cache them on the way past. Those two
// functions live in `logs.js` now. The rule that would have caught it: nothing
// under `../app/` may be imported here unless it imports nothing itself, or
// everything it imports is also on this list.
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./queue.js",
  "./when.js",
  "./style.css",
  // The theme, the device layer and the title screen, all shared with the
  // dashboard. `style.css` imports them, and an @import is a separate request
  // the browser makes on its own — a cached shell that omitted them would open
  // offline as unstyled markup, which is worse than not opening.
  "../tokens.css",
  "../chrome.css",
  "../splash.css",
  "../vendor/orbitron/orbitron-latin.woff2",
  // Imports nothing, by design — see the head of the file.
  "../app/logs.js",
  // `splash.js` imports `dom.js` and `dom.js` imports nothing, so the two of
  // them close the graph.
  "../app/splash.js",
  "../app/dom.js",
  "./icon.svg",
  "./manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  // One `cache.add` each, not `addAll`.
  //
  // `addAll` is atomic: one 404 and the whole install rejects, leaving no cache
  // at all — the app then falls back to "works only on wifi" with no signal
  // that anything is wrong, which is the exact failure this file exists to
  // prevent, arrived at by a typo in the list above. Per-file, a miss costs the
  // one file. Whether that is survivable depends on which file, but a shell
  // with fifteen of sixteen entries is strictly better than no shell, and the
  // miss is logged where it can be read off the phone's inspector.
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => Promise.all(
        SHELL.map((path) => cache.add(path).catch((error) => {
          console.error("[sw] not cached:", path, error);
        })),
      ))
      // `skipWaiting` so an update lands on the next open rather than after
      // every tab is closed — on a phone, "every tab is closed" may not happen
      // for weeks.
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // The API is never cached — see the note at the top.
  if (url.pathname.startsWith("/api/")) return;

  // Network first, cache as the fallback, and a stopwatch on the first half.
  //
  // Network first because the other way round means an edit to `app.js` never
  // arriving while the phone is on wifi, which is the whole class of problem
  // `StaticAssets` sends `no-cache` to avoid.
  //
  // The stopwatch because "network first" and "unreachable" are not the same
  // shape of failure. A laptop that is off refuses the connection in
  // milliseconds and the fallback is instant. A network that accepts the packet
  // and drops it — a hotel portal, a locked-down campus wifi, a tailnet whose
  // other end is asleep behind a firewall that blackholes rather than rejects —
  // leaves the socket open until the OS gives up, which is tens of seconds, per
  // file. Sixteen files of that is an app that appears not to launch. So the
  // network gets two seconds to be better than the cache, and after that the
  // cached copy wins. Two seconds is far more than a healthy round trip on a
  // tailnet and far less than a person deciding the icon is broken.
  //
  // The fetch is not aborted when it loses. It is left running so its `cache.put`
  // still lands, which means the slow network still refreshes the shell for
  // next time — it just does not make you wait to find out.
  const fromNetwork = fetch(request)
    .then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(VERSION).then((cache) => cache.put(request, copy));
      }
      return response;
    });

  event.respondWith(
    (async () => {
      const hit = await caches.match(request);
      if (!hit) {
        // Nothing cached, so there is no deadline to keep — the network is the
        // only answer there is, and waiting for it is better than failing while
        // it is still in flight.
        try {
          return await fromNetwork;
        } catch {
          // A navigation to any path under the scope is the app itself; hand
          // back the shell rather than the browser's dinosaur.
          if (request.mode === "navigate") {
            const shell = await caches.match("./index.html");
            if (shell) return shell;
          }
          return Response.error();
        }
      }
      const raced = await Promise.race([
        fromNetwork.then((r) => (r.ok ? r : null)).catch(() => null),
        new Promise((resolve) => setTimeout(() => resolve(null), NETWORK_MS)),
      ]);
      return raced ?? hit;
    })(),
  );
});
