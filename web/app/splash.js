// The title screen.
//
// It exists for two reasons, and only one of them is that it looks good.
//
// The other is that every moment it covers is a moment the dashboard would
// otherwise spend visibly assembling itself. Boot paints six empty panes and
// fills them when /api/state lands; waking from sleep fires a rollover or a
// live refresh and the panels repopulate under you. Both are jank you cannot
// design away, because the data genuinely is not there yet. A curtain in front
// of them turns the wait into the point.
//
// So the rule is: the screen goes up *before* the work starts, and Enter is
// only offered once the work is done.
//
// Four things raise it — boot, waking from sleep, the day turning, and sitting
// idle. The last three all mean the same thing in the end ("you have been away,
// and what is on screen is older than you think"), but they are detected
// differently and only the sleep one is subtle. See `watchForReturn`.

import { $, el } from "./dom.js";

/** How often the tick runs. Also the resolution of the idle timer. */
const TICK_MS = 30_000;

/**
 * A tick arriving this late means the clock jumped rather than that we were
 * merely busy — i.e. the machine was suspended. Three missed ticks, because a
 * loaded laptop can drop one without having slept at all.
 */
const SLEEP_GAP_MS = 90_000;

/** No keyboard and no pointer for this long and the screen comes back. */
const IDLE_MS = 15 * 60_000;

/**
 * How long the entrance runs before Enter is live, in the worst case.
 *
 * The `PRESS ENTER` prompt fades in at 2s and the whole animation is over by
 * 2.4s. Taking the key before then would let a keypress meant for the
 * dashboard skip a screen the eye has not finished reading, which is the one
 * way a title screen becomes annoying rather than pleasant.
 */
const ARM_MS = 1400;

/** Must match the `#splash` opacity transition in style.css. */
const FADE_MS = 420;

/**
 * How this device wants the screen run — see `initSplash`, which is the only
 * thing that sets it. The defaults are the dashboard's, and the dashboard
 * passes nothing.
 */
let mode = { returns: true, armMs: ARM_MS, liftMs: null };

const dismissListeners = new Set();

let open = false;
let armed = false;
let hideTimer = null;
/** The self-dismissal, when this device has one. */
let liftTimer = null;
/** Set while the screen is up, so the reason reaches the dismiss handlers. */
let reason = "boot";
/** What had the focus when the screen went up, to hand it back on the way out. */
let restoreFocus = null;

/** True while the title screen covers the dashboard. `keys.js` gates on this. */
export const splashOpen = () => open;

/** Called with the reason string after each dismissal. */
export function onSplashDismiss(fn) {
  dismissListeners.add(fn);
  return () => dismissListeners.delete(fn);
}

// --- the two data lines ----------------------------------------------------

/**
 * Fill in the save-file line.
 *
 * Deliberately tolerant of every field being missing: at boot this runs before
 * /api/state has landed, and the screen has to look finished either way. A
 * countdown that is not known yet is simply absent rather than a zero.
 */
export function paintSplash({ days_out = null, fight = null, week = null, weeks = null } = {}) {
  $("#sp-file").textContent = days_out == null ? "FILE 01" : `FILE 01 · DAY ${days_out}`;
  $("#sp-fight").textContent = fight ? stamp(fight) : "";
  $("#sp-sub").textContent =
    week != null && weeks != null ? `FIGHT CAMP · WEEK ${week} OF ${weeks}` : "FIGHT CAMP";
}

/** `2027-02-17` as `17.02.2027` — the readout on the bar, not prose. */
function stamp(iso) {
  const [y, m, d] = String(iso).split("-");
  return d && m && y ? `${d}.${m}.${y}` : String(iso);
}

// --- showing and dismissing ------------------------------------------------

/**
 * Print whichever of the two ways in this device actually has.
 *
 * `pointer: coarse` is the primary input being a finger. A touchscreen laptop
 * reports `fine` and keeps "press enter", which is right — it has both and one
 * of them is faster.
 *
 * Called every time the screen goes up rather than once at startup, and that is
 * not defensiveness: the answer can change after boot. A tab opened at desktop
 * width and later handed to a phone — or, more prosaically, a viewport that
 * finishes resolving after the module runs — would otherwise keep whichever
 * label it happened to be built with, and tell you to press a key that is not
 * there.
 */
