#!/usr/bin/env python3
"""Golden Goal live controller.

Run:  python3 server.py      (keep this window open while you stream)
Phone controller : the http://<your-ip>:8080 address printed below (same Wi-Fi)
OBS browser source: http://localhost:8080/overlay
"""
import json, os, random, socket, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

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
