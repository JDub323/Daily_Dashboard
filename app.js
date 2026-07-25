"use strict";
// ============================================================================
// Daily Dashboard front-end.
// Layout: task chips on top, three columns (feeds | hero | metrics), modals
// for small tasks. All state lives in state.json via /api/state/merge —
// panels save ONLY the top-level keys they touched, so saves can't clobber
// each other (the old full-state POST was losing elo updates).
// ============================================================================

let STATE = null, CONFIG = null, TOPIC_INFO = null;
const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const localISO = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const today = () => localISO(new Date());

async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", body: JSON.stringify(body)} : {});
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  return j;
}

// POST that streams newline-delimited JSON: {status} lines then {problem}|{error}.
async function streamMath(onStatus) {
  const r = await fetch("/api/math/new", {method: "POST", body: "{}"});
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "", result = null;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buf += dec.decode(value, {stream: true});
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.status) onStatus(ev.status);
      else if (ev.problem) result = ev.problem;
      else if (ev.error) throw new Error(ev.error);
    }
  }
  if (!result) throw new Error("no problem returned");
  return result;
}

// merge-save: only the named top-level keys are written
async function save(...keys) {
  const patch = {};
  keys.forEach(k => patch[k] = STATE[k]);
  try {
    await api("/api/state/merge", patch);
  } catch (e) {
    toast(`save failed (${e.message}) — retrying…`);
    await new Promise(r => setTimeout(r, 1500));
    try { await api("/api/state/merge", patch); toast("saved ✓", true); }
    catch (e2) { toast(`SAVE FAILED: ${e2.message} — your last action may be lost`); }
  }
}

let toastTimer = null;
function toast(msg, info = false) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  t.className = info ? "info" : "";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.hidden = true, 4000);
}

function typeset(el) {
  if (window.renderMathInElement) renderMathInElement(el, {delimiters: [
    {left: "$$", right: "$$", display: true}, {left: "$", right: "$", display: false}]});
}

// ---- modal ----------------------------------------------------------------
function openModal(title, build) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  body.innerHTML = "";
  build(body);
  $("#modal-overlay").hidden = false;
}
function closeModal() { $("#modal-overlay").hidden = true; updateChips(); }

// ---- elo ------------------------------------------------------------------
// High K with little history (fast convergence early — you can't grind games),
// decaying toward a stable 24 as evidence accumulates.
const kFactor = n => Math.max(24, Math.round(96 / (1 + n / 8)));
function eloUpdate(rating, oppRating, score, n) {
  const exp = 1 / (1 + Math.pow(10, (oppRating - rating) / 400));
  return Math.round(rating + kFactor(n) * (score - exp));
}
function successRate(hist, n = 10) {
  const h = hist.slice(-n);
  return h.length ? Math.round(100 * h.filter(x => x.score > 0.5).length / h.length) : null;
}

