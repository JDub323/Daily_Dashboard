// The daily debate: read, collect facts, take the prompt, paste the ballot.
//
// The argument itself happens in the Claude app — this panel is the scaffolding
// and the scorekeeper. That split is deliberate and is what makes the feature
// free: the expensive part runs on a subscription, and everything here is a
// string being assembled or a ballot being parsed.
//
// Four states, drawn from one payload the way the math panel draws three:
//
//   unopened   the week's issue and sub-area, and a button that freezes them
//   prep       the readings, the facts box, and what you believe going in
//   briefed    the prompt, a copy button, and somewhere to paste the ballot
//   scored     the eight lines, what was voided, and what it cost or earned
//
// [Invariant] Opening the session is a button, not something that happens on
// render. Opening freezes the angle, the side and the reading list, and a panel
// that opened on mount would burn a session every morning the politics tab was
// drawn — which is every morning. Same rule as the theorem draw.

import * as api from "../api.js";
import { promptBlock as copyBlock } from "../clipboard.js";
import { el, link, mount } from "../dom.js";
import { put, store } from "../store.js";
import { toast } from "../ui.js";

const STANCES = [
  ["for", "supports my side"],
  ["against", "cuts against me"],
  ["context", "context"],
];

/**
 * What you have typed but not submitted, kept across re-renders.
 *
 * The panel is redrawn by any store change — a feed landing, a chip update, a
 * rollover check — and each redraw builds fresh inputs. Without this, half a
 * pasted ballot disappears because something unrelated finished loading.
 *
 * Keyed by date, so tomorrow's session starts empty rather than inheriting a
 * fact you meant to log against yesterday's issue.
 */
let draft = { date: null, claim: "", source: "", stance: "context", position: "", ballot: "" };

function drafted() {
  const date = api.today();
  if (draft.date !== date) {
    draft = { date, claim: "", source: "", stance: "context", position: "", ballot: "" };
  }
  return draft;
}

// --- shared bits -----------------------------------------------------------

/** Rating, K, and the per-issue strip. The debate track's version of the math
 *  header, and read the same way: the last issue in the row is the weak one. */
function header(panel) {
  const { rating, k_next, measured_count, success_rate } = panel.summary;
  const detail = [`elo · K=${k_next}`];
  if (success_rate !== null) detail.push(`${success_rate}% last ${Math.min(measured_count, 10)}`);

  const strip = panel.issues.length
    ? el("div.areas", null,
        ...panel.issues.map((issue) =>
          el(`span.area${issue.area === panel.issue ? ".area-live" : ""}`, {
            title: issue.events
              ? `${issue.area}: ${issue.events} debate${issue.events === 1 ? "" : "s"}`
              : `${issue.area}: nothing yet — showing your overall debate rating`,
          },
            el("span.faint", { text: `${issue.area} ` }),
            el(`span${issue.events ? "" : ".faint"}`, { text: issue.rating }),
          ),
        ),
      )
    : null;

  return [
    // The hero figure, as on math and chess — see `summaryRow` in math.js.
    el("div.figure-row", null,
      el("span.figure", { text: rating }),
      el("span.figure-label", { text: detail.join(" · ") }),
    ),
    strip,
  ];
}

/** Issue · sub-area · today's angle. The line that says what today *is*. */
function subject(panel) {
  return el("div", null,
    el("div.big.gold", { text: panel.subarea ?? "—" }),
    el("div.faint", {
      text: panel.reading_day
        ? `${panel.issue_name} · reading day — no debate, just the file`
        : `${panel.issue_name} · ${panel.angle_label ?? "—"}`,
    }),
  );
}

function headlines(session) {
  if (!session.readings.length) {
    return el("div.faint.gap", {
      text: "nothing on this issue in today's feeds — argue from the dossier, or add a "
        + "feed for it in config.toml",
    });
  }
  return el("div", null,
    el("div.faint.gap", { text: "today's headlines" }),
    el("ul.clean", null,
      ...session.readings.map((r) => el("li", null, link(r.link, r.title))),
    ),
  );
}

/**
 * The week's assigned reading: required articles, then optional, then books.
 *
 * Shown every day of the week rather than only on the reading day. The prompt
 * quotes what you have ticked and the opponent opens by quizzing you on it, so
 * a piece you finish on Thursday still needs somewhere to be ticked — and
 * Thursday is exactly when you would otherwise be tempted to claim Sunday's
 * list was read.
 */
