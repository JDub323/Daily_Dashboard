#!/usr/bin/env python3
"""Daily Dashboard — single-file local server.
Run:  python dashboard.py   (serves http://localhost:8502 and opens a tab)
"""
import json, os, re, time, queue, subprocess, threading, urllib.request, urllib.parse, webbrowser
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================================
# CONFIG — edit everything here
# ============================================================================
PORT = int(os.environ.get("PORT") or 8502)
TOPIC_OF_WEEK = "generating functions"          # weekly math topic
TICKERS = ["^SPX", "AAPL", "MSFT", "NVDA"]      # markets panel
LLM_MODEL = "claude-sonnet-5"
PROXY_PREFIX = "https://login.proxy.library.nd.edu/login?url="
PAYWALLED = ["wsj.com", "ft.com", "nytimes.com", "economist.com",
             "bloomberg.com", "washingtonpost.com"]
NEWS_FEEDS = [  # (category, url)
    ("uspolitics", "https://rss.politico.com/politics-news.xml"),
    ("uspolitics", "https://feeds.npr.org/1014/rss.xml"),
    ("world",      "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("finance",    "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("finance",    "https://www.ft.com/rss/home"),
    ("other",      "https://feeds.arstechnica.com/arstechnica/index"),
]
ARXIV_QUERY = ('(cat:cs.AI OR cat:cs.LG OR cat:cs.CL) AND '
               '(abs:"language model" OR abs:JEPA OR abs:"joint embedding" OR '
               'abs:"game playing" OR abs:poker OR abs:"imperfect information" OR '
               'abs:"branching factor" OR abs:"reinforcement learning" AND abs:game)')
# Fixed Elo anchors per problem tier — the LLM picks a tier, never a number,
# so the math rating scale can't drift.
MATH_ANCHORS = {"amc": 1100, "aime-early": 1300, "aime-late": 1500,
                "olympiad-easy": 1650, "putnam-early": 1750,
                "olympiad-hard": 1900, "putnam-late": 2050}

# Approx pricing ($ per token) for the retrieval-efficiency log. Sonnet 5 is at
# intro pricing through 2026-08-31; update when it reverts to $3/$15.
PRICING = {"claude-sonnet-5": {"in": 2e-6, "out": 10e-6},
           "claude-opus-4-8": {"in": 5e-6, "out": 25e-6},
           "claude-haiku-4-5": {"in": 1e-6, "out": 5e-6}}
WEB_SEARCH_COST = 0.01  # $ per web search

# Per-topic theorem lists + free textbook links. Hand-curated so the LLM never
# has to regenerate them; unknown topics get generated once and cached in state.
TOPIC_LIBRARY = {
    "generating functions": {
        "textbook": {"title": "generatingfunctionology (Wilf) — free full PDF",
                     "url": "https://www2.math.upenn.edu/~wilf/gfologyLinked2.pdf"},
        "theorems": [
            {"name": "Stars and Bars", "url": "https://en.wikipedia.org/wiki/Stars_and_bars_(combinatorics)"},
            {"name": "Generalized Binomial Theorem", "url": "https://en.wikipedia.org/wiki/Binomial_series"},
            {"name": "Generating Functions (overview + linear recurrences)", "url": "https://en.wikipedia.org/wiki/Generating_function"},
            {"name": "Partial Fraction Decomposition", "url": "https://en.wikipedia.org/wiki/Partial_fraction_decomposition"},
            {"name": "Catalan Numbers via GFs", "url": "https://en.wikipedia.org/wiki/Catalan_number"},
            {"name": "Exponential Generating Functions", "url": "https://en.wikipedia.org/wiki/Generating_function#Exponential_generating_function_(EGF)"},
        ],
    },
}
# ============================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "state.json")
PROBLEMS_PATH = os.path.join(ROOT, "problems.json")
METRICS_PATH = os.path.join(ROOT, "metrics.jsonl")  # one JSON line per retrieval
LOCK = threading.Lock()
CACHE = {}  # name -> (timestamp, value); serves stale on refetch failure

def load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

DEFAULT_STATE = {
    "math": {"rating": 1500, "history": [], "seen": [], "current": None},
    "chess": {"rating": 1400, "history": [], "current": None},
    "word": {"byDate": {}},
    "games": {"wordle": [], "pokedoku": []},
    "theorems": {"cards": []},
    "daylog": {},
    "papers": {"current": None, "queue": [], "archive": []},
    "health": {"weight": [], "sleep": []},
    "completion": {},
    "topics": {},
}

def _backfill_ratings(hist, base, cur):
    """Old history entries have no post-game rating; interpolate so charts work."""
    missing = [h for h in hist if "rating" not in h]
    if not missing:
        return
    n = len(hist)
    for i, h in enumerate(hist):
        if "rating" not in h:
            h["rating"] = round(base + (cur - base) * (i + 1) / n)

def _migrate(s):
    for k, v in DEFAULT_STATE.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    _backfill_ratings(s["math"]["history"], 1500, s["math"]["rating"])
    _backfill_ratings(s["chess"]["history"], 1400, s["chess"]["rating"])
    return s

def _load_unlocked():
    if os.path.exists(STATE_PATH):
        try:
            return _migrate(json.load(open(STATE_PATH, encoding="utf-8")))
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))

