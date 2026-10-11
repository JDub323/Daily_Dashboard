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
//
// This string is also printed along the foot of the title screen, so the phone
// can be asked which build it is running rather than having to be caught out by
// the bug you thought you had fixed. `app.js` declares it a second time — a
// classic worker cannot import a module — and `shell.test.js` fails when the
// two disagree.

const VERSION = "log-v23";

/** How long a *cold* request gets before the shell falls back to itself.
 *  Only ever spent on a file that is not in the cache, which after the first
 *  successful open is none of them. Argued for in the fetch handler. */
const COLD_MS = 2000;

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
  // The laptop's last answer, kept so the day survives being sent. It imports
  // `queue.js` and `when.js`, both already here.
  "./held.js",
  "./when.js",
  "./tally.js",
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
  // What a day asks for, and where a todo row goes. Both are the desktop's own
  // rules rather than a second copy of them, and both are pure — `day.js`
  // imports `logs.js` above and `todolist.js` imports nothing at all, so the
  // two of them add no third file to this list. That property is the reason
  // they are separate modules rather than functions on the panels they came
  // out of, and `shell.test.js` is what keeps it true.
  "../app/day.js",
  "../app/todolist.js",
  // The week grid's arithmetic — packing, the drawn window, the twelve-hue
  // table. It came out of `panels/calendar.js` precisely so it could be listed
  // here: the schedule screen is one column of that grid, and the panel it
  // used to live in imports `api.js`, `dom.js`, `freshness.js`, a modal and
  // `panel.js`. This imports nothing, which is the rule above stated as a
  // file.
  "../app/dayplan.js",
  // `splash.js` imports `dom.js` and `dom.js` imports nothing, so the two of
  // them close the graph.
  "../app/splash.js",
  "../app/dom.js",
  "./icon.svg",
  // The launcher's copy, and the one entry here that is not for the app's own
  // benefit: Android re-reads the maskable icon when the home screen is
  // rebuilt, which is not an occasion it checks for a network first.
  "./icon-maskable.svg",
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

  // Cache first for anything already in the shell, with the refresh happening
  // behind the screen rather than in front of it.
  //
  // This was network first with a two-second stopwatch on it, and the reasoning
  // was sound for one file and wrong for sixteen. The shell is not a flat list
  // of requests, it is a waterfall six levels deep: the navigation has to
  // arrive before `style.css` is known, `style.css` has to be *parsed* before
  // its three `@import`s are asked for, `splash.css` imports `chrome.css`
  // which imports `tokens.css`, the webfont is not requested until all of that
  // has resolved, and `app.js` walks its own graph down through `splash.js` to
  // `dom.js` alongside. Every level waits on the one above it, so the deadline
  // was never two seconds — it was two seconds *per level*.
  //
  // Against a laptop asleep behind a tailnet that drops packets rather than
  // refusing them — the ordinary case, the phone in a gym — that is ten-odd
  // seconds of white before the title card, and nothing can paint sooner:
  // `body.booting` hides the shell, and the splash it hides it in favour of is
  // drawn by a stylesheet at the bottom of the chain.
  //
  // Serving the hit immediately makes the whole waterfall free. Every level
  // resolves out of the cache without a round trip, so the title screen is up
  // before the phone has finished deciding whether it has a network at all.
  // Nothing is lost by not waiting: `app.js` already treats the laptop as
  // optional — it draws from the queue first and folds in `GET /api/health`
  // when and if it answers — so there is no upstream fact a fresher *shell*
  // would have got right.
  //
  // What it costs is that an edit lands one open later rather than this one.
  // That was already half true: the browser only re-checks `sw.js` after the
  // page it is serving has loaded, so a `VERSION` bump has always taken two
  // opens to show up. The build stamp along the foot of the title screen is
  // how you tell which one you are on, and it is worth reading twice now.
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
      // A navigation matches the shell whatever its query says: the only
      // query one carries is a ping's (`?ping=`, see the handlers below), and
      // the shell it wants is the same shell.
      const hit = await caches.match(request, { ignoreSearch: request.mode === "navigate" });
      if (hit) {
        // The refresh has to outlive the response it is not blocking. Without
        // this the worker is eligible for termination the moment the cached
        // copy is handed back, and the update in flight dies with it — which
        // would turn "an edit lands one open later" into "an edit never lands".
        event.waitUntil(fromNetwork.catch(() => {}));
        return hit;
      }

      // Nothing cached, so the network is the only answer there is. This is the
      // first open after an install, and the one place a stopwatch still earns
      // its keep: a request that is going to hang for the OS's own timeout
      // should give up while there is still something to show instead.
      const raced = await Promise.race([
        fromNetwork.catch(() => null),
        new Promise((resolve) => setTimeout(() => resolve(null), COLD_MS)),
      ]);
      if (raced) return raced;

      // A navigation to any path under the scope is the app itself; hand back
      // the shell rather than the browser's dinosaur.
      if (request.mode === "navigate") {
        const shell = await caches.match("./index.html");
        if (shell) return shell;
      }

      // Not a navigation, or there was no shell to fall back to. Wait it out —
      // failing a module request while its fetch is still in flight breaks the
      // graph whole, and a slow file beats a blank screen.
      try {
        return await fromNetwork;
      } catch {
        return Response.error();
      }
    })(),
  );
});

// --- the feel pings ------------------------------------------------------------
//
// Twice a day the laptop pushes "how do you feel?" here (see `dashboard/feel.py`).
// A push must always show something — a browser that receives one and draws
// nothing takes the subscription away — so a body that will not parse is still
// a notification, with the words it would have had.

self.addEventListener("push", (event) => {
  let body = {};
  try {
    body = event.data ? event.data.json() : {};
  } catch { /* shown with the defaults below */ }
  event.waitUntil(self.registration.showNotification(body.title ?? "how do you feel?", {
    body: body.body ?? "energy and mood, two taps",
    // One at a time per ping: a late resend replaces the first rather than
    // stacking beside it.
    tag: body.tag ?? "feel",
    icon: "icon.svg",
    data: { url: body.url ?? "./?ping=" },
  }));
});

// Tapping it opens the app on the feel tab. An app already open is brought
// forward and told which ping — opening a second window would be a second copy
// of the queue — and only when none is open is one opened at the ping's address.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url ?? "./", self.registration.scope);
  const ping = url.searchParams.get("ping") ?? "";
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of open) {
      if (!("focus" in client)) continue;
      client.postMessage({ ping });
      return client.focus();
    }
    return self.clients.openWindow(url.href);
  })());
});