function weekReading(panel, redraw) {
  if (!panel.reading.length) {
    return panel.reading_day
      ? el("div.faint.gap", {
          text: "no reading list for this week — the overnight task has not left one. "
            + "Today's headlines and the dossier are what there is.",
        })
      : null;
  }

  const tick = (item) =>
    // `.read` in the selector rather than a `class` prop: `el` passes a class
    // string to classList.add, and an empty one throws rather than being
    // ignored — which is a whole panel that fails to render for the sake of a
    // tick box that has not been ticked.
    el(`button.tiny${item.read_on ? ".read" : ""}`, {
      text: item.read_on ? "✓" : "○",
      title: item.read_on ? `read ${item.read_on} — click to untick` : "mark as read",
      onclick: async () => {
        put("debate", await api.post("/api/debate/read", {
          reading_id: item.id,
          read: !item.read_on,
        }));
        redraw();
      },
    });

  const article = (item) =>
    el(`li.reading${item.read_on ? ".read" : ""}`, null,
      tick(item),
      " ",
      item.url ? link(item.url, item.title) : el("span", { text: item.title }),
      item.required ? el("span.tag", { text: "required" }) : null,
      item.publisher ? el("span.faint", { text: ` ${item.publisher}` }) : null,
      item.summary ? el("div.dim.small", { text: item.summary }) : null,
    );

  // A book is a standing offer, not a task, so it gets no tick box — the
  // schema will not let one be required and the panel should not imply it is.
  const book = (item) =>
    el("li.reading", null,
      el("span.tag", { text: item.lean }),
      " ",
      item.url ? link(item.url, item.title) : el("span", { text: item.title }),
      item.author ? el("span.faint", { text: ` — ${item.author}` }) : null,
      item.summary ? el("div.dim.small", { text: item.summary }) : null,
    );

  const articles = panel.reading.filter((r) => r.kind === "article");
  const books = panel.reading.filter((r) => r.kind === "book");

  return el("div.gap", null,
    el("div.faint", {
      text: panel.unread_required
        ? `this week's reading · ${panel.unread_required} required piece(s) left`
        : "this week's reading · all required pieces read ✓",
    }),
    articles.length ? el("ul.clean", null, ...articles.map(article)) : null,
    books.length
      ? el("div", null,
          el("div.faint.gap", { text: "books · optional, one from each side" }),
          el("ul.clean", null, ...books.map(book)),
        )
      : null,
  );
}

/**
 * The fact box: a claim, where it came from, and which way it cuts.
 *
 * The source is required by the request model, not merely by this form. A claim
 * you cannot attribute is one the `evidence` line of the ballot will find out
 * about in half an hour, and the box is where that is cheap to fix.
 */