function sayHowToDismiss(splash) {
  const touch = matchMedia("(pointer: coarse)").matches;
  $(".sp-press").textContent = touch ? "TAP TO START" : "PRESS ENTER";
  splash.setAttribute(
    "aria-label",
    touch ? "sharpness — tap to begin" : "sharpness — press enter to begin",
  );
}

export function showSplash(why = "wake") {
  if (open) return;
  open = true;
  armed = false;
  reason = why;
  restoreFocus = document.activeElement;

  const splash = $("#splash");
  splash.hidden = false;
  sayHowToDismiss(splash);
  // Two classes rather than one: `booting` is the first-paint state that
  // index.html ships with, and it is the only one that also hides the shell.
  // A re-entry mid-session leaves the dashboard mounted underneath, so that
  // panels keep their timers, their streams and their half-typed inputs.
  document.body.classList.add("splash-up");
  // Restart the entrance. Reading offsetWidth between the two is what forces
  // the style flush; without it the class never appears to have been away.
  splash.classList.remove("sp-run");
  void splash.offsetWidth;
  splash.classList.add("sp-run");

  // The shell is behind an opaque cover, so it is not just invisible — it is
  // not there. Without this a screen reader reads straight through the title
  // screen into a dashboard nobody can see.
  $("#shell").setAttribute("aria-hidden", "true");
  splash.focus({ preventScroll: true });

  // Armed on the spot when there is no wait, rather than on a zero timer: a
  // tap in the first frame is exactly the tap the phone's mode exists for.
  if (mode.armMs > 0) {
    setTimeout(() => {
      armed = true;
    }, mode.armMs);
  } else {
    armed = true;
  }

  clearTimeout(liftTimer);
  if (mode.liftMs != null) {
    liftTimer = setTimeout(() => {
      armed = true;
      dismiss();
    }, mode.liftMs);
  }
}

function dismiss() {
  if (!open || !armed) return;
  open = false;
  clearTimeout(liftTimer);

  const splash = $("#splash");
  document.body.classList.remove("booting", "splash-up");
  $("#shell").removeAttribute("aria-hidden");

  // Kept in the DOM rather than removed: it comes back several times a day,
  // and rebuilding the starfield each time is work for nothing. It does have
  // to leave the accessibility tree though — it is a role=dialog, and one that
  // outlives its own dismissal is one a screen reader is still inside.
  //
  // Not on `transitionend` alone. That event does not arrive in a tab the
  // browser has stopped compositing, and it does not arrive at all if the
  // screen is re-shown mid-fade, either of which would leave the dialog in the
  // tree for the rest of the session. The timer is the one that is guaranteed;
  // the event is only there to make the common case prompt.
  clearTimeout(hideTimer);
  const hide = () => {
    if (!open) splash.hidden = true;
  };
  hideTimer = setTimeout(hide, FADE_MS + 60);
  splash.addEventListener("transitionend", hide, { once: true });

  // Focus first, so the ring lands somewhere real before anything re-renders.
  //
  // `restoreFocus` is only worth honouring when it is somewhere you were
  // actually working — a modal's input, a half-filled field. At boot it is
  // <body>, and after a re-entry it can be the splash itself; handing focus to
  // either means the first `j` goes nowhere.
  const inSplash = restoreFocus === splash || splash.contains(restoreFocus);
  const usable = restoreFocus?.isConnected && restoreFocus !== document.body && !inSplash;
  if (usable) restoreFocus.focus({ preventScroll: true });
  else $("#tabbar .tab.active")?.focus({ preventScroll: true });
  restoreFocus = null;

  for (const fn of dismissListeners) fn(reason);
}

// --- what brings it back ---------------------------------------------------

/**
 * Sleep and idle, on one timer.
 *
 * `visibilitychange` is not enough on its own and is the obvious thing to
 * reach for: a laptop that sleeps with this tab frontmost often never fires
 * it. What is reliable is that timers do not run while the machine is
 * suspended, so a tick that arrives far later than it was scheduled for *is*
 * the wake signal — the wall clock moved and we did not.
 *
 * The visibility handler is still here, but only to check that same gap
 * immediately rather than up to a tick late, which is the difference between
 * the screen being there when you open the lid and it appearing while you are
 * already reading.
 *
 * A backgrounded tab trips the same wire, because browsers throttle and
 * eventually freeze timers in one — so a gap opens without the machine having
 * slept at all. That is left alone rather than guarded against: "this tab has
 * been buried behind something else for minutes" and "you have been away" are
 * the same statement about how stale the screen is, and they deserve the same
 * answer.
 */
