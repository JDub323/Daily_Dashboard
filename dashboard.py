#!/usr/bin/env python3
"""Daily Dashboard — single-file local server.
Run:  python dashboard.py   (serves http://localhost:8501 and opens a tab)
"""
import json, os, re, csv, io, subprocess, threading, urllib.request, urllib.parse, webbrowser
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
# ============================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "state.json")
PROBLEMS_PATH = os.path.join(ROOT, "problems.json")
LOCK = threading.Lock()

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
}

def load_state():
    with LOCK:
        if os.path.exists(STATE_PATH):
            try:
                return json.load(open(STATE_PATH, encoding="utf-8"))
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_STATE))

def save_state(s):
    with LOCK:
        json.dump(s, open(STATE_PATH, "w", encoding="utf-8"), indent=2)

def http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-dashboard/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def llm(system, user, max_tokens=8000):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing from .env")
    body = json.dumps({"model": LLM_MODEL, "max_tokens": max_tokens,
                       "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        out = json.load(r)
    # content may lead with non-text blocks (e.g. thinking) — take the text one
    txt = "".join(b.get("text", "") for b in out["content"] if b.get("type") == "text")
    if not txt:
        raise RuntimeError(f"LLM returned no text (stop_reason={out.get('stop_reason')})")
    return txt

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
    return picks

def get_markets():
    out = []
    for t in TICKERS:
        sym = "^GSPC" if t == "^SPX" else t  # yahoo's S&P symbol
        try:
            d = json.loads(http_get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                "?range=1mo&interval=1d", headers={"User-Agent": "Mozilla/5.0"}))
            res = d["chart"]["result"][0]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None][-30:]
            last = res["meta"].get("regularMarketPrice", closes[-1])
            prev = res["meta"].get("regularMarketPreviousClose", closes[-2])
            out.append({"ticker": t, "price": last,
                        "change_pct": round((last - prev) / prev * 100, 2),
                        "spark": closes})
        except Exception as e:
            out.append({"ticker": t, "error": str(e)[:80]})
    return out

def wsl_gcalcli(args):
    cmd = "gcalcli --nocolor " + args
    # server may run on Windows (shell through wsl) or inside WSL (call directly)
    prefix = ["wsl", "-e"] if os.name == "nt" else []
    r = subprocess.run(prefix + ["bash", "-lc", cmd],
                       capture_output=True, text=True, timeout=45)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or "gcalcli failed")
    return r.stdout

def get_calendar():
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

def get_chess_puzzle(rating):
    # anonymous /api/puzzle/next difficulty is relative to ~1500
    # anonymous difficulties are offsets from ~1500: easiest -600 … hardest +600
    diff = ("easiest" if rating < 1050 else "easier" if rating < 1350 else
            "normal" if rating < 1650 else "harder" if rating < 1950 else "hardest")
    d = json.loads(http_get(f"https://lichess.org/api/puzzle/next?difficulty={diff}"))
    p = d["puzzle"]
    return {"id": p["id"], "rating": p["rating"], "themes": p.get("themes", []),
            "url": f"https://lichess.org/training/{p['id']}"}

def get_paper():
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

def new_math_problem(state):
    rating = state["math"]["rating"]
    weekday = dt.date.today().weekday()  # 0=Mon
    seen = state["math"]["seen"][-200:]
    anchors = json.dumps(MATH_ANCHORS)
    sys_p = ("You select real, verifiable competition math problems. Respond ONLY with JSON: "
             '{"source":"e.g. Putnam 1998 B3","tier":one of the tier keys,'
             '"statement":"problem in LaTeX ($..$ inline, $$..$$ display)",'
             '"answer":"final answer, LaTeX ok",'
             '"hint1":"direction, under five words","hint2":"minor hint","hint3":"major hint",'
             '"textbook":{"title":"...","url":"link to a FREE online textbook section on the topic"},'
             '"theorems":[{"name":"...","url":"..."} x3, common theorems for the TOPIC, not tailored to this problem]}')
    user_p = (f"Topic of the week: {TOPIC_OF_WEEK}. Today is weekday {weekday} (0=Mon: ramp difficulty "
              f"through the week, but vary sub-skills to teach the topic broadly). "
              f"My rating: {rating}. Tier anchors: {anchors}. Pick a tier whose anchor gives me a "
              f"30-70% solve chance, adjusted for weekday. Must be from a real past competition. "
              f"Do NOT use any of these already-seen problems: {json.dumps(seen)}")
    prob = llm_json(sys_p, user_p)
    if prob.get("tier") not in MATH_ANCHORS:
        prob["tier"] = "aime-late"
    prob["anchor"] = MATH_ANCHORS[prob["tier"]]
    prob["date"] = dt.date.today().isoformat()
    prob["hints_revealed"] = 0
    # append to hand-editable problems file
    plist = json.load(open(PROBLEMS_PATH, encoding="utf-8")) if os.path.exists(PROBLEMS_PATH) else []
    plist.append(prob)
    json.dump(plist, open(PROBLEMS_PATH, "w", encoding="utf-8"), indent=2)
    state["math"]["seen"].append(prob["source"])
    state["math"]["current"] = prob
    save_state(state)
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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api(self, fn):
        try:
            self._send(200, fn())
        except Exception as e:
            self._send(500, {"error": str(e)[:300]})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, open(os.path.join(ROOT, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._send(200, {"topic": TOPIC_OF_WEEK, "tickers": TICKERS})
        elif path == "/api/state":
            self._send(200, load_state())
        elif path == "/api/news":     self._api(get_news)
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
        if path == "/api/state":            # full replace — single-user app
            save_state(body); self._send(200, {"ok": True})
        elif path == "/api/math/new":
            self._api(lambda: new_math_problem(load_state()))
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