def _save_unlocked(s):
    tmp = STATE_PATH + ".tmp"
    json.dump(s, open(tmp, "w", encoding="utf-8"), indent=2)
    os.replace(tmp, STATE_PATH)

def load_state():
    with LOCK:
        return _load_unlocked()

def save_state(s):
    with LOCK:
        _save_unlocked(s)

def merge_state(patch):
    """Top-level key merge — panels save only what they touched, so concurrent
    saves from different panels can't clobber each other."""
    with LOCK:
        s = _load_unlocked()
        for k, v in patch.items():
            s[k] = v
        _save_unlocked(s)
        return s

def cached(name, ttl, fn):
    now = time.time()
    ent = CACHE.get(name)
    if ent and now - ent[0] < ttl:
        return ent[1]
    try:
        v = fn()
    except Exception:
        if ent:  # serve stale rather than crash the panel
            return ent[1]
        raise
    CACHE[name] = (now, v)
    return v

def http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (daily-dashboard)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def llm(system, user, max_tokens=16000, tools=None):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing from .env")
    messages = [{"role": "user", "content": user}]
    for _ in range(5):  # pause_turn continuation loop (server-side tools)
        payload = {"model": LLM_MODEL, "max_tokens": max_tokens,
                   "system": system, "messages": messages}
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.load(r)
        if out.get("stop_reason") == "pause_turn":
            messages = [{"role": "user", "content": user},
                        {"role": "assistant", "content": out["content"]}]
            continue
        txt = "".join(b.get("text", "") for b in out["content"] if b.get("type") == "text")
        if not txt:
            raise RuntimeError(f"LLM returned no text (stop_reason={out.get('stop_reason')})")
        return txt
    raise RuntimeError("LLM kept pausing (too many web searches?)")

def _clean_blocks(blocks):
    # assistant content to replay on pause_turn continuation: keep text +
    # paired server-tool blocks; drop thinking (empty text under omitted display
    # can be rejected) and internal accumulator keys.
    out = []
    for b in blocks:
        if b.get("type") in ("thinking", "redacted_thinking"):
            continue
        out.append({k: v for k, v in b.items() if not k.startswith("_")})
    return out