// ---- charts ---------------------------------------------------------------
function sparkline(vals, w = 110, h = 26) {
  if (!vals || vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals), r = max - min || 1;
  const pts = vals.map((v, i) => `${(i/(vals.length-1)*w).toFixed(1)},${(h-2-(v-min)/r*(h-4)).toFixed(1)}`).join(" ");
  const up = vals[vals.length-1] >= vals[0];
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none"
          stroke="${up ? "var(--ok)" : "var(--bad)"}" stroke-width="1.5"/></svg>`;
}

function lineChart(vals, {w = 300, h = 80, color = "var(--gold)"} = {}) {
  if (!vals || vals.length < 2)
    return `<div class="faint">need ≥2 data points${vals && vals.length === 1 ? ` (now: ${vals[0]})` : ""}</div>`;
  const min = Math.min(...vals), max = Math.max(...vals), r = max - min || 1;
  const px = i => 34 + i/(vals.length-1)*(w-40);
  const py = v => 6 + (1-(v-min)/r)*(h-16);
  const pts = vals.map((v,i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  return `<svg width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="max-height:${h}px">
    <line x1="34" y1="${py(max)}" x2="${w}" y2="${py(max)}" stroke="var(--border-soft)" stroke-dasharray="3,3"/>
    <line x1="34" y1="${py(min)}" x2="${w}" y2="${py(min)}" stroke="var(--border-soft)" stroke-dasharray="3,3"/>
    <text x="30" y="${py(max)+3}" text-anchor="end">${max}</text>
    <text x="30" y="${py(min)+3}" text-anchor="end">${min}</text>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8"/>
    <circle cx="${px(vals.length-1)}" cy="${py(vals[vals.length-1])}" r="2.6" fill="${color}"/>
  </svg>`;
}

function streak(list) { // consecutive days with an entry, ending today or yesterday
  const days = new Set(list.map(e => e.date));
  let n = 0;
  const d = new Date();
  if (!days.has(localISO(d))) d.setDate(d.getDate() - 1); // today not done yet is ok
  for (;; d.setDate(d.getDate() - 1)) {
    if (days.has(localISO(d))) n++; else break;
  }
  return n;
}

// ============================================================================
// Task chips — the daily checklist. Gold = done.
// ============================================================================
function taskStatus() {
  const t = today();
  const w = STATE.word.byDate[t];
  const dueCards = STATE.theorems.cards.filter(c => c.due <= t).length;
  const tasks = [
    {id: "math",    label: "math problem",  done: STATE.math.current?.date === t && !!STATE.math.current?.done, open: () => scrollToPanel("math")},
    {id: "chess",   label: "chess puzzle",  done: STATE.chess.current?.date === t && !!STATE.chess.current?.done, open: () => scrollToPanel("chess")},
    {id: "word",    label: "word of day",   done: !!w?.result, open: openWordModal},
    {id: "wordle",  label: "wordle",        done: STATE.games.wordle.some(e => e.date === t), open: () => openGamesModal("wordle")},
    {id: "pokedoku",label: "pokedoku",      done: STATE.games.pokedoku.some(e => e.date === t), open: () => openGamesModal("pokedoku")},
    {id: "theorem", label: dueCards ? `theorems (${dueCards} due)` : "theorems", done: STATE.theorems.cards.length > 0 && dueCards === 0, open: openTheoremModal},
    {id: "paper",   label: "paper triage",  done: STATE.papers.current?.date === t && !!STATE.papers.current?.marked, open: () => scrollToPanel("reading")},
    {id: "log",     label: "day log",       done: !!STATE.daylog[t], open: openDaylogModal},
  ];
  return tasks;
}

function scrollToPanel(id) {
  const el = document.getElementById("panel-" + id);
  if (el) { el.scrollIntoView({behavior: "smooth", block: "center"});
            el.style.transition = "box-shadow .3s"; el.style.boxShadow = "0 0 0 2px var(--gold-dim)";
            setTimeout(() => el.style.boxShadow = "", 1200); }
}

function updateChips() {
  const tasks = taskStatus();
  const wrap = $("#tasks");
  wrap.innerHTML = tasks.map(t =>
    `<span class="chip ${t.done ? "done" : ""}" data-id="${t.id}">${esc(t.label)}</span>`).join("");
  tasks.forEach(t => { wrap.querySelector(`[data-id="${t.id}"]`).onclick = t.open; });
  const done = tasks.filter(t => t.done).length;
  $("#hdr-completion").innerHTML =
    `<span class="completion-ring">${done}/${tasks.length}</span> ${done === tasks.length ? "— perfect day ✦" : "done today"}`;
  // gold borders on hero panels
  for (const id of ["math", "chess"]) {
    const p = document.getElementById("panel-" + id);
    if (p) p.classList.toggle("done-gold", tasks.find(t => t.id === id).done);
  }
  // persist completion snapshot for the 100%-streak metric
  const pct = done / tasks.length;
  const prev = STATE.completion[today()];
  if (!prev || prev.pct !== pct) {
    STATE.completion[today()] = {pct, done, total: tasks.length};
    save("completion");
  }
  const metrics = PANELS.find(p => p.id === "metrics");
  if (metrics && metrics.el) metrics.render(metrics.el);
}

// ============================================================================
// Panels
// ============================================================================
const PANELS = [];
function rerender(id) { const p = PANELS.find(x => x.id === id); p.render(p.el); }

// ======================================================= chess (hero) ======
const PIECES = {p:"♟", n:"♞", b:"♝", r:"♜", q:"♛", k:"♚"};

PANELS.push({ id: "chess", title: "♞ chess puzzle", col: "center", hero: true, async render(el) {
  const c = STATE.chess;
  const rate = successRate(c.history);
  const head = `<div class="row"><span class="rating">${c.rating}</span>
    <span class="faint">elo · K=${kFactor(c.history.length)}${rate !== null ? ` · ${rate}% last ${Math.min(c.history.length,10)}` : ""}</span></div>`;

  const cur = c.current;
  const isToday = cur && cur.date === today();

  if (isToday && cur.done) {
    const last = c.history[c.history.length - 1];
    const delta = last && last.delta != null ? last.delta : null;
    el.innerHTML = head + `<div class="ok">done for today ✓
        ${delta != null ? `<span class="${delta >= 0 ? "delta-up" : "delta-down"}">${delta >= 0 ? "+" : ""}${delta}</span>` : ""}
        <a href="${esc(cur.url)}" target="_blank" class="faint">(${esc(cur.id)})</a></div>
      <div class="row"><button id="bonus">bonus puzzle (unrated)</button></div>`;
    $("#bonus", el).onclick = () => attempt(async () => {
      const p = await api("/api/chess/puzzle");
      el.innerHTML = head + `<div class="faint">unrated practice · puzzle rated ${p.rating}</div><div id="pz"></div>`;
      mountPuzzle($("#pz", el), p, null);
    }, el, "chess");
    return;
  }

  if (!isToday || !cur.pgn) {  // stale, missing, or old-format puzzle
    el.innerHTML = head + `<button id="new" class="primary">get today's puzzle (near ${c.rating})</button>`;
    $("#new", el).onclick = () => attempt(async () => {
      $("#new", el).disabled = true; $("#new", el).textContent = "fetching…";
      const p = await api("/api/chess/puzzle");
      c.current = {...p, date: today(), done: false};
      await save("chess");
      this.render(el);
    }, el, "chess");
    return;
  }

  el.innerHTML = head +
    `<div class="faint">puzzle <a href="${esc(cur.url)}" target="_blank">${esc(cur.id)}</a>
       <span class="tag">rated ${cur.rating}</span> · ${cur.themes.slice(0,4).map(esc).join(" · ")}</div>
     <div id="pz"></div>`;
  mountPuzzle($("#pz", el), cur, (score, mistakes) => {
    if (cur.done) return;
    const n = c.history.length;
    const before = c.rating;
    c.rating = eloUpdate(c.rating, cur.rating, score, n);
    c.history.push({date: cur.date, id: cur.id, opp: cur.rating, score,
                    mistakes, rating: c.rating, delta: c.rating - before});
    cur.done = true; cur.score = score;
    save("chess");
    this.render(el);
    rerender("elo");
    updateChips();
  });
}});

