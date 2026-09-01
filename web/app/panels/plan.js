// What today asks for, as boxes to fill in.
//
// The nudge and the logging are the same object here, deliberately. A reminder
// you read and then have to go and act on somewhere else is two jobs; a row
// that says "log your road work" and has the three boxes in it is one. It
// appears because the plan says so and it goes away because you filled it in,
// which is the entire mechanism.
//
// This panel does NOT replace `session`. That one stays exactly as it was, for
// logging something the plan never asked for — a pickup session, a walk, a day
// the schedule got abandoned. Two panels rather than one rewritten panel is the
// honest split: prescribed and freehand are different acts, and the freehand
// form's keyboard flow is documented behaviour that had no reason to change.
//
// --- what is deliberately not pre-filled ------------------------------------
//
// The kind, and only the kind. Minutes and RPE are always empty, at John's
// explicit instruction and for a reason worth restating: load is rpe x minutes,
// and every number `health.py` derives — acwr, monotony, strain — is a multiple
// of exactly those two. A default that survived into a submit would be a
// measurement nobody took, and it would be indistinguishable afterwards from
// one that was.
//
// `asks` is printed beside the box for the same reason it is not *in* the box:
// `A3` means "go and do thirty minutes", and what you log is however long you
// were actually out.
//
// --- cards, and why a practice is one --------------------------------------
//
// A card is one outing — one `*` group in the plan. A practice splits into a
// conditioning row and a technique row because John asked for those volumes to
// stay separable, which costs four fields a day instead of two. Making them one
// card with one button is as far as that cost can be cut without giving the
// split back up: it is one thing that happened, and it should be one action.
//
// Extras are the parts the plan cannot predict — sparring, neck. They are
// toggles rather than rows, never owed, and an extra that today's blocks
// already prescribe is not offered at all: the prescribed block is ticked off
// instead, so a neck workout done at practice is not entered twice.

import * as api from "../api.js";
import { beginCapture, endCapture } from "../capture.js";
import { el, mount } from "../dom.js";
import { answeredKeys } from "../logs.js";
import { notify, put, store } from "../store.js";

const OWNER = "plan";

/** The anchors that stop RPE 7 drifting into RPE 5 over a long camp. Same
 *  table as the freehand form, and deliberately the same words. */
const RPE_ANCHOR = {
  1: "nothing", 2: "very easy", 3: "easy", 4: "comfortable", 5: "moderate",
  6: "working", 7: "hard", 8: "very hard", 9: "near maximal", 10: "everything",
};

/**
 * Today's prescription, arranged into cards.
 *
 * Pure, and exported for that reason: which blocks are already answered is the
 * piece of this panel that decides whether a nudge is showing, and a bug in it
 * is invisible — the card simply does not appear, on a morning you would have
 * no reason to go looking for it.
 */
export function cards(plan, sessions) {
  if (!plan?.today.length) return [];
  // `answeredKeys` rather than a set built here: the phone answers prescribed
  // blocks too, and the two clients deciding separately what "already logged"
  // means is exactly the drift that would leave one of them asking for a
  // session the other had recorded.
  const done = answeredKeys(sessions);
  const logged = new Map(sessions.filter((s) => s.plan_key).map((s) => [s.plan_key, s]));

  return [...new Set(plan.today.map((b) => b.slot))]
    .sort((a, b) => a - b)
    .map((slot) => {
      const all = plan.today.filter((b) => b.slot === slot);
      const rows = all.filter((b) => !b.extra);
      return {
        slot,
        group: all.find((b) => b.group)?.group ?? null,
        rows,
        extras: all.filter((b) => b.extra),
        logged,
        // Only the prescribed rows count. A card that needed its extras before
        // it would close would never close on a day you did not spar.
        done: rows.length > 0 && rows.every((b) => done.has(b.key)),
      };
    });
}

/**
 * The POST body for one card — the rows you filled in, and nothing else.
 *
 * Exported and pure because this is where a mis-shaped submit would turn into
 * a doubled day or a silently dropped row, and neither announces itself.
 */
export function partsFrom(card, draft, chosen) {
  const wanted = [...card.rows, ...card.extras.filter((b) => chosen.has(b.key))];
  return wanted
    .filter((b) => !card.logged.has(b.key))
    .map((b) => {
      const d = draft.get(b.key) ?? {};
      return {
        kind: b.kind ?? d.kind ?? b.choose[0],
        rpe: d.rpe ?? 0,
        minutes: Number.parseInt(d.minutes ?? "", 10),
        note: (draft.get(`note:${card.slot}`)?.text ?? "").trim() || null,
        plan_key: b.key,
      };
    });
}

/** What is missing, as a sentence, or null when the card is ready to post. */
export function incomplete(parts) {
  const bad = parts.find(
    (p) => !p.rpe || !Number.isInteger(p.minutes) || p.minutes < 1 || p.minutes > 600,
  );
  if (!bad) return null;
  return !bad.rpe ? "pick an RPE" : "minutes?";
}

