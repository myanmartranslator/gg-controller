#!/usr/bin/env python3
"""Golden Goal live controller.

Run:  python3 server.py      (keep this window open while you stream)
Phone controller : the http://<your-ip>:8080 address printed below (same Wi-Fi)
OBS browser source: http://localhost:8080/overlay
"""
import json, os, random, socket, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request

PORT = int(os.environ.get("PORT") or os.environ.get("GG_PORT") or "8080")  # Render sets PORT
ON_RENDER = bool(os.environ.get("RENDER"))
HERE = os.path.dirname(os.path.abspath(__file__))
PIN = os.environ.get("GG_PIN") or str(random.randint(1000, 9999))

cond = threading.Condition()
commands = []          # list of (id, text)
next_id = 1
state = {"json": "{}", "at": 0}

TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------- live league table (ESPN public standings feed, no key needed) ----------
LEAGUES = {"eng.1": "Premier League", "esp.1": "LaLiga", "ita.1": "Serie A", "ger.1": "Bundesliga",
           "fra.1": "Ligue 1", "eng.2": "Championship", "uefa.champions": "Champions League"}
SHORT = {"Manchester City": "Man City", "Manchester United": "Man United", "Brighton & Hove Albion": "Brighton",
         "AFC Bournemouth": "Bournemouth", "Tottenham Hotspur": "Tottenham", "Newcastle United": "Newcastle",
         "Nottingham Forest": "Nott'm Forest", "Leeds United": "Leeds", "Wolverhampton Wanderers": "Wolves",
         "West Ham United": "West Ham", "Ipswich Town": "Ipswich", "Coventry City": "Coventry", "Hull City": "Hull",
         "Leicester City": "Leicester", "Sheffield United": "Sheffield Utd", "Norwich City": "Norwich",
         "Atletico Madrid": "Atlético", "Paris Saint-Germain": "PSG", "Bayern Munich": "Bayern",
         "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen", "Internazionale": "Inter"}
TABLE_URL = os.environ.get("GG_TABLE_URL", "https://site.api.espn.com/apis/v2/sports/soccer/{lg}/standings")
_tcache = {}


def parse_standings(data):
    groups = data.get("children") or [data]
    entries = []
    for g in groups:
        entries += (g.get("standings") or {}).get("entries") or []
    rows = []
    for e in entries:
        st = {x.get("name"): x.get("value") for x in e.get("stats", []) if "value" in x}
        t = e.get("team") or {}
        full = t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "?"
        n = lambda k: int(round(float(st.get(k) or 0)))
        rows.append({"team": SHORT.get(full, t.get("shortDisplayName") if len(full) > 14 and t.get("shortDisplayName") else full),
                     "p": n("gamesPlayed"), "w": n("wins"), "d": n("ties"), "l": n("losses"),
                     "gd": n("pointDifferential"), "pts": n("points"), "_r": st.get("rank") or 99})
    rows.sort(key=lambda r: (r["_r"] or 99, -r["pts"], -r["gd"]))
    for r in rows:
        r.pop("_r", None)
    return rows


def live_table(lg):
    if lg not in LEAGUES:
        lg = "eng.1"
    c = _tcache.get(lg)
    if c and time.time() - c[0] < 60:
        return c[1]
    req = urllib.request.Request(TABLE_URL.format(lg=lg), headers={"User-Agent": "Mozilla/5.0 GoldenGoal"})
    with urllib.request.urlopen(req, timeout=12) as r:
        rows = parse_standings(json.loads(r.read().decode("utf-8")))
    if not rows:
        raise ValueError("feed returned no teams")
    out = {"ok": True, "league": LEAGUES[lg], "rows": rows, "at": int(time.time())}
    _tcache[lg] = (time.time(), out)
    return out


# ---------- official line-ups (ESPN scoreboard + match summary) ----------
BOARD_URL = os.environ.get("GG_BOARD_URL", "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={d}")
SUMMARY_URL = os.environ.get("GG_SUMMARY_URL", "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/summary?event={ev}")
_jcache = {}


def get_json(url, ttl):
    c = _jcache.get(url)
    if c and time.time() - c[0] < ttl:
        return c[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 GoldenGoal"})
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode("utf-8"))
    _jcache[url] = (time.time(), data)
    return data