function watchForReturn() {
  let lastTick = Date.now();
  let lastActivity = Date.now();

  const check = () => {
    const now = Date.now();
    const slept = now - lastTick > SLEEP_GAP_MS;
    lastTick = now;
    if (open) {
      // Still up from last time. Keep the idle clock from firing the moment
      // it is dismissed.
      lastActivity = now;
      return;
    }
    if (slept) showSplash("wake");
    else if (now - lastActivity > IDLE_MS) showSplash("idle");
  };

  setInterval(check, TICK_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) check();
  });

  // Pointer *movement* counts, not just clicks: reading a long feed without
  // touching the keyboard is not being away from the machine.
  for (const event of ["keydown", "pointerdown", "pointermove", "wheel", "touchstart"]) {
    addEventListener(
      event,
      () => {
        lastActivity = Date.now();
      },
      { passive: true },
    );
  }
}

/**
 * Build the screen, bind its ways out, and raise it.
 *
 * With no argument this is the dashboard's title screen: it waits `ARM_MS`
 * before it can be dismissed, stays until it is, and comes back on sleep, idle
 * and the day turning. The phone passes the other three answers, and each one
 * is about a thing the laptop has and the phone does not:
 *
 *   returns: false  the phone never raises it again after boot. On the laptop
 *                   it covers panels refilling after a wake; the phone draws
 *                   from its own storage in a frame, so there is nothing to
 *                   cover. And iOS freezes a backgrounded app's timers, so
 *                   the sleep detector read every return to the app after
 *                   90s as a wake — the title screen before nearly every log.
 *   armMs: 0        a tap works from the first frame. The laptop's wait is
 *                   there so a key meant for the dashboard does not skip a
 *                   screen unread; on a phone the only tap is one meant for
 *                   this screen, and ignoring it read as the app hanging.
 *   liftMs          goes by itself, so a cold open costs no tap at all.
 */
export function initSplash({ returns = true, armMs = ARM_MS, liftMs = null } = {}) {
  mode = { returns, armMs, liftMs };
  const splash = $("#splash");

  // The starfield is one element per layer carrying N box-shadows. Cheaper to
  // animate than N nodes, and the parallax is then just two durations.
  starfield($(".sp-stars"), 90, 2, 0.75);
  starfield($(".sp-stars2"), 130, 1, 0.45);

  // Capture phase, and the event is stopped dead: `keys.js` also binds Enter,
  // to the focused panel, and one keypress must not both raise the curtain and
  // press whatever was behind it.
  addEventListener(
    "keydown",
    (event) => {
      if (!open) return;
      if (event.key === "Enter") {
        event.preventDefault();
        event.stopImmediatePropagation();
        dismiss();
        return;
      }
      // Everything else is swallowed too. The dashboard is not on screen, so a
      // `j` here is a keypress aimed at something the eye cannot see.
      if (event.key !== "Tab" && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  // A tap anywhere raises the curtain, and on a touchscreen it is the only way
  // to. The splash was dismissed by Enter and by nothing else, which is fine on
  // the machine this was written on and makes it a dead end on a phone: there
  // is no keyboard until something asks for one, and the splash is not a text
  // field, so nothing ever does. The dashboard was unreachable from iOS.
  //
  // Dismissing on click rather than only focusing is also the plainer
  // behaviour on a desktop — a splash screen you click to get past is what
  // everyone expects — and it replaces the focus-restoring handler rather than
  // joining it, because a screen that is going away has no use for focus.
  //
  // `preventDefault` matters on touch: without it the browser synthesises a
  // click after the tap, and that click lands on whatever the splash was
  // covering. Tapping to start would press a button on the dashboard behind it.
  splash.addEventListener("pointerdown", (event) => {
    if (!open) return;
    event.preventDefault();
    dismiss();
  });

  sayHowToDismiss(splash);

  if (returns) watchForReturn();
  showSplash("boot");
}

function starfield(host, count, size, alpha) {
  const shadows = [];
  for (let i = 0; i < count; i++) {
    const x = (Math.random() * 100).toFixed(2);
    const y = (Math.random() * 160).toFixed(2);
    shadows.push(`${x}vw ${y}vh 0 rgba(223,230,242,${alpha})`);
  }
  host.append(
    el("i", {
      style:
        `position:absolute;top:0;left:0;width:${size}px;height:${size}px;` +
        `border-radius:50%;box-shadow:${shadows.join(",")}`,
    }),
  );
}