// Interactive puzzle board. onFinish(score, mistakes) — null for unrated.
function mountPuzzle(root, p, onFinish) {
  const game = new Chess();
  if (!game.load_pgn(p.pgn, {sloppy: true})) {
    root.innerHTML = `<div class="err">couldn't parse puzzle — solve it
      <a href="${esc(p.url)}" target="_blank">on lichess</a> instead</div>
      <div class="row">
        <button data-s="1" class="ok">solved clean</button>
        <button data-s="0.4">solved w/ mistakes</button>
        <button data-s="0" class="bad">failed</button></div>`;
    root.querySelectorAll("button[data-s]").forEach(b =>
      b.onclick = () => onFinish && onFinish(+b.dataset.s, 0));
    return;
  }
  const playerColor = game.turn();          // side to move = you
  let idx = 0, mistakes = 0, hints = 0, sel = null, lastMove = null, finished = false;

  root.innerHTML = `
    <div id="board-wrap">
      <div class="faint" id="pz-turn"></div>
      <div id="board"></div>
      <div class="row">
        <span id="pz-status" class="dim">your move — ${playerColor === "w" ? "white" : "black"} to play</span>
        <span style="flex:1"></span>
        <button id="pz-hint">hint</button>
        <button id="pz-giveup" class="bad">give up</button>
      </div>
    </div>`;
  const boardEl = $("#board", root), statusEl = $("#pz-status", root);

  const sqName = (f, r) => "abcdefgh"[f] + (r + 1);
  function draw(marks = {}) {
    const rows = game.board(); // rank 8 first
    let html = "";
    const order = playerColor === "w" ? [...Array(8).keys()] : [...Array(8).keys()].reverse();
    for (const ri of order) {                       // ri: 0 = rank 8
      const files = playerColor === "w" ? [...Array(8).keys()] : [...Array(8).keys()].reverse();
      for (const fi of files) {
        const sq = sqName(fi, 7 - ri);
        const pc = rows[ri][fi];
        const cls = ["sq", (fi + ri) % 2 ? "dark" : "light"];
        if (sel === sq) cls.push("sel");
        if (lastMove && (lastMove.from === sq || lastMove.to === sq)) cls.push("last");
        if (marks[sq]) cls.push(marks[sq]);
        html += `<div class="${cls.join(" ")}" data-sq="${sq}">` +
          (pc ? `<span class="piece-${pc.color}">${PIECES[pc.type]}</span>` : "") + `</div>`;
      }
    }
    boardEl.innerHTML = html;
    boardEl.querySelectorAll(".sq").forEach(d => d.onclick = () => click(d.dataset.sq));
  }

  function legalTargets(from) {
    const marks = {};
    for (const m of game.moves({square: from, verbose: true}))
      marks[m.to] = m.captured ? "cap" : "dot";
    return marks;
  }

  function click(sq) {
    if (finished || game.turn() !== playerColor) return;
    const pc = game.get(sq);
    if (sel && sel !== sq && !(pc && pc.color === playerColor)) { tryMove(sel, sq); return; }
    if (pc && pc.color === playerColor) { sel = sq; draw(legalTargets(sq)); return; }
    sel = null; draw();
  }

  function tryMove(from, to) {
    const expected = p.solution[idx] || "";
    const promo = expected.length > 4 ? expected[4] : "q";
    const mv = game.move({from, to, promotion: promo});
    sel = null;
    if (!mv) { draw(); return; }
    const uci = mv.from + mv.to + (mv.promotion || "");
    if (uci === expected || game.in_checkmate()) {   // lichess accepts alt mates
      idx++; lastMove = mv; draw();
      if (idx >= p.solution.length || game.in_checkmate()) return finish(true);
      statusEl.textContent = "✓ correct";
      setTimeout(() => {                             // opponent reply
        const r = p.solution[idx]; idx++;
        lastMove = game.move({from: r.slice(0,2), to: r.slice(2,4), promotion: r[4] || "q"});
        draw();
        statusEl.textContent = "your move";
        if (idx >= p.solution.length) finish(true);
      }, 350);
    } else {
      mistakes++;
      game.undo();
      draw({[to]: "wrong"});
      statusEl.innerHTML = `<span class="bad">✗ not it — try again</span> <span class="faint">(${mistakes} mistake${mistakes > 1 ? "s" : ""})</span>`;
    }
  }

  $("#pz-hint", root).onclick = () => {
    if (finished) return;
    const expected = p.solution[idx];
    if (!expected) return;
    hints++;
    draw({[expected.slice(0, 2)]: "dot"});
    statusEl.innerHTML = `<span class="dim">hint: move the piece on ${expected.slice(0,2)}</span>`;
  };

  $("#pz-giveup", root).onclick = () => {
    if (finished) return;
    finished = true;
    // play out the solution so you see it
    const playNext = () => {
      const r = p.solution[idx];
      if (!r) return;
      idx++;
      lastMove = game.move({from: r.slice(0,2), to: r.slice(2,4), promotion: r[4] || "q"});
      draw();
      setTimeout(playNext, 500);
    };
    playNext();
    statusEl.innerHTML = `<span class="bad">solution shown</span>`;
    onFinish && onFinish(0, mistakes);
  };

  function finish(solved) {
    if (finished) return;
    finished = true;
    const clean = solved && mistakes === 0 && hints === 0;
    const score = clean ? 1 : solved ? 0.4 : 0;
    statusEl.innerHTML = clean
      ? `<span class="gold">✦ solved clean!</span>`
      : `<span class="ok">solved</span> <span class="faint">(${mistakes} mistake${mistakes === 1 ? "" : "s"}${hints ? `, ${hints} hint${hints === 1 ? "" : "s"}` : ""})</span>`;
    $("#pz-hint", root).disabled = $("#pz-giveup", root).disabled = true;
    onFinish && onFinish(score, mistakes);
  }

  draw();
}