def list_matches(lg):
    if lg not in LEAGUES:
        lg = "eng.1"
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date()
    out, seen = [], set()
    for k in (-1, 0, 1, 2):
        d = (today + _dt.timedelta(days=k)).strftime("%Y%m%d")
        try:
            data = get_json(BOARD_URL.format(lg=lg, d=d), 120)
        except Exception:
            continue
        for ev in data.get("events") or []:
            if ev.get("id") in seen:
                continue
            seen.add(ev.get("id"))
            comp = ((ev.get("competitions") or [{}])[0].get("competitors")) or []
            t = {}
            for c in comp:
                tm = c.get("team") or {}
                t[c.get("homeAway")] = {"abbr": tm.get("abbreviation", ""), "name": tm.get("displayName", ""),
                                        "short": SHORT.get(tm.get("displayName", ""), tm.get("shortDisplayName", ""))}
            if "home" in t and "away" in t:
                st = ((ev.get("status") or {}).get("type") or {}).get("shortDetail", "")
                out.append({"id": ev.get("id"), "date": ev.get("date", ""), "status": st, "home": t["home"], "away": t["away"]})
    out.sort(key=lambda m: m["date"])
    return {"ok": True, "league": LEAGUES[lg], "matches": out}


def _side_key(pos):
    # 0 = left wide, 1 = left-centre, 2 = centre, 3 = right-centre, 4 = right wide (from the team's own view)
    p = (pos or "").upper()
    if p.endswith("-L"):
        return 1
    if p.endswith("-R"):
        return 3
    if p.startswith("L"):
        return 0
    if p.startswith("R"):
        return 4
    return 2


def _line(pos):
    p = (pos or "").upper()
    if p in ("G", "GK"):
        return 0
    if p.startswith("CD") or p.startswith("CB") or p.endswith("B") or p in ("D", "SW") or "WB" in p:
        return 1
    if p.startswith("DM"):
        return 2
    if p.startswith("AM"):
        return 4
    if p.startswith("F") or p.startswith("CF") or p.startswith("ST") or p in ("S", "LW", "RW", "W", "LF", "RF"):
        return 5
    return 3      # CM, M, LM, RM ...


def build_xi(roster, top_half):
    starters = [r for r in roster if r.get("starter")]
    if len(starters) < 11:
        return None
    starters = starters[:11]
    lines = {}
    lastnames = {}
    for r in starters:
        a = r.get("athlete") or {}
        ln = (a.get("lastName") or "").strip() or (a.get("displayName") or "?").split()[-1]
        lastnames[ln] = lastnames.get(ln, 0) + 1
    for r in starters:
        pos = ((r.get("position") or {}).get("abbreviation")) or ""
        a = r.get("athlete") or {}
        ln = (a.get("lastName") or "").strip() or (a.get("displayName") or "?").split()[-1]
        if lastnames.get(ln, 0) > 1:
            ln = a.get("shortName") or a.get("displayName") or ln
        fp = r.get("formationPlace")
        try:
            fp = int(fp)
        except Exception:
            fp = 99
        lines.setdefault(_line(pos), []).append((_side_key(pos), fp, ln, str(r.get("jersey") or "")))
    if len(lines.get(0, [])) != 1:          # need exactly one keeper; otherwise trust order as-is
        gk = lines.pop(0, [])
        extra = gk[1:] if gk else []
        lines.setdefault(3, []).extend(extra)
        lines[0] = gk[:1] or [lines.setdefault(3, []).pop(0)]
    # wide midfielders behind a No.10 belong in the same row (4-2-3-1: LM, AM, RM)
    if lines.get(3) and lines.get(4) and all(x[0] in (0, 4) for x in lines[3]):
        lines[4] = lines[4] + lines.pop(3)
    # one holding mid + two central mids, nothing in front = a flat 3 (4-3-3)
    if len(lines.get(2, [])) == 1 and len(lines.get(3, [])) == 2 and not lines.get(4):
        lines[3] = lines[3] + lines.pop(2)
    rows, p, n = [], [], []
    for k in sorted(lines):
        pl = sorted(lines[k], key=lambda x: (x[0], x[1]))
        if top_half:                         # away team is drawn upside-down: their right is on screen left
            pl = pl[::-1]
        if k:
            rows.append(str(len(pl)))
        for x in pl:
            p.append(x[2]); n.append(x[3])
    return {"f": "-".join(rows), "p": p, "n": n}