// --- state that outlives a render -------------------------------------------
//
// A card posts and the store notifies, which rebuilds this body — so a
// half-filled card cannot live in a render closure. Keyed by plan key rather
// than by position, so a redraw that reorders nothing still finds its values,
// and a rollover that changes every key correctly finds none.

/** `planKey -> { rpe, minutes, kind }`, plus `note:<slot> -> { text }`. */
const draft = new Map();
/** Extra blocks switched on, by plan key. */
const chosen = new Set();
/** `{ slot, row, field }` — where the keyboard is, or null. */
let stage = null;
let handlers = null;

const setDraft = (key, patch) => draft.set(key, { ...(draft.get(key) ?? {}), ...patch });

function take() {
  if (!handlers) return;
  beginCapture({
    owner: OWNER,
    label: handlers.label(),
    onKey: handlers.onKey,
    onEnd: handlers.onEnd,
  });
  handlers.paint();
}

export default {
  id: OWNER,
  title: "today's plan",
  tab: "health",
  slot: "grid",
  span: 3,

  onEnter() {
    const open = cards(store.state.health.plan, store.state.health.sessions_today).find(
      (c) => !c.done,
    );
    if (!open) return false;
    stage = { slot: open.slot, row: 0, field: "rpe" };
    take();
    return true;
  },

  async render(node) {
    // The headline is what the plan actually asks for -- `lower french
    // contrast`, not the plainer `strength training` bucket a log of it lands
    // in. The bucket is the thing you can be nudged about, but it is not the
    // thing you go and do, and by the time this row is on screen the nudge has
    // already worked. The two qualifiers ride along in the same string because
    // each changes what is being asked: an optional block is one you may skip,
    // and a regression is the lighter thing you can drop to that still counts
    // as done.
    const headline = (block) => {
      const parts = [block.name];
      if (block.optional && !block.extra) parts.push("(optional)");
      if (block.regress_to) parts.push(`(Regression: ${block.regress_to})`);
      return parts.join(" ");
    };

    const health = store.state.health;
    const plan = health.plan;
    const list = cards(plan, health.sessions_today);

    if (!list.length) {
      // Not an error and not an empty form. The plan asks for nothing today —
      // either a rest day or a week nobody has written — and the hero above
      // already says which. Repeating it here would be two panels answering
      // one question.
      mount(node, el("div.faint", { text: "nothing on the plan today." }));
      return;
    }

    const message = el("span.faint");
    /** Every field node, so the keyboard walker can reach them after a draw. */
    const fields = new Map();

    function cardNode(card) {
      const heading = card.group ?? (card.rows[0] ? headline(card.rows[0]) : "training");

      if (card.done) {
        return el("div.plan-card.done", null,
          el("div.row.spread", null,
            el("span.value", { text: `✓ ${heading}` }),
            el("span.faint", {
              text: card.rows
                .map((b) => {
                  const s = card.logged.get(b.key);
                  return `${b.name} rpe ${s.rpe} × ${s.minutes}m`;
                })
                .join(" · "),
            }),
          ),
        );
      }

      const rowNodes = card.rows
        .filter((b) => !card.logged.has(b.key))
        .map((block, index) => rowNode(card, block, index));

      const extraNodes = card.extras.map((block) =>
        el("button.tiny", {
          text: headline(block),
          class: chosen.has(block.key) ? "on" : "",
          title: "offered, never owed",
          onclick: (e) => {
            if (chosen.has(block.key)) chosen.delete(block.key);
            else chosen.add(block.key);
            e.currentTarget.blur();
            notify("health");
          },
        }),
      );

      const chosenNodes = card.extras
        .filter((b) => chosen.has(b.key))
        .map((block, i) => rowNode(card, block, card.rows.length + i));

      const note = el("input", {
        placeholder: "note (optional)",
        maxlength: 200,
        value: draft.get(`note:${card.slot}`)?.text ?? "",
        oninput: (e) => setDraft(`note:${card.slot}`, { text: e.currentTarget.value }),
        onkeydown: (e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          void submit(card);
        },
      });
      fields.set(`note:${card.slot}`, note);

      return el("div.plan-card", null,
        el("div.row.spread.gap", null,
          el("span.value", { text: `log your ${heading}` }),
          card.rows[0]?.source ? el("span.faint", { text: card.rows[0].source }) : null,
        ),
        ...rowNodes,
        ...chosenNodes,
        extraNodes.length
          ? el("div.row.gap", null, el("span.faint", { text: "also did" }), ...extraNodes)
          : null,
        note,
        el("div.row", null,
          el("button.primary", { text: "log", onclick: () => submit(card) }),
        ),
      );
    }

    function rowNode(card, block, index) {
      const d = draft.get(block.key) ?? {};

      const minutes = el("input.mins", {
        type: "number",
        min: "1",
        step: "1",
        inputmode: "numeric",
        placeholder: "min",
        value: d.minutes ?? "",
        oninput: (e) => setDraft(block.key, { minutes: e.currentTarget.value }),
        onkeydown: (e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          advance(card, index);
        },
      });
      fields.set(`${block.key}:minutes`, minutes);

      const rpeRow = el("div.scale", null,
        ...Array.from({ length: 10 }, (_, i) => i + 1).map((n) =>
          el("button.tiny", {
            text: String(n),
            class: n === d.rpe ? "on" : "",
            onclick: () => {
              setDraft(block.key, { rpe: n });
              paintRpe(block.key, n);
              minutes.focus();
              minutes.select();
            },
          }),
        ),
      );
      fields.set(`${block.key}:rpe`, rpeRow);

      // Only where the plan genuinely does not decide — cardio. Everywhere else
      // the kind is the one thing that IS pre-filled, so there is nothing to
      // ask.
      const kindRow = block.choose.length
        ? el("div.scale", null,
            ...block.choose.map((k) =>
              el("button.tiny", {
                text: k,
                class: k === (d.kind ?? block.choose[0]) ? "on" : "",
                onclick: (e) => {
                  setDraft(block.key, { kind: k });
                  for (const b of e.currentTarget.parentNode.children) {
                    b.classList.toggle("on", b.textContent === k);
                  }
                },
              }),
            ),
          )
        : null;
      if (kindRow) fields.set(`${block.key}:kind`, kindRow);

      return el("div.plan-row", null,
        el("div.row.spread.gap", null,
          el("span", null,
            el("span.value", { text: headline(block) }),
            block.extra ? el("span.faint", { text: " (extra)" }) : null,
          ),
          el("span.faint", {
            text: [block.label, block.asks].filter(Boolean).join(" · "),
          }),
        ),
        kindRow,
        el("div.row.gap", null, rpeRow, minutes),
      );
    }

    function paintRpe(key, value) {
      const row = fields.get(`${key}:rpe`);
      if (!row) return;
      for (const b of row.children) b.classList.toggle("on", b.textContent === String(value));
    }

    /** The open rows of a card, in the order the keyboard walks them. */
    const walk = (card) => [
      ...card.rows.filter((b) => !card.logged.has(b.key)),
      ...card.extras.filter((b) => chosen.has(b.key)),
    ];

    /** Off this row and on to the next, or to the note if this was the last. */
    function advance(card, index) {
      const rows = walk(card);
      const next = rows[index + 1];
      if (!next) {
        const note = fields.get(`note:${card.slot}`);
        note?.focus();
        note?.select();
        return;
      }
      stage = { slot: card.slot, row: index + 1, field: next.choose.length ? "kind" : "rpe" };
      take();
    }

    async function submit(card) {
      const parts = partsFrom(card, draft, chosen);
      if (!parts.length) return void (message.textContent = "nothing to log");
      const missing = incomplete(parts);
      if (missing) return void (message.textContent = missing);
      try {
        put("health", await api.post("/api/session/card", { parts }));
        for (const part of parts) draft.delete(part.plan_key);
        draft.delete(`note:${card.slot}`);
        for (const block of card.extras) chosen.delete(block.key);
        stage = null;
        endCapture(OWNER);
      } catch (error) {
        message.textContent = `failed: ${error.message}`;
      }
    }

    // --- the keyboard --------------------------------------------------------

    const current = () => {
      const card = list.find((c) => c.slot === stage?.slot);
      return card ? { card, block: walk(card)[stage.row] } : {};
    };

    function label() {
      const { block } = current();
      return `plan ▸ ${block ? `${block.name} ${stage.field}` : "done"}`;
    }

    function paint() {
      for (const [, node] of fields) node.classList?.remove("on-row");
      const { block } = current();
      if (block && stage) fields.get(`${block.key}:${stage.field}`)?.classList.add("on-row");
    }

    function onEnd() {
      stage = null;
      paint();
    }

    function onKey(e) {
      const { card, block } = current();
      if (!card || !block) return false;

      if (stage.field === "kind" && block.choose.length) {
        const hit = block.choose.find((k) => k[0] === e.key);
        if (hit) {
          setDraft(block.key, { kind: hit });
          stage = { ...stage, field: "rpe" };
          notify("health");
          return true;
        }
      }

      if (/^[0-9]$/.test(e.key)) {
        const value = e.key === "0" ? 10 : Number(e.key);
        setDraft(block.key, { rpe: value });
        paintRpe(block.key, value);
        stage = null;
        endCapture(OWNER);
        const box = fields.get(`${block.key}:minutes`);
        box?.focus();
        box?.select();
        return true;
      }

      if (e.key === "Enter") {
        const box = fields.get(`${block.key}:minutes`);
        stage = null;
        endCapture(OWNER);
        box?.focus();
        box?.select();
        return true;
      }
      return false;
    }

    handlers = { onKey, paint, onEnd, label };

    const anchor = stage ? current().block : null;
    mount(node,
      ...list.map(cardNode),
      el("div.row.gap", null,
        message,
        anchor
          ? el("span.faint", { text: RPE_ANCHOR[draft.get(anchor.key)?.rpe] ?? "" })
          : null,
      ),
    );

    if (stage) take();
    else paint();
  },
};