// ======================================================== math (hero) ======
PANELS.push({ id: "math", title: "∑ math problem of the day", col: "center", hero: true, async render(el) {
  const m = STATE.math;
  const rate = successRate(m.history);
  const head = `<div class="row"><span class="rating">${m.rating}</span>
    <span class="faint">elo · K=${kFactor(m.history.length)}${rate !== null ? ` · ${rate}% last ${Math.min(m.history.length,10)}` : ""}</span></div>`;
  if (m.current && !m.current.done && m.current.date !== today()) m.current = null; // stale, unfinished

  if (!m.current || m.current.date !== today()) {
    el.innerHTML = head + `<button id="new" class="primary">get today's problem</button>
      <div class="faint">topic: ${esc(CONFIG.topic)} · verified against AoPS via web search</div>`;
    $("#new", el).onclick = () => attempt(async () => {
      el.innerHTML = head + `<div class="row"><span class="spinner"></span>
        <span id="mstat" class="dim">starting…</span></div>`;
      m.current = await streamMath(s => { const st = $("#mstat", el); if (st) st.textContent = s; });
      STATE.math = (await api("/api/state")).math;   // server saved seen-list + current
      this.render(el);
    }, el, "math");
    return;
  }
  const p = m.current;
  if (p.done) {
    const last = m.history[m.history.length - 1];
    el.innerHTML = head + `<div class="ok">done for today ✓ (${esc(p.source)})
      ${last && last.delta != null ? `<span class="${last.delta >= 0 ? "delta-up" : "delta-down"}">${last.delta >= 0 ? "+" : ""}${last.delta}</span>` : ""}</div>
      <div id="ans" style="margin-top:6px">answer: ${p.answer}</div>`;
    typeset(el);
    return;
  }

  const topicLinks = TOPIC_INFO
    ? `<a href="${esc(TOPIC_INFO.textbook.url)}" target="_blank">${esc(TOPIC_INFO.textbook.title)}</a> · theorems: ` +
      TOPIC_INFO.theorems.map(t => `<a href="${esc(t.url)}" target="_blank">${esc(t.name)}</a>`).join(", ")
    : `<span class="faint">topic links loading…</span>`;
  const hints = [topicLinks, esc(p.hint1), esc(p.hint2), esc(p.hint3)];

  el.innerHTML = head +
    `<div class="faint">${esc(p.source)} · tier ${esc(p.tier)} (${p.anchor})${p.verified_url ? ` · <a href="${esc(p.verified_url)}" target="_blank">verified ✓</a>` : ""}</div>
     <div id="stmt" style="margin:8px 0">${p.statement}</div>
     <div id="hints"></div>
     <div class="row">
       <button id="hint">hint ${p.hints_revealed}/4</button>
       <button id="solved" class="ok">solved</button>
       <button id="failed" class="bad">couldn't solve</button>
     </div><div id="ans"></div>`;
  const hintsEl = $("#hints", el);
  for (let i = 0; i < p.hints_revealed; i++)
    hintsEl.innerHTML += `<div class="dim">▸ ${hints[i]}</div>`;
  typeset(el);
  $("#hint", el).onclick = () => {
    if (p.hints_revealed < 4) { p.hints_revealed++; save("math"); this.render(el); }
  };
  const finish = score => {
    if (p.done) return;                         // guard: no double-submits
    const before = m.rating;
    m.rating = eloUpdate(m.rating, p.anchor, score, m.history.length);
    m.history.push({date: p.date, source: p.source, score, hints: p.hints_revealed,
                    rating: m.rating, delta: m.rating - before});
    p.done = true;
    save("math");
    $("#solved", el).disabled = $("#failed", el).disabled = $("#hint", el).disabled = true;
    $("#ans", el).innerHTML = `<div style="margin-top:8px">answer: ${p.answer}
      <span class="${m.rating - before >= 0 ? "delta-up" : "delta-down"}">${m.rating - before >= 0 ? "+" : ""}${m.rating - before}</span>
      <span class="faint">→ ${m.rating}</span></div>`;
    typeset($("#ans", el));
    rerender("elo");
    updateChips();
  };
  $("#solved", el).onclick = () => finish(Math.max(0.25, 1 - 0.15 * p.hints_revealed));
  $("#failed", el).onclick = () => finish(0);
}});

// ============================================================ piano ========
const NOTES = ["C","C♯","D","D♯","E","F","F♯","G","G♯","A","A♯","B"];
const SCALES = [
  ["Dorian", [0,2,3,5,7,9,10], "minor with a bright 6th — 'So What' (Miles Davis), 'Scarborough Fair'"],
  ["Phrygian", [0,1,3,5,7,8,10], "dark, Spanish — flamenco and metal riffs live here"],
  ["Lydian", [0,2,4,6,7,9,11], "major with a raised 4th, floaty — The Simpsons theme"],
  ["Mixolydian", [0,2,4,5,7,9,10], "major with a flat 7th — 'Sweet Home Alabama', 'Norwegian Wood'"],
  ["Natural minor", [0,2,3,5,7,8,10], "the relative minor — 'Losing My Religion' (R.E.M.)"],
  ["Harmonic minor", [0,2,3,5,7,8,11], "raised 7th gives an exotic pull — 'Hava Nagila'"],
  ["Melodic minor", [0,2,3,5,7,9,11], "jazz minor — the sound over m(maj7) chords"],
  ["Major pentatonic", [0,2,4,7,9], "five notes, no wrong ones — 'My Girl' riff (Temptations)"],
  ["Minor pentatonic", [0,3,5,7,10], "the rock-solo scale — nearly every blues lick starts here"],
  ["Blues", [0,3,5,6,7,10], "minor pentatonic + flat 5 — 'Sunshine of Your Love' (Cream)"],
  ["Whole tone", [0,2,4,6,8,10], "dream-sequence blur — 'You Are the Sunshine of My Life' intro"],
  ["Half-whole diminished", [0,1,3,4,6,7,9,10], "jazz weapon over dom7♭9 chords"],
  ["Locrian", [0,1,3,5,6,8,10], "half-diminished tonic — theory candy, almost never home"],
];
const CHORDS = [
  ["maj7", "major seventh", [0,4,7,11], "'The Girl from Ipanema' — the opening Fmaj7"],
  ["m7", "minor seventh", [0,3,7,10], "every ii chord in a ii–V–I; 'So What' voicings"],
  ["7", "dominant seventh", [0,4,7,10], "'Johnny B. Goode' — the engine of the blues"],
  ["dim7", "diminished seventh", [0,3,6,9], "the villain sting; passing chord in 'Michelle' (Beatles)"],
  ["m7♭5", "half-diminished", [0,3,6,10], "'Autumn Leaves' — the minor ii chord"],
  ["aug", "augmented", [0,4,8], "'Oh! Darling' (Beatles) — the opening chord"],
  ["sus4", "suspended 4th", [0,5,7], "'Pinball Wizard' (The Who)"],
  ["sus2", "suspended 2nd", [0,2,7], "open and ambiguous — pop intros before the resolve"],
  ["6", "major sixth", [0,4,7,9], "the final chord of 'She Loves You' (Beatles)"],
  ["m6", "minor sixth", [0,3,7,9], "gypsy-jazz tonic; film-noir soundtracks"],
  ["add9", "added ninth", [0,4,7,14], "'Every Breath You Take' (Police) — those arpeggios"],
  ["9", "dominant ninth", [0,4,7,10,14], "James Brown funk vamps"],
  ["maj9", "major ninth", [0,4,7,11,14], "the neo-soul staple — dreamy resolve"],
  ["m9", "minor ninth", [0,3,7,10,14], "lo-fi and neo-soul ii chords"],
  ["13", "dominant thirteenth", [0,4,7,10,14,21], "the big-band final V chord"],
  ["7♯9", "the Hendrix chord", [0,4,7,10,15], "'Purple Haze'"],
  ["mMaj7", "minor-major seventh", [0,3,7,11], "the James Bond theme's final chord"],
];