def lineups(lg, ev):
    if lg not in LEAGUES:
        lg = "eng.1"
    data = get_json(SUMMARY_URL.format(lg=lg, ev=ev), 30)
    out = {"ok": True, "available": False}
    for ro in data.get("rosters") or []:
        side = ro.get("homeAway")
        tm = ro.get("team") or {}
        xi = build_xi(ro.get("roster") or [], side == "away")
        out[side] = {"abbr": tm.get("abbreviation", ""), "name": tm.get("displayName", ""), "xi": xi}
    out["available"] = bool(out.get("home", {}).get("xi") and out.get("away", {}).get("xi"))
    return out


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def file(self, name):
        p = os.path.normpath(os.path.join(HERE, name))
        if not p.startswith(HERE) or not os.path.isfile(p):
            return self.send(404, b"not found")
        with open(p, "rb") as f:
            data = f.read()
        self.send(200, data, TYPES.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))

    def pin_ok(self):
        return self.headers.get("X-PIN", "") == PIN

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/control"):
            return self.file("controller.html")
        if path == "/overlay":
            return self.file("overlay.html")
        if path == "/state":
            return self.send(200, state["json"].encode(), "application/json")
        if path in ("/api/matches", "/api/lineups"):
            q = parse_qs(urlparse(self.path).query)
            lg = (q.get("league") or ["eng.1"])[0]
            try:
                out = list_matches(lg) if path == "/api/matches" else lineups(lg, (q.get("event") or [""])[0])
            except Exception as e:
                out = {"ok": False, "error": str(e)[:200]}
            return self.send(200, json.dumps(out).encode(), "application/json")
        if path == "/api/table":
            lg = (parse_qs(urlparse(self.path).query).get("league") or ["eng.1"])[0]
            try:
                out = live_table(lg)
            except Exception as e:
                out = {"ok": False, "error": str(e)[:200]}
            return self.send(200, json.dumps(out).encode(), "application/json")
        if path == "/healthz":
            return self.send(200, b"ok")
        if path == "/check":
            return self.send(200 if self.pin_ok() else 403, b"ok" if self.pin_ok() else b"bad pin")
        if path == "/events":                     # overlay listens here (server-sent events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")   # stop proxies buffering the stream
            self.end_headers()
            with cond:
                last = next_id - 1
                try:
                    lid = int(self.headers.get("Last-Event-ID", ""))
                    if lid < last: last = lid          # resume: resend commands missed while reconnecting
                except ValueError:
                    pass
            try:
                self.wfile.write(b"retry: 2000\n: connected\n\n"); self.wfile.flush()
                while True:
                    with cond:
                        new = [c for c in commands if c[0] > last]
                        if not new:
                            cond.wait(timeout=15)
                            new = [c for c in commands if c[0] > last]
                    if new:
                        for cid, text in new:
                            self.wfile.write(("id: %d\ndata: " % cid + json.dumps({"id": cid, "cmd": text}) + "\n\n").encode())
                            last = cid
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                return
        # static files (sponsor.png, sponsor-ad.png ...)
        return self.file(path.lstrip("/"))

    def do_POST(self):
        global next_id
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        if path == "/cmd":
            if not self.pin_ok():
                return self.send(403, b"bad pin")
            with cond:
                commands.append((next_id, body)); next_id += 1
                del commands[:-200]
                cond.notify_all()
            return self.send(200, b"ok")
        if path == "/state":                      # overlay reports its state for the phone display
            state["json"], state["at"] = body or "{}", time.time()
            return self.send(200, b"ok")
        return self.send(404, b"not found")


if __name__ == "__main__":
    if ON_RENDER:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
        srv.daemon_threads = True
        host = os.environ.get("RENDER_EXTERNAL_URL", "https://<your-app>.onrender.com")
        print(f"Golden Goal running on Render (port {PORT})", flush=True)
        print(f"  PHONE:              {host}", flush=True)
        print(f"  OBS browser source: {host}/overlay", flush=True)
        print("  PIN:                " + ("(set via GG_PIN env var)" if os.environ.get("GG_PIN") else PIN), flush=True)
        srv.serve_forever()
    ip = lan_ip()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    line = "=" * 58
    print(line)
    print("  GOLDEN GOAL LIVE CONTROLLER is running")
    print(line)
    print(f"  PHONE (same Wi-Fi):  http://{ip}:{PORT}")
    print(f"  PIN:                 {PIN}")
    print(f"  OBS browser source:  http://localhost:{PORT}/overlay")
    print(line)
    print("  Keep this window open while you stream. Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
