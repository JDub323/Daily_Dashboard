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

const VERSION = "log-v1";

const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./queue.js",
  "./when.js",
  "./style.css",
  "../tokens.css",
  "../app/logs.js",
  "../app/quicklog.js",
  "./icon.svg",
  "./manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  // `skipWaiting` so an update lands on the next open rather than after every
  // tab is closed — on a phone, "every tab is closed" may not happen for weeks.
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
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

  // Network first, cache as the fallback. The other way round would mean an
  // edit to `app.js` never arriving while the phone is on wifi, which is the
  // whole class of problem `StaticAssets` sends `no-cache` to avoid. The cache
  // is here for the case where the network is not an option at all.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const hit = await caches.match(request);
        if (hit) return hit;
        // A navigation to any path under the scope is the app itself; hand back
        // the shell rather than the browser's dinosaur.
        if (request.mode === "navigate") return caches.match("./index.html");
        return Response.error();
      }),
  );
});