function pianoSVG(rootIdx, intervals) {
  const hl = new Set(intervals.map(iv => (rootIdx + iv) % 12));
  const KW = 20, KH = 58, BW = 12, BH = 36, NW = 14;      // 2 octaves = 14 white keys
  const whiteSemis = [0,2,4,5,7,9,11];
  let whites = "", blacks = "";
  for (let i = 0; i < NW; i++) {
    const sem = whiteSemis[i % 7];
    const cls = hl.has(sem) ? (sem === rootIdx ? "pk-root" : "pk-hl") : "pk-white";
    whites += `<rect class="pk-white ${cls === "pk-white" ? "" : cls}" x="${i*KW}" y="0" width="${KW}" height="${KH}" rx="2"/>`;
    if (hl.has(sem))
      whites += `<text x="${i*KW + KW/2}" y="${KH - 8}" text-anchor="middle" fill="#14171f" style="font-weight:700">${NOTES[sem]}</text>`;
    if (sem !== 4 && sem !== 11) {                          // black key follows
      const bsem = (sem + 1) % 12;
      const bcls = hl.has(bsem) ? (bsem === rootIdx ? "pk-root" : "pk-hl") : "pk-black";
      blacks += `<rect class="pk-black ${bcls === "pk-black" ? "" : bcls}" x="${(i+1)*KW - BW/2}" y="0" width="${BW}" height="${BH}" rx="2"/>`;
    }
  }
  return `<svg viewBox="0 0 ${NW*KW} ${KH}" xmlns="http://www.w3.org/2000/svg">${whites}${blacks}</svg>`;
}

PANELS.push({ id: "piano", title: "♪ piano of the day", col: "right", async render(el) {
  const day = Math.floor(new Date(today() + "T12:00:00").getTime() / 864e5);
  const [sName, sIv, sNote] = SCALES[day % SCALES.length];
  const sRoot = (day * 7) % 12;                     // ×7 cycles all 12 keys
  const [cSym, cName, cIv, cSong] = CHORDS[day % CHORDS.length];
  const cRoot = (day * 5 + 3) % 12;
  const noteList = (root, ivs) => ivs.map(iv => NOTES[(root + iv) % 12]).join(" – ");
  el.innerHTML = `
    <div class="piano-item">
      <div class="row" style="justify-content:space-between;margin:0">
        <span class="gold" style="font-weight:600">${NOTES[sRoot]} ${sName}</span>
        <span class="faint">scale</span></div>
      <div id="piano-wrap">${pianoSVG(sRoot, sIv)}</div>
      <div class="dim" style="font-size:11.5px">${noteList(sRoot, sIv)}</div>
      <div class="faint">${esc(sNote)}</div>
    </div>
    <div class="piano-item" style="margin-top:10px;border-top:1px solid var(--border-soft);padding-top:8px">
      <div class="row" style="justify-content:space-between;margin:0">
        <span class="gold" style="font-weight:600">${NOTES[cRoot]}${cSym}</span>
        <span class="faint">${esc(cName)}</span></div>
      <div id="piano-wrap2">${pianoSVG(cRoot, cIv)}</div>
      <div class="dim" style="font-size:11.5px">${noteList(cRoot, cIv)}</div>
      <div class="faint">${esc(cSong)}</div>
    </div>`;
}});

// ==================================================== calendar (left) ======
PANELS.push({ id: "cal", title: "calendar", col: "left", async render(el) {
  el.innerHTML = `<div class="faint">loading gcalcli via wsl…</div>`;
  const addBox = `<div class="row"><input id="q" placeholder='quick add: "essay due Fri 5pm"'>
    <button id="add">add</button></div><div id="addmsg" class="faint"></div>`;
  try {
    const c = await api("/api/calendar");
    const fmt = ev => `<li><span class="dim">${esc(ev.start || "all day")}</span> ${esc(ev.title)}</li>`;
    el.innerHTML =
      `<div class="big">today</div><ul class="clean">${c.today.map(fmt).join("") || "<li class='faint'>nothing scheduled</li>"}</ul>
       <div class="big">this week</div><ul class="clean">${
         c.week.map(ev => `<li><span class="dim">${esc(ev.date)}</span> ${esc(ev.title)}</li>`).join("") || "<li class='faint'>clear</li>"
       }</ul>` + addBox;
  } catch (e) {
    el.innerHTML = `<div class="err">calendar: ${esc(e.message)}</div>
      <div class="row"><button onclick="rerender('cal')">retry</button></div>` + addBox;
  }
  $("#add", el).onclick = async () => {
    const q = $("#q", el).value.trim(); if (!q) return;
    $("#addmsg", el).textContent = "adding…";
    try { await api("/api/calendar/add", {text: q}); this.render(el); }
    catch (e) { $("#addmsg", el).textContent = "failed: " + e.message; }
  };
}});

// ===================================================== markets (left) ======
PANELS.push({ id: "mkt", title: "markets", col: "left", async render(el) {
  el.innerHTML = `<div class="faint">fetching quotes…</div>`;
  const rows = await api("/api/markets");
  el.innerHTML = rows.map(r => (r.error || typeof r.price !== "number")
    ? `<div class="row"><b>${esc(r.ticker)}</b> <span class="err">${esc(r.error || "no data")}</span></div>`
    : `<div class="row" style="justify-content:space-between">
         <span><b>${esc(r.ticker)}</b> <span class="big">${r.price.toFixed(2)}</span>
         <span class="${r.change_pct >= 0 ? "ok" : "bad"}">${r.change_pct >= 0 ? "+" : ""}${r.change_pct}%</span></span>
         ${sparkline(r.spark)}</div>`).join("");
}});

