"""Stands in for nginx on this machine, as curryplan.conf has it: static files with Cache-Control: no-cache, and /api/
forwarded to 127.0.0.1:8765 without the prefix.  Usage: mock_nginx.py ROOT PORT"""
import mimetypes, os, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT, PORT = sys.argv[1], int(sys.argv[2])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
class H(BaseHTTPRequestHandler):
    def reply(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def route(self):
        if self.path.startswith("/api/"):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else None
            heads = {k: self.headers[k] for k in ("Content-Type", "User-Agent", "Cookie", "X-Notify-Key") if self.headers.get(k)}
            try:
                r = opener.open(urllib.request.Request("http://127.0.0.1:8765" + self.path[4:], data=body, method=self.command, headers=heads), timeout=120)
            except urllib.error.HTTPError as e:
                r = e
            return self.reply(r.status if hasattr(r, "status") else r.code, r.read(), r.headers.get("Content-Type"))
        path = os.path.normpath(os.path.join(ROOT, self.path.split("?")[0].lstrip("/")))
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not path.startswith(os.path.realpath(ROOT)) or not os.path.isfile(path):
            return self.reply(404, b"not found", "text/plain")
        with open(path, "rb") as f:
            self.reply(200, f.read(), mimetypes.guess_type(path)[0])
    do_GET = do_POST = route
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
