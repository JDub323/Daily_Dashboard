// The holdings panel, behind Windows Hello.
//
// The gate this panel draws is not the gate. `/api/portfolio` refuses without
// a token minted from a verified assertion, and the token expires server-side
// whether or not this countdown is still running — see dashboard/webauthn.py.
// What happens here is presentation: asking Hello for the assertion, showing
// how long is left, and putting the numbers away when it runs out.
//
// The token lives in the module closure below and nowhere else. Not
// localStorage, not sessionStorage, not a cookie: a reload should re-lock, a
// second tab should have to ask for itself, and nothing that survives the page
// should be able to reach the file.

import * as api from "../api.js";
import { el, mount } from "../dom.js";
import { toast } from "../ui.js";

let token = null;
let ticking = null;

const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });
const shares = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });

const auth = () => (token ? { authorization: `Bearer ${token}` } : null);

// U+2212 MINUS SIGN, not the hyphen a raw number stringifies to. Both columns
// go through `sign` because they sit side by side in one cell, and mixing the
// two glyphs there — "−$10.00 -1.64%" — is obvious once you have seen it.
const sign = (value) => (value >= 0 ? "+" : "−");
const tone = (value) => (value >= 0 ? "ok" : "bad");

/** A signed dollar figure, with the sign that makes it readable at a glance. */
function signed(value) {
  if (value == null) return el("span.faint", { text: "—" });
  return el(`span.${tone(value)}`, { text: `${sign(value)}${money.format(Math.abs(value))}` });
}

function pct(value) {
  if (value == null) return null;
  return el(`span.${tone(value)}`, { text: `${sign(value)}${Math.abs(value)}%` });
}

// --- base64url <-> ArrayBuffer ---------------------------------------------
//
// WebAuthn speaks ArrayBuffer in the browser and base64url over the wire, and
// every field crossing that boundary needs converting in one direction or the
// other. atob/btoa are the only primitives available, and neither knows about
// the URL alphabet or the missing padding.

function toBuffer(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function toBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  // Chunked: String.fromCharCode(...bytes) blows the argument limit on a large
  // attestation, and the failure is a RangeError from deep inside the spread
  // rather than anything that names this function.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// --- the two ceremonies -----------------------------------------------------

/**
 * Turn a WebAuthn rejection into something worth reading.
 *
 * The DOM exceptions here are notoriously unhelpful — a cancelled Hello prompt
 * and an unusable origin both surface as NotAllowedError with an empty
 * message, and the second one is the failure someone will actually hit.
 */
function explain(error) {
  if (error?.name === "NotAllowedError") {
    return "Hello was cancelled or timed out — try again";
  }
  if (error?.name === "InvalidStateError") {
    return "this authenticator is already enrolled";
  }
  if (error?.name === "SecurityError") {
    return `WebAuthn refused this origin (${location.origin}) — see the panel's note above`;
  }
  if (error?.name === "NotSupportedError") {
    return "this browser has no platform authenticator — is Windows Hello set up?";
  }
  return error?.message || String(error);
}

async function enroll() {
  const options = await api.postRaw("/api/portfolio/enroll/begin", {}, auth());
  const credential = await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: toBuffer(options.challenge),
      user: { ...options.user, id: toBuffer(options.user.id) },
      excludeCredentials: options.excludeCredentials.map((c) => ({ ...c, id: toBuffer(c.id) })),
    },
  });
  const response = credential.response;
  const publicKey = response.getPublicKey?.();
  if (!publicKey) {
    // getPublicKey() returns null when the browser cannot parse the COSE key
    // it was handed. Saying so beats storing an empty key and failing every
    // future unlock with "that did not verify".
    throw new Error("this browser did not expose the credential's public key");
  }
  await api.post(
    "/api/portfolio/enroll/finish",
    {
      credential_id: credential.id,
      client_data_json: toBase64url(response.clientDataJSON),
      public_key: toBase64url(publicKey),
      alg: response.getPublicKeyAlgorithm(),
      label: navigator.platform || "",
    },
    auth(),
  );
}

async function unlock() {
  const options = await api.postRaw("/api/portfolio/unlock/begin", {});
  const assertion = await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: toBuffer(options.challenge),
      allowCredentials: options.allowCredentials.map((c) => ({ ...c, id: toBuffer(c.id) })),
    },
  });
  const response = assertion.response;
  const granted = await api.post("/api/portfolio/unlock/finish", {
    credential_id: assertion.id,
    client_data_json: toBase64url(response.clientDataJSON),
    authenticator_data: toBase64url(response.authenticatorData),
    signature: toBase64url(response.signature),
  });
  token = granted.token;
}

/**
 * Drop the token, here and on the server.
 *
 * `keepalive` so the pagehide path survives the document being torn down —
 * without it the request is cancelled and the token sits out its full five
 * minutes on a server whose only client has gone.
 */
function lock({ keepalive = false } = {}) {
  const held = token;
  token = null;
  clearInterval(ticking);
  ticking = null;
  if (!held) return;
  fetch("/api/portfolio/lock", {
    method: "POST",
    headers: { authorization: `Bearer ${held}` },
    keepalive,
  }).catch(() => {
    // The token expires on its own; failing to hand it back early is not
    // worth a toast on a page that is already closing.
  });
}