// ======================================================== news (left) ======
PANELS.push({ id: "news", title: "headlines", col: "left", async render(el) {
  el.innerHTML = `<div class="faint">fetching feeds…</div>`;
  const items = await api("/api/news");
  el.innerHTML = `<ul class="clean">` + items.map(i =>
    `<li><span class="tag">${esc(i.cat)}</span> <a href="${esc(i.link)}" target="_blank">${esc(i.title)}</a></li>`
  ).join("") + `</ul>`;
}});

// ============================================= reading: paper+queue ========
PANELS.push({ id: "reading", title: "reading", col: "left", async render(el) {
  const P = STATE.papers;
  // yesterday's unmarked paper auto-queues
  if (P.current && P.current.date !== today() && !P.current.marked) {
    P.queue.push({title: P.current.title, link: P.current.link, added: P.current.date});
    P.current = null; await save("papers");
  }
  if (!P.current || P.current.date !== today()) {
    el.innerHTML = `<div class="faint">fetching arXiv…</div>`;
    const p = await api("/api/paper");
    P.current = {...p, date: today(), marked: false}; await save("papers");
  }
  const p = P.current;
  const Q = P.queue;
  const qItem = (q, i) =>
    `<li><span class="faint">${i+1}.</span> <a href="${esc(q.link)}" target="_blank">${esc(q.title)}</a>
       <span class="row" style="display:inline-flex;gap:4px;margin:0">
         <button data-a="up" data-i="${i}">↑</button>
         <button data-a="read" data-i="${i}" class="ok">read</button>
         <button data-a="drop" data-i="${i}" class="bad">×</button></span></li>`;
  el.innerHTML =
    `<div class="tag">${p.fresh ? "new" : "new-ish"} — ${esc(p.published)}</div>
     <div style="margin:6px 0"><a href="${esc(p.link)}" target="_blank">${esc(p.title)}</a></div>
     <div class="dim" style="font-size:12px">${esc(p.summary)}…</div>
     <div class="row" style="margin:8px 0">${p.marked
       ? `<span class="ok">marked: ${esc(p.marked)}</span>`
       : `<button id="read">read it</button><button id="queue">→ queue</button><button id="skip">skip</button>`}</div>
     <div class="faint" style="border-top:1px solid var(--border-soft);padding-top:6px">queue · ${P.archive.length} cleared</div>
     <ul class="clean">${Q.map(qItem).join("") || "<li class='faint'>empty</li>"}</ul>
     <div class="row"><input id="url" placeholder="paste a link to queue it"><button id="addq">add</button></div>`;
  const mark = async v => {
    p.marked = v;
    if (v === "queued") Q.push({title: p.title, link: p.link, added: today()});
    await save("papers"); this.render(el); updateChips();
  };
  if (!p.marked) {
    $("#read", el).onclick = () => mark("read");
    $("#queue", el).onclick = () => mark("queued");
    $("#skip", el).onclick = () => mark("skipped");
  }
  el.querySelectorAll("button[data-a]").forEach(b => b.onclick = async () => {
    const i = +b.dataset.i, a = b.dataset.a;
    if (a === "up" && i > 0) [Q[i-1], Q[i]] = [Q[i], Q[i-1]];
    if (a === "read") P.archive.push({...Q.splice(i,1)[0], cleared: today()});
    if (a === "drop") Q.splice(i,1);
    await save("papers"); this.render(el);
  });
  $("#addq", el).onclick = async () => {
    const u = $("#url", el).value.trim(); if (!u) return;
    Q.push({title: u.replace(/^https?:\/\//,""), link: u, added: today()});
    await save("papers"); this.render(el);
  };
}});

// ======================================================= elo (right) =======
PANELS.push({ id: "elo", title: "elo history", col: "right", async render(el) {
  const ch = STATE.chess.history.map(h => h.rating).filter(r => r != null);
  const mh = STATE.math.history.map(h => h.rating).filter(r => r != null);
  el.innerHTML = `
    <div class="chart-block"><div class="chart-title"><span>♞ chess</span>
      <span class="gold">${STATE.chess.rating}</span></div>
      ${lineChart([1400, ...ch].slice(-40), {color: "var(--gold)"})}</div>
    <div class="chart-block"><div class="chart-title"><span>∑ math</span>
      <span class="gold">${STATE.math.rating}</span></div>
      ${lineChart([1500, ...mh].slice(-40), {color: "var(--accent)"})}</div>`;
}});

// ============================================== trophies & streaks =========
function wordleGrid(grid) {
  if (!grid || !grid.length) return "";
  const cls = {"🟩": "g", "🟨": "y", "⬛": "", "⬜": ""};
  return `<span class="wgrid">` + grid.map(line =>
    Array.from(line).map(ch => `<span class="cell ${cls[ch] ?? ""}"></span>`).join("")).join("") + `</span>`;
}
function pokedokuGrid(grid) {
  if (!grid || !grid.length) return "";
  const cls = ch => ("✅🟩".includes(ch)) ? "g" : ("❌".includes(ch)) ? "r" : "w";
  return `<span class="pgrid">` + grid.map(line =>
    Array.from(line).map(ch => `<span class="cell ${cls(ch)}"></span>`).join("")).join("") + `</span>`;
}

PANELS.push({ id: "metrics", title: "trophies & streaks", col: "right", async render(el) {
  const g = STATE.games;
  const t = today();
  const wToday = g.wordle.find(e => e.date === t);
  const pToday = g.pokedoku.find(e => e.date === t);
  const wAvg = g.wordle.filter(e => e.guesses).map(e => e.guesses);
  const perfectDays = Object.entries(STATE.completion).filter(([,v]) => v.pct === 1)
    .map(([date]) => ({date}));
  el.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <span>wordle streak</span><span class="gold big">${streak(g.wordle)}</span></div>
    <div class="row" style="justify-content:space-between">
      <span>pokedoku streak</span><span class="gold big">${streak(g.pokedoku)}</span></div>
    <div class="row" style="justify-content:space-between">
      <span>100% days streak</span><span class="gold big">${streak(perfectDays)}</span></div>
    <div class="row" style="justify-content:space-between">
      <span class="faint">wordle avg</span><span class="faint">${wAvg.length ? (wAvg.reduce((a,b)=>a+b,0)/wAvg.length).toFixed(2) : "—"} / 6</span></div>
    <div style="border-top:1px solid var(--border-soft);margin-top:8px;padding-top:8px">
      <div class="faint">today's results</div>
      ${wToday ? `<div>${wordleGrid(wToday.grid)}<span class="dim">wordle ${wToday.won ? wToday.guesses + "/6" : "X/6"}</span></div>` : `<div class="faint">wordle — not yet</div>`}
      ${pToday ? `<div>${pokedokuGrid(pToday.grid)}<span class="dim">pokedoku ${pToday.correct}/9${pToday.score ? " · " + pToday.score : ""}</span></div>` : `<div class="faint">pokedoku — not yet</div>`}
    </div>`;
}});

// ====================================================== health (right) =====
PANELS.push({ id: "health", title: "health", col: "right", async render(el) {
  const H = STATE.health;
  const entry = (list, label, id, unit, color) => {
    const vals = list.slice(-30).map(e => e.v);
    const last = list[list.length - 1];
    return `<div class="chart-block">
      <div class="chart-title"><span>${label}</span>
        <span class="gold">${last ? last.v + unit : "—"}</span></div>
      ${lineChart(vals, {color})}
      <div class="row"><input id="${id}" type="number" step="0.1" placeholder="log today's ${label}">
        <button data-log="${id}">log</button></div></div>`;
  };
  el.innerHTML =
    entry(H.weight, "weight", "wt", " lb", "var(--gold)") +
    entry(H.sleep, "sleep score", "sl", "", "var(--accent)") +
    `<div class="faint">manual for now — Apple Health import can slot in here later</div>`;
  el.querySelectorAll("button[data-log]").forEach(b => b.onclick = async () => {
    const id = b.dataset.log;
    const v = parseFloat($("#" + id, el).value);
    if (isNaN(v)) return;
    const list = id === "wt" ? H.weight : H.sleep;
    const t = today();
    const existing = list.find(e => e.date === t);
    if (existing) existing.v = v; else list.push({date: t, v});
    await save("health"); this.render(el);
  });
}});

// ============================================================ modals =======
function openWordModal() {
  openModal("word of the day", async body => {
    const w = STATE.word.byDate[today()] ||= {};
    const rebuild = () => { body.innerHTML = ""; build(); };
    const build = () => {
      if (!w.word) {
        body.innerHTML = `<button id="get" class="primary">get today's word</button>`;
        $("#get", body).onclick = async () => {
          $("#get", body).textContent = "asking…"; $("#get", body).disabled = true;
          try {
            Object.assign(w, await api("/api/word/today", {}));
            await save("word"); rebuild();
          } catch (e) { body.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
        };
        return;
      }
      if (w.result) {
        body.innerHTML = `<div class="big gold">${esc(w.word)}</div><div class="dim">${esc(w.definition)}</div>
          <div class="${w.result.distinct ? "ok" : "bad"}" style="margin-top:6px">${esc(w.result.verdict)}</div>
          <ul class="clean">${w.result.per_sentence.map((r,i) =>
            `<li><span class="${r.ok ? "ok" : "bad"}">${r.ok ? "✓" : "✗"}</span> ${esc(w.sentences[i])} <span class="faint">${esc(r.note)}</span></li>`).join("")}</ul>
          ${!w.result.distinct ? `<div class="row"><button id="retry">edit & retry</button></div>` : ""}`;
        const rb = $("#retry", body);
        if (rb) rb.onclick = () => { delete w.result; save("word"); rebuild(); };
        return;
      }
      body.innerHTML = `<div class="big gold">${esc(w.word)} <span class="faint">${esc(w.pos)}</span></div>
        <div class="dim">${esc(w.definition)}</div>
        <div class="faint" style="margin:8px 0 4px">write five semantically distinct sentences:</div>` +
        [0,1,2,3,4].map(i => `<input class="s" style="margin:3px 0" value="${esc(w.sentences?.[i] || "")}">`).join("") +
        `<div class="row"><button id="check" class="primary">check</button><span id="msg" class="faint"></span></div>`;
      body.querySelectorAll(".s").forEach((inp, i) => inp.onchange = () => {
        (w.sentences ||= ["","","","",""])[i] = inp.value; save("word");
      });
      $("#check", body).onclick = async () => {
        const s = [...body.querySelectorAll(".s")].map(i => i.value.trim());
        if (s.some(x => !x)) { $("#msg", body).textContent = "need all five"; return; }
        $("#msg", body).textContent = "judging…";
        try {
          w.sentences = s;
          w.result = await api("/api/word/check", {word: w.word, sentences: s});
          await save("word"); rebuild(); updateChips();
        } catch (e) { $("#msg", body).textContent = "failed: " + e.message; }
      };
    };
    build();
  });
}

function openGamesModal(focus) {
  openModal(focus === "wordle" ? "wordle" : "pokedoku", body => {
    const g = STATE.games;
    const t = today();
    const wToday = g.wordle.find(e => e.date === t);
    const pToday = g.pokedoku.find(e => e.date === t);
    body.innerHTML = `
      <div class="row">
        <a href="https://www.nytimes.com/games/wordle" target="_blank">play wordle ↗</a>
        <a href="https://pokedoku.com" target="_blank">play pokedoku ↗</a>
      </div>
      <div style="margin:6px 0">
        ${wToday ? `<div>${wordleGrid(wToday.grid)}<span class="dim">wordle #${esc(String(wToday.n ?? ""))} ${wToday.won ? wToday.guesses + "/6" : "X/6"} ✓</span></div>` : ""}
        ${pToday ? `<div>${pokedokuGrid(pToday.grid)}<span class="dim">pokedoku ${pToday.correct}/9 ✓</span></div>` : ""}
      </div>
      <div class="faint">paste your share text (auto-detects which game):</div>
      <textarea id="paste" rows="8" placeholder="Wordle 1,496 4/6&#10;⬛🟨⬛⬛⬛&#10;…"></textarea>
      <div class="row"><button id="log" class="primary">parse & log</button><span id="msg" class="faint"></span></div>`;
    $("#log", body).onclick = async () => {
      const txt = $("#paste", body).value, msg = $("#msg", body);
      let m;
      if ((m = txt.match(/Wordle\s+([\d,.]+)\s+(X|\d)\/6/i))) {
        const grid = txt.split("\n").map(l => l.trim())
          .filter(l => Array.from(l).length === 5 && Array.from(l).every(c => "⬛⬜🟨🟩".includes(c)));
        const entry = {date: t, n: m[1], guesses: m[2].toUpperCase() === "X" ? null : +m[2],
                       won: m[2].toUpperCase() !== "X", grid};
        const i = g.wordle.findIndex(e => e.date === t);
        if (i >= 0) g.wordle[i] = entry; else g.wordle.push(entry);
        msg.textContent = `wordle #${m[1]} logged ✓`;
      } else if (/pokedoku/i.test(txt)) {
        const grid = txt.split("\n").map(l => l.trim())
          .filter(l => Array.from(l).length === 3 && Array.from(l).every(c => "✅❌⬜🟩🟥".includes(c)));
        const hits = (txt.match(/✅|🟩/gu) || []).length;
        const score = (txt.match(/score[:\s]+(\d+)/i) || [])[1];
        const entry = {date: t, correct: hits, score: score ? +score : null, grid};
        const i = g.pokedoku.findIndex(e => e.date === t);
        if (i >= 0) g.pokedoku[i] = entry; else g.pokedoku.push(entry);
        msg.textContent = `pokedoku logged (${hits}/9) ✓`;
      } else { msg.textContent = "couldn't parse that — paste the share text"; return; }
      await save("games");
      rerender("metrics"); updateChips();
      openGamesModal(focus);   // re-render modal with the trophy
    };
  });
}