function factBox(panel, redraw) {
  const held = drafted();
  const claim = el("input", {
    value: held.claim,
    placeholder: "a fact — \"CBO scored it at $x over ten years\"",
    oninput: (e) => { held.claim = e.target.value; },
  });
  const source = el("input", {
    value: held.source,
    placeholder: "where it came from — a URL, or \"Borjas, ch. 4\"",
    oninput: (e) => { held.source = e.target.value; },
  });
  const stance = el("select", { onchange: (e) => { held.stance = e.target.value; } },
    ...STANCES.map(([value, label]) =>
      el("option", { value, text: label, selected: held.stance === value }),
    ),
  );
  const message = el("span.faint");

  const add = el("button", {
    text: "log it",
    onclick: async () => {
      if (claim.value.trim().length < 3 || source.value.trim().length < 4) {
        message.textContent = "both boxes — a claim without a source is not a fact yet";
        return;
      }
      add.disabled = true;
      try {
        put("debate", await api.post("/api/debate/fact", {
          claim: claim.value.trim(),
          source: source.value.trim(),
          stance: stance.value,
        }));
        draft = { ...drafted(), claim: "", source: "" };
        redraw();
      } catch (error) {
        add.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  const logged = panel.facts_today.map((fact) =>
    el("li", null,
      el("span.tag", { text: fact.stance }),
      ` ${fact.claim} `,
      el("span.faint", { text: `— ${fact.source}` }),
      " ",
      el("button.tiny", {
        text: "✕",
        title: "remove",
        onclick: async () => {
          put("debate", await api.post("/api/debate/fact/delete", { fact_id: fact.id }));
          redraw();
        },
      }),
    ),
  );

  return el("div", null,
    el("div.faint.gap", {
      text: `facts · ${panel.facts_today.length} today, ${panel.facts_total} on this issue`
        + ` · ${panel.min_facts} needed`,
    }),
    logged.length ? el("ul.clean", null, ...logged) : null,
    el("div.row", null, claim),
    el("div.row", null, source, stance, add, message),
  );
}

/** What you believe, before arguing — and after, once it is over. */
function positionBox(session, { after }) {
  const held = drafted();
  const stored = after ? session.position_after : session.position;
  const storedConfidence = after ? session.confidence_after : session.confidence;

  if (stored) {
    return el("div.gap", null,
      el("div.faint", { text: after ? "where you landed" : "where you started" }),
      el("div.dim", { text: `${stored}${storedConfidence ? ` · ${storedConfidence}/100` : ""}` }),
    );
  }

  const text = el("input", {
    value: after ? "" : held.position,
    placeholder: after
      ? "did arguing it move you? Say so even if it did not."
      : "what do you actually think, in one line?",
    oninput: (e) => { if (!after) held.position = e.target.value; },
  });
  const confidence = el("input.minutes", {
    type: "number", min: 0, max: 100, placeholder: "0-100",
    title: "how sure you are, 0-100",
  });
  const save = el("button", {
    text: "save",
    onclick: async () => {
      if (text.value.trim().length < 3) return;
      save.disabled = true;
      put("debate", await api.post("/api/debate/position", {
        position: text.value.trim(),
        confidence: confidence.value ? Number(confidence.value) : null,
        after,
      }));
      held.position = "";
    },
  });

  return el("div.gap", null,
    el("div.faint", {
      text: after
        ? "where you landed — the only line here that tracks being right rather than persuasive"
        : "where you start · stated before you read, so it can be checked against later",
    }),
    el("div.row", null, text),
    el("div.row", null, confidence, save),
  );
}

// --- the states ------------------------------------------------------------

function unopened(panel, redraw) {
  const open = el("button.primary", {
    text: panel.reading_day ? "open the week" : "open today's session",
    onclick: async () => {
      open.disabled = true;
      open.textContent = "fetching the reading…";
      try {
        put("debate", await api.post("/api/debate/open"));
        redraw();
      } catch (error) {
        open.disabled = false;
        open.textContent = "open today's session";
        toast(error.message);
      }
    },
  });
  return el("div", null,
    subject(panel),
    panel.angle_brief ? el("div.dim.gap", { text: panel.angle_brief }) : null,
    el("div.row", null, open),
    el("div.faint", {
      text: "opening freezes today's angle, side and reading list — a reload will not reroll them",
    }),
  );
}

/** The prompt, and the button that puts it on the clipboard.
 *  The fallback chain lives in `clipboard.js` — the trick panel needs it too. */
function promptBlock(panel) {
  return copyBlock(panel.prompt).node;
}

function prep(panel, redraw) {
  const session = panel.session;
  const ready = panel.facts_today.length >= panel.min_facts;

  const brief = el("button.primary", {
    text: "take the prompt →",
    disabled: !ready,
    title: ready ? "" : `log ${panel.min_facts} fact(s) first`,
    onclick: async () => {
      brief.disabled = true;
      put("debate", await api.post("/api/debate/brief"));
      redraw();
    },
  });

  return el("div", null,
    subject(panel),
    motionLine(session),
    el("div.dim.gap", { text: panel.angle_brief ?? "" }),
    sideLine(session),
    weekReading(panel, redraw),
    headlines(session),
    positionBox(session, { after: false }),
    factBox(panel, redraw),
    el("div.row.divider", null,
      brief,
      el("span.faint", {
        text: ready
          ? `${panel.grace_days === 1 ? "you have the rest of tomorrow" : `${panel.grace_days} days`} to paste the ballot once you take it`
          : `${panel.min_facts - panel.facts_today.length} more fact(s) — the prompt argues from them`,
      }),
    ),
  );
}

/**
 * The resolution, when the overnight task wrote one.
 *
 * Nothing is drawn for a template frame — "US policy on X" restates the
 * sub-area already above it, and a line that adds nothing teaches you to skip
 * the ones that do. The `overnight` tag is there so a morning where the task
 * did not run is visibly different rather than quietly worse.
 */
function motionLine(session) {
  if (session.motion_source !== "batch") return null;
  return el("div.motion.gap", null,
    el("div", null,
      el("span.tag", { text: "overnight" }),
      " ",
      el("span", { text: session.motion }),
    ),
    session.motion_note
      ? el("div.dim.small", null,
          session.motion_note,
          session.motion_url ? " " : null,
          session.motion_url ? link(session.motion_url, "source ↗") : null,
        )
      : null,
  );
}

function sideLine(session) {
  if (session.side === "con") {
    return el("div.gap", null,
      el("span.tag", { text: "against your own position" }),
      el("span.faint", {
        text: " · assigned, not chosen — rated as the harder job",
      }),
    );
  }
  return el("div.faint.gap", { text: "your own side today" });
}

function briefed(panel, redraw) {
  const held = drafted();
  const box = el("textarea.recall-box", {
    rows: 4,
    value: held.ballot,
    placeholder: "paste the whole ```json ballot from the end of the debate",
    oninput: (e) => { held.ballot = e.target.value; },
  });
  const message = el("span.faint");

  const submit = el("button.primary", {
    text: "score it",
    onclick: async () => {
      if (!box.value.trim()) {
        message.textContent = "paste the ballot first";
        return;
      }
      submit.disabled = true;
      message.textContent = "scoring…";
      try {
        put("debate", await api.post("/api/debate/verdict", { text: box.value }));
        draft = { ...drafted(), ballot: "" };
        redraw();
      } catch (error) {
        submit.disabled = false;
        message.textContent = error.message;
      }
    },
  });

  return el("div", null,
    subject(panel),
    motionLine(panel.session),
    sideLine(panel.session),
    promptBlock(panel),
    weekReading(panel, redraw),
    headlines(panel.session),
    el("div.faint.divider", { text: "the ballot" }),
    box,
    el("div.row", null, submit, message),
    factBox(panel, redraw),
  );
}

/** Graded: the eight lines, the fouls, and what it did to the rating. */
function scored(panel) {
  const session = panel.session;
  const card = session.scorecard ?? { items: [], fouls: [], voided: [], verdict: "" };
  const byKey = new Map(card.items.map((item) => [item.key, item]));
  const delta = panel.delta;
  const sign = delta > 0 ? "+" : "";

  const line = (spec) => {
    const item = byKey.get(spec.key);
    const score = item?.score ?? 0;
    const voided = card.voided.includes(spec.key);
    return el("div.ballot-line", null,
      el("div.row.spread", null,
        el("span", { text: spec.label, title: spec.asks }),
        el(`span.${score === 2 ? "ok" : score === 1 ? "gold" : "bad"}`, { text: `${score}/2` }),
      ),
      item?.note ? el("div.dim.small", { text: item.note }) : null,
      // Said out loud rather than silently applied: a rule you cannot see
      // enforced is one you assume is not being enforced.
      voided ? el("div.err.small", { text: "voided — claimed a point with no quotation" }) : null,
    );
  };

  return el("div", null,
    el("div.row.spread", null,
      subject(panel),
      el(`span.${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "faint"}`, {
        text: delta == null ? "" : delta === 0 ? "no change" : `${sign}${delta}`,
        title: `${panel.issue} rating`,
      }),
    ),
    session.status === "lapsed"
      ? el("div.err.gap", { text: "lapsed — briefed and never scored, which costs what a loss costs" })
      : el("div.faint.gap", {
          text: `${Math.round(session.score * 16)}/16 · ${session.format}`
            + `${session.contra ? " · against your own position" : ""}`,
        }),
    ...panel.rubric.map(line),
    card.fouls.length
      ? el("div.divider", null,
          el("div.faint", { text: "fouls" }),
          ...card.fouls.map((foul) =>
            el("div.err.small", { text: `${foul.kind}${foul.quote ? ` — “${foul.quote}”` : ""}` }),
          ),
        )
      : null,
    card.verdict ? el("div.dim.divider", { text: card.verdict }) : null,
    positionBox(session, { after: true }),
  );
}

// --- the panel -------------------------------------------------------------

export default {
  id: "debate",
  title: "⚖ debate of the day",
  tab: "politics",
  slot: "hero",
  hero: true,
  collapsible: true,

  summary() {
    const panel = store.state.debate;
    if (!panel.enabled) return "off";
    const session = panel.session;
    if (!session) return `${panel.summary.rating}`;
    // A reading day never scores, so its one line is the reading rather than a
    // rating that did not move.
    if (panel.reading_day) {
      return panel.reading.length
        ? `read ${panel.reading.filter((r) => r.read_on).length}/${panel.reading.filter((r) => r.required).length} · ${panel.facts_today.length} fact(s)`
        : `${panel.facts_today.length} fact(s)`;
    }
    if (session.score == null) return `${panel.summary.rating} · ${session.status}`;
    const delta = panel.delta == null ? "" : ` ${panel.delta >= 0 ? "+" : ""}${panel.delta}`;
    return `${panel.summary.rating}${delta} · ${Math.round(session.score * 16)}/16`;
  },

  async render(node) {
    const panel = store.state.debate;
    const redraw = () => this.render(node);

    if (!panel.enabled) {
      mount(node, el("div.faint", {
        text: "no issues configured — add a [[debate.issues]] table to config.toml",
      }));
      return;
    }

    const session = panel.session;
    let body;
    if (!session) body = unopened(panel, redraw);
    else if (session.status === "scored" || session.status === "lapsed") body = scored(panel);
    else if (session.status === "briefed") body = briefed(panel, redraw);
    else if (panel.reading_day) {
      body = el("div", null,
        subject(panel),
        weekReading(panel, redraw),
        headlines(session),
        positionBox(session, { after: false }),
        factBox(panel, redraw),
      );
    } else body = prep(panel, redraw);

    mount(node, ...header(panel), body);
  },
};