window.addEventListener("pagehide", () => lock({ keepalive: true }));

// --- rendering --------------------------------------------------------------

function countdown(seconds, onExpire) {
  const node = el("span.value");
  let left = seconds;
  const paint = () => {
    const minutes = Math.floor(left / 60);
    node.textContent = `locks in ${minutes}:${String(left % 60).padStart(2, "0")}`;
  };
  paint();
  clearInterval(ticking);
  ticking = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      lock();
      onExpire();
      return;
    }
    paint();
  }, 1000);
  return node;
}

function holdingsTable(positions) {
  const head = el("tr", null,
    ...["symbol", "qty", "last", "value", "today", "total"].map((label) =>
      el("th", { text: label }),
    ),
  );
  const rows = positions.map((p) =>
    el("tr", null,
      el("td", null,
        el("b", { text: p.symbol }),
        p.description ? el("div.faint.small", { text: p.description }) : null,
      ),
      el("td", { text: p.quantity == null ? "—" : shares.format(p.quantity) }),
      el("td", { text: p.last_price == null ? "—" : money.format(p.last_price) }),
      el("td", { text: p.value == null ? "—" : money.format(p.value) }),
      el("td", null, signed(p.day_gain), " ", pct(p.day_gain_pct)),
      el("td", null, signed(p.total_gain), " ", pct(p.total_gain_pct)),
    ),
  );
  return el("div.scroll-x", null, el("table.holdings", null, el("thead", null, head), el("tbody", null, ...rows)));
}

function totalsRow(totals) {
  // What the tab is worth, at the size the question deserves — the two gain
  // figures are read against it rather than beside it as equals.
  return el("div.figure-row", null,
    el("span.figure", { text: totals.value == null ? "—" : money.format(totals.value) }),
    el("span.figure-label", null,
      "today ", signed(totals.day_gain), " ", pct(totals.day_gain_pct),
    ),
    el("span.figure-label", null,
      "all time ", signed(totals.total_gain), " ", pct(totals.total_gain_pct),
    ),
  );
}

/** The "you are at the wrong URL" note. The one failure worth pre-empting. */
function originNote(expected) {
  if (location.origin === expected) return null;
  return el("div.err", {
    text:
      `Windows Hello cannot run at ${location.origin}. WebAuthn needs a domain, ` +
      `and an IP address is not one — open the dashboard at ${expected} instead.`,
  });
}

export default {
  id: "portfolio",
  title: "portfolio",
  tab: "markets",
  slot: "hero",
  hero: true,

  async render(node) {
    const status = await api.get("/api/portfolio/status", {}, auth());

    if (!status.enabled) {
      mount(node, el("div.faint", { text: "switched off in config.toml ([portfolio].enabled)" }));
      return;
    }

    const redraw = () => this.render(node);
    const wrong = originNote(status.origin);

    // Unlocked, and the token still good on the server's own accounting.
    if (status.unlocked && token) {
      const held = await api.get("/api/portfolio", {}, auth());
      mount(node,
        totalsRow(held.totals),
        holdingsTable(held.positions),
        held.warnings.length
          ? el("div.err", null,
              el("div", { text: `${held.warnings.length} row(s) did not fully parse:` }),
              ...held.warnings.map((w) => el("div.small", { text: w })),
            )
          : null,
        el("div.row.spread", null,
          countdown(held.expires_in, redraw),
          el("button", { text: "lock now", onclick: () => { lock(); redraw(); } }),
        ),
        held.as_of
          ? el("div.stamp", {
              text: `positions file saved ${new Date(held.as_of).toLocaleString()}`,
            })
          : null,
      );
      return;
    }

    // A token we still hold but the server has expired — the tab was asleep
    // through the window. Drop it so the button says "unlock" rather than
    // silently failing on the next request.
    if (token && !status.unlocked) lock();

    if (!status.enrolled) {
      mount(node,
        wrong,
        el("div.faint", {
          text:
            "No authenticator enrolled yet. Enrolling creates a passkey in Windows Hello " +
            "for this dashboard — do it now rather than later: until you do, anyone at " +
            "this machine can enrol their own and open the panel with it.",
        }),
        el("div.row", null,
          el("button", {
            text: "enrol Windows Hello",
            disabled: Boolean(wrong),
            onclick: async () => {
              try {
                await enroll();
                await redraw();
              } catch (error) {
                toast(explain(error));
              }
            },
          }),
        ),
      );
      return;
    }

    mount(node,
      wrong,
      el("div.faint", {
        text: status.present
          ? "Locked. Windows Hello will accept your fingerprint."
          : "Locked, and there is no positions file yet — drop the export at the path in " +
            "[paths].positions and it will appear here.",
      }),
      el("div.row", null,
        el("button", {
          text: "unlock with Windows Hello",
          disabled: Boolean(wrong),
          onclick: async () => {
            try {
              await unlock();
              await redraw();
            } catch (error) {
              toast(explain(error));
            }
          },
        }),
      ),
    );
  },
};