function openTheoremModal() {
  openModal("theorem recall", body => {
    const T = STATE.theorems;
    const due = T.cards.filter(c => c.due <= today()).sort((a,b) => a.due < b.due ? -1 : 1);
    const newBtn = `<div class="row" style="margin-top:10px"><button id="new">+ card from this week's topic</button><span id="nmsg" class="faint"></span></div>`;
    if (!due.length) {
      body.innerHTML = `<div class="faint">no cards due · ${T.cards.length} in deck</div>` + newBtn;
    } else {
      const c = due[0];
      body.innerHTML = `<div class="big gold">${esc(c.name)}</div>
        <div class="faint">${esc(c.topic)} · ${due.length} due · try to recall it, then reveal</div>
        <div class="row"><button id="reveal" class="primary">reveal</button></div><div id="tbody"></div>` + newBtn;
      $("#reveal", body).onclick = () => {
        $("#tbody", body).innerHTML = `<div style="margin:8px 0">${c.statement}</div>
          <div class="row">${[1,2,3,4].map(q =>
            `<button class="g" data-q="${q}">${["blank","hard","good","easy"][q-1]}</button>`).join("")}</div>`;
        typeset($("#tbody", body));
        body.querySelectorAll(".g").forEach(b => b.onclick = () => {
          const q = +b.dataset.q;   // SM-2-lite
          c.ease = Math.max(1.3, (c.ease || 2.5) + (q - 3) * 0.15);
          c.interval = q === 1 ? 1 : Math.round((c.interval || 1) * (q === 2 ? 1.2 : c.ease));
          const d = new Date(); d.setDate(d.getDate() + c.interval);
          c.due = localISO(d);
          save("theorems"); openTheoremModal();
        });
      };
    }
    $("#new", body).onclick = async () => {
      $("#nmsg", body).textContent = "asking…";
      try {
        const card = await api("/api/theorem/new", {});
        T.cards.push({...card, due: today(), interval: 1, ease: 2.5});
        await save("theorems"); openTheoremModal();
      } catch (e) { $("#nmsg", body).textContent = "failed: " + e.message; }
    };
  });
}