def llm_stream(system, user, tools=None, max_tokens=16000, on_status=None):
    """Streaming Messages call. Keeps the socket warm (no idle timeout on long
    agentic runs), reports progress via on_status(str), and returns
    (text, metrics). metrics tracks tokens, web searches, and pause continuations."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing from .env")
    def status(s):
        if on_status:
            on_status(s)
    messages = [{"role": "user", "content": user}]
    m = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_creation": 0,
         "web_searches": 0, "pause_continuations": 0}
    for _ in range(6):
        payload = {"model": LLM_MODEL, "max_tokens": max_tokens, "stream": True,
                   "system": system, "messages": messages}
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        blocks, cur, stop_reason = [], None, None
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                t = ev.get("type")
                if t == "message_start":
                    u = ev["message"].get("usage", {})
                    m["input_tokens"] += u.get("input_tokens", 0)
                    m["cache_read"] += u.get("cache_read_input_tokens", 0)
                    m["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                    status("thinking about a problem…")
                elif t == "content_block_start":
                    cb = ev["content_block"]; cur = {"type": cb["type"]}
                    if cb["type"] == "text":
                        cur["text"] = ""; status("writing it up…")
                    elif cb["type"] == "thinking":
                        cur["thinking"] = ""
                    elif cb["type"] == "server_tool_use":
                        cur.update({"id": cb.get("id"), "name": cb.get("name"),
                                    "input": cb.get("input", {}), "_partial": ""})
                        if cb.get("name") == "web_search":
                            m["web_searches"] += 1
                            status(f"verifying against AoPS… (search {m['web_searches']})")
                    elif cb["type"] == "web_search_tool_result":
                        cur.update({"tool_use_id": cb.get("tool_use_id"),
                                    "content": cb.get("content")})
                        status("checking the source…")
                    else:
                        cur.update({k: v for k, v in cb.items()})
                elif t == "content_block_delta":
                    d = ev.get("delta", {})
                    if d.get("type") == "text_delta":
                        cur["text"] = cur.get("text", "") + d.get("text", "")
                    elif d.get("type") == "thinking_delta":
                        cur["thinking"] = cur.get("thinking", "") + d.get("thinking", "")
                    elif d.get("type") == "input_json_delta":
                        cur["_partial"] = cur.get("_partial", "") + d.get("partial_json", "")
                elif t == "content_block_stop":
                    if cur is not None:
                        if cur.get("type") == "server_tool_use" and cur.get("_partial"):
                            try: cur["input"] = json.loads(cur["_partial"])
                            except Exception: pass
                        blocks.append(cur); cur = None
                elif t == "message_delta":
                    stop_reason = ev.get("delta", {}).get("stop_reason", stop_reason)
                    m["output_tokens"] += ev.get("usage", {}).get("output_tokens", 0)
        if stop_reason == "pause_turn":
            m["pause_continuations"] += 1
            messages = [{"role": "user", "content": user},
                        {"role": "assistant", "content": _clean_blocks(blocks)}]
            status("still verifying…")
            continue
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise RuntimeError(f"LLM returned no text (stop_reason={stop_reason})")
        return text, m
    raise RuntimeError("LLM kept pausing (too many web searches?)")

def est_cost(model, m):
    p = PRICING.get(model, {"in": 3e-6, "out": 15e-6})
    return round(m["input_tokens"] * p["in"] + m["output_tokens"] * p["out"]
                 + m["web_searches"] * WEB_SEARCH_COST, 4)

def log_metrics(rec):
    try:
        with open(METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass

def llm_json(system, user, **kw):
    txt = llm(system, user, **kw)
    m = re.search(r"\{.*\}|\[.*\]", txt, re.S)
    raw = m.group(0) if m else txt
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # LaTeX in responses often has singly-escaped backslashes — repair
        return json.loads(re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", raw))

def proxify(link):
    if any(d in link for d in PAYWALLED):
        return PROXY_PREFIX + urllib.parse.quote(link, safe="")
    return link

# ---------------------------------------------------------------- panels ----

def get_news():
    import xml.etree.ElementTree as ET
    by_cat = {}
    for cat, url in NEWS_FEEDS:
        try:
            root = ET.fromstring(http_get(url).encode())
            for item in root.iter("item"):
                t = item.findtext("title") or ""
                l = (item.findtext("link") or "").strip()
                if t and l:
                    by_cat.setdefault(cat, []).append({"title": t.strip(), "link": proxify(l), "cat": cat})
        except Exception:
            continue
    picks, quotas = [], [("uspolitics", 2), ("world", 1), ("finance", 1), ("other", 1)]
    for cat, n in quotas:
        picks += by_cat.get(cat, [])[:n]
    if not picks:
        raise RuntimeError("all news feeds failed")
    return picks

def _fetch_markets():
    out, any_ok = [], False
    for t in TICKERS:
        sym = "^GSPC" if t == "^SPX" else t  # yahoo's S&P symbol
        row = None
        for host in ("query1", "query2"):
            try:
                d = json.loads(http_get(
                    f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                    "?range=1mo&interval=1d",
                    headers={"Accept": "application/json"}))
                res = d["chart"]["result"][0]
                closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None][-30:]
                last = res["meta"].get("regularMarketPrice") or closes[-1]
                prev = res["meta"].get("regularMarketPreviousClose") or closes[-2]
                row = {"ticker": t, "price": last,
                       "change_pct": round((last - prev) / prev * 100, 2),
                       "spark": closes}
                any_ok = True
                break
            except Exception as e:
                row = {"ticker": t, "error": str(e)[:80]}
        out.append(row)
    if not any_ok:
        raise RuntimeError("all tickers failed: " + (out[0].get("error") or "?"))
    return out

def get_markets():
    return cached("markets", 600, _fetch_markets)

def wsl_gcalcli(args):
    cmd = "gcalcli --nocolor " + args
    # server may run on Windows (shell through wsl) or inside WSL (call directly)
    prefix = ["wsl", "-e"] if os.name == "nt" else []
    r = subprocess.run(prefix + ["bash", "-lc", cmd],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or "gcalcli failed")
    return r.stdout

def _fetch_calendar():
    today = dt.date.today()
    week_end = (today + dt.timedelta(days=7)).isoformat()
    day = wsl_gcalcli(f"agenda '{today} 00:00' '{today} 23:59' --tsv")
    week = wsl_gcalcli(f"agenda '{today}' '{week_end}' --tsv")
    def parse(tsv):
        ev = []
        for line in tsv.strip().splitlines():
            p = line.split("\t")
            if p and p[0] == "start_date":  # gcalcli tsv header row
                continue
            if len(p) >= 5:
                ev.append({"date": p[0], "start": p[1], "end": p[3], "title": p[4]})
        return ev
    return {"today": parse(day), "week": parse(week)}

def get_calendar():
    return cached("calendar", 300, _fetch_calendar)

def get_chess_puzzle(rating):
    # anonymous difficulties are offsets from ~1500: easiest -600 … hardest +600
    diff = ("easiest" if rating < 1050 else "easier" if rating < 1350 else
            "normal" if rating < 1650 else "harder" if rating < 1950 else "hardest")
    d = json.loads(http_get(f"https://lichess.org/api/puzzle/next?difficulty={diff}"))
    p, g = d["puzzle"], d["game"]
    return {"id": p["id"], "rating": p["rating"], "themes": p.get("themes", []),
            "url": f"https://lichess.org/training/{p['id']}",
            "pgn": g.get("pgn", ""), "initialPly": p.get("initialPly"),
            "solution": p.get("solution", [])}

def get_paper():
    def fetch():
        url = ("http://export.arxiv.org/api/query?search_query=" +
               urllib.parse.quote(ARXIV_QUERY) +
               "&sortBy=submittedDate&sortOrder=descending&max_results=20")
        import xml.etree.ElementTree as ET
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(http_get(url).encode())
        papers = []
        for e in root.findall("a:entry", ns):
            papers.append({
                "title": re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip(),
                "link": e.findtext("a:id", "", ns),
                "summary": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip()[:400],
                "published": e.findtext("a:published", "", ns)[:10],
            })
        if not papers:
            raise RuntimeError("arXiv returned nothing")
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        fresh = [p for p in papers if p["published"] >= yesterday]
        return (fresh or papers)[0] | {"fresh": bool(fresh)}
    return cached("paper", 3600, fetch)

def get_topic_info():
    """Theorem list + textbook for the current topic — hand-curated when
    possible, LLM-generated once and cached in state otherwise."""
    if TOPIC_OF_WEEK in TOPIC_LIBRARY:
        return {"topic": TOPIC_OF_WEEK, **TOPIC_LIBRARY[TOPIC_OF_WEEK]}
    state = load_state()
    if TOPIC_OF_WEEK in state["topics"]:
        return {"topic": TOPIC_OF_WEEK, **state["topics"][TOPIC_OF_WEEK]}
    info = llm_json(
        'Respond ONLY JSON: {"textbook":{"title":"...","url":"link to a FREE online textbook on the topic"},'
        '"theorems":[{"name":"...","url":"..."} x6, the most important theorems/techniques for the topic]}',
        f"Topic: {TOPIC_OF_WEEK}. Give the canonical free textbook and six key theorems with reference links.")
    state["topics"][TOPIC_OF_WEEK] = info
    merge_state({"topics": state["topics"]})
    return {"topic": TOPIC_OF_WEEK, **info}

WEB_SEARCH_TOOL = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}]

def new_math_problem(state, on_status=None):
    rating = state["math"]["rating"]
    weekday = dt.date.today().weekday()  # 0=Mon
    seen = state["math"]["seen"][-200:]
    anchors = json.dumps(MATH_ANCHORS)
    sys_p = ("You select real competition math problems and VERIFY them with web search before answering. "
             "Steps: pick a candidate problem, then search (Art of Problem Solving wiki or official archives) "
             "to confirm (a) the problem really appeared in the cited competition, (b) your statement matches "
             "the original exactly, and (c) the answer is correct. If verification fails, pick another problem "
             "and verify again. After verifying, respond ONLY with JSON:\n"
             '{"source":"e.g. Putnam 1998 B3","tier":one of the tier keys,'
             '"statement":"problem in LaTeX ($..$ inline, $$..$$ display), matching the original",'
             '"answer":"final answer, LaTeX ok",'
             '"hint1":"direction, under five words","hint2":"minor hint","hint3":"major hint",'
             '"verified_url":"the URL where you confirmed the problem"}')
    user_p = (f"Topic of the week: {TOPIC_OF_WEEK}. Today is weekday {weekday} (0=Mon: ramp difficulty "
              f"through the week, but vary sub-skills to teach the topic broadly). "
              f"My rating: {rating}. Tier anchors: {anchors}. Pick a tier whose anchor gives me a "
              f"30-70% solve chance, adjusted for weekday. Must be from a real past competition. "
              f"Do NOT use any of these already-seen problems: {json.dumps(seen)}")
    t0 = time.time()
    metrics, prob, err = None, None, None
    try:
        text, metrics = llm_stream(sys_p, user_p, tools=WEB_SEARCH_TOOL, on_status=on_status)
        mm = re.search(r"\{.*\}", text, re.S)
        raw = mm.group(0) if mm else text
        try:
            prob = json.loads(raw)
        except json.JSONDecodeError:
            prob = json.loads(re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", raw))
    except Exception as e:
        err = str(e)[:300]
        raise
    finally:
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
               "date": dt.date.today().isoformat(), "model": LLM_MODEL,
               "topic": TOPIC_OF_WEEK, "rating": rating,
               "wall_seconds": round(time.time() - t0, 1),
               "ok": prob is not None, "error": err}
        if prob:
            rec.update({"source": prob.get("source"), "tier": prob.get("tier")})
        if metrics:
            rec.update(metrics)
            rec["est_cost_usd"] = est_cost(LLM_MODEL, metrics)
        log_metrics(rec)
    if on_status:
        on_status("done ✓")
    if prob.get("tier") not in MATH_ANCHORS:
        prob["tier"] = "aime-late"
    prob["anchor"] = MATH_ANCHORS[prob["tier"]]
    prob["date"] = dt.date.today().isoformat()
    prob["hints_revealed"] = 0
    # append to hand-editable problems file
    plist = json.load(open(PROBLEMS_PATH, encoding="utf-8")) if os.path.exists(PROBLEMS_PATH) else []
    plist.append(prob)
    json.dump(plist, open(PROBLEMS_PATH, "w", encoding="utf-8"), indent=2)
    with LOCK:
        s = _load_unlocked()
        s["math"]["seen"].append(prob["source"])
        s["math"]["current"] = prob
        _save_unlocked(s)
    return prob

def word_today():
    seed = dt.date.today().isoformat()
    return llm_json(
        'Respond ONLY JSON: {"word":"...","pos":"...","definition":"..."}',
        f"Pick an uncommon-but-usable English word of the day for {seed}. "
        f"Deterministic-ish: base the choice on that date.")

def word_check(word, sentences):
    return llm_json(
        'You grade vocabulary practice. Respond ONLY JSON: '
        '{"distinct":bool,"per_sentence":[{"ok":bool,"note":"short"} x5],"verdict":"one line"}',
        f'Word: "{word}". Are these five sentences each correct usages AND semantically '
        f"distinct from each other (different senses/contexts, not rewordings)?\n" +
        "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences)))

def theorem_card():
    return llm_json(
        'Respond ONLY JSON: {"name":"...","statement":"LaTeX ok","topic":"..."}',
        f"Give one important theorem or definition from the topic '{TOPIC_OF_WEEK}' "
        f"suitable for a spaced-repetition recall card.")

# ---------------------------------------------------------------- server ----

STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/chess.js": ("chess.js", "application/javascript; charset=utf-8")}

def math_new_events():
    """Run generation in a worker; yield {status:…} lines, then the final
    {problem:…} or {error:…}. Lets the handler stream progress to the browser."""
    q = queue.Queue()
    result = {}
    def work():
        try:
            result["problem"] = new_math_problem(load_state(), on_status=lambda s: q.put({"status": s}))
        except Exception as e:
            result["error"] = str(e)[:300]
        finally:
            q.put(None)
    threading.Thread(target=work, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            break
        yield item
    yield {"problem": result["problem"]} if "problem" in result else {"error": result.get("error", "unknown")}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _stream_ndjson(self, gen):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for ev in gen:
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()
        except Exception:
            pass  # client disconnected mid-stream

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _api(self, fn):
        try:
            self._send(200, fn())
        except Exception as e:
            self._send(500, {"error": str(e)[:300]})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in STATIC:
            fname, ctype = STATIC[path]
            self._send(200, open(os.path.join(ROOT, fname), "rb").read(), ctype)
        elif path == "/api/config":
            self._send(200, {"topic": TOPIC_OF_WEEK, "tickers": TICKERS})
        elif path == "/api/topic":
            self._api(get_topic_info)
        elif path == "/api/state":
            self._send(200, load_state())
        elif path == "/api/news":     self._api(lambda: cached("news", 900, get_news))
        elif path == "/api/markets":  self._api(get_markets)
        elif path == "/api/calendar": self._api(get_calendar)
        elif path == "/api/paper":    self._api(get_paper)
        elif path == "/api/chess/puzzle":
            self._api(lambda: get_chess_puzzle(load_state()["chess"]["rating"]))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":            # full replace — legacy, avoid
            save_state(body); self._send(200, {"ok": True})
        elif path == "/api/state/merge":    # top-level key merge — preferred
            merge_state(body); self._send(200, {"ok": True})
        elif path == "/api/math/new":
            self._stream_ndjson(math_new_events())
        elif path == "/api/word/today":     self._api(word_today)
        elif path == "/api/word/check":
            self._api(lambda: word_check(body["word"], body["sentences"]))
        elif path == "/api/theorem/new":    self._api(theorem_card)
        elif path == "/api/calendar/add":
            text = body["text"].replace("'", "'\\''")
            self._api(lambda: {"out": wsl_gcalcli(f"quick '{text}'")} )
        else:
            self._send(404, {"error": "not found"})

def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Daily Dashboard → {url}   (Ctrl+C to stop)")
    def open_tab():
        if os.name != "nt" and os.path.exists("/mnt/c/Windows/System32/cmd.exe"):
            subprocess.run(["/mnt/c/Windows/System32/cmd.exe", "/c", "start", url],
                           cwd="/mnt/c", capture_output=True)
        else:
            webbrowser.open(url)
    threading.Timer(0.6, open_tab).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