function openDaylogModal() {
  openModal("day log", body => {
    const ago = n => { const d = new Date(); d.setDate(d.getDate()-n); return localISO(d); };
    const echo = [[7, "a week ago"], [30, "a month ago"]]
      .map(([n, lbl]) => STATE.daylog[ago(n)] ? `<div class="faint">${lbl}: ${esc(STATE.daylog[ago(n)])}</div>` : "")
      .join("");
    body.innerHTML = echo +
      `<input id="line" placeholder="one line about today" value="${esc(STATE.daylog[today()] || "")}">
       <div class="faint" id="msg" style="margin-top:4px">${STATE.daylog[today()] ? "saved ✓" : ""}</div>`;
    $("#line", body).onchange = async e => {
      STATE.daylog[today()] = e.target.value;
      await save("daylog");
      $("#msg", body).textContent = "saved ✓"; updateChips();
    };
  });
}

// ========================================================= bootstrap =======
async function attempt(fn, el, id) {
  try { await fn(); } catch (e) {
    el.innerHTML = `<div class="err">${esc(e.message)}</div>
      <div class="row"><button onclick="rerender('${id}')">retry</button></div>`;
  }
}

async function boot() {
  $("#hdr-date").textContent = new Date().toDateString();
  $("#modal-close").onclick = closeModal;
  $("#modal-overlay").onclick = e => { if (e.target.id === "modal-overlay") closeModal(); };
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !$("#modal-overlay").hidden) closeModal();
  });
  try { CONFIG = await api("/api/config"); $("#hdr-topic").textContent = "topic: " + CONFIG.topic; }
  catch { CONFIG = {topic: "?", tickers: []}; }
  STATE = await api("/api/state");
  api("/api/topic").then(t => { TOPIC_INFO = t; }).catch(() => {});  // non-blocking

  for (const p of PANELS) {
    const card = document.createElement("section");
    card.className = "panel" + (p.hero ? " hero" : "");
    card.id = "panel-" + p.id;
    card.innerHTML = `<h2>${p.title}</h2><div class="body"></div>`;
    document.getElementById("col-" + p.col).appendChild(card);
    const orig = p.render.bind(p);
    p.render = el => Promise.resolve(orig(el)).catch(e =>
      el.innerHTML = `<div class="err">${esc(e.message)}</div>
        <div class="row"><button onclick="rerender('${p.id}')">retry</button></div>`);
    p.el = $(".body", card);
    p.render(p.el);
  }
  updateChips();
}
window.addEventListener("DOMContentLoaded", boot);
