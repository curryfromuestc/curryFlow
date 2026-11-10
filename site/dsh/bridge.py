#!/usr/bin/env python3
"""Runs DeepSeek Harness for the curryplan ask box. Listens on 127.0.0.1:8766; the VPS reaches it through a reverse ssh tunnel.

POST /run {project, session, model, intro, prompt, api_key}; intro is prepended on a session's first turn -> text/event-stream of
     {s: step} for each tool call, {t: text} for answer text, {error}, then {done: true}
GET  /health -> {ok: true}

Meant to run inside bwrap (see run.sh): the code trees are mounted read-only, only dsh/home is writable.
"""
import glob
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deepseek_harness import DeepSeekHarness

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "home")
CODE = {"currygpu": os.path.expanduser("~/code/curryGPU"), "fastarchsim": os.path.expanduser("~/code/curryGPUpaper/sim")}
MODELS = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
PATCH = os.path.join(HERE, "profile.patch.yml")
PORT = 8766

harnesses = {}
busy = {}  # session id -> lock; one turn at a time per session
glock = threading.Lock()


def harness_for(project, model, key):
    k = (project, model)
    with glock:
        h = harnesses.get(k)
        if h is None:
            h = DeepSeekHarness(provider="deepseek-official", model=MODELS[model], cwd=CODE[project], dsh_home=HOME,
                                api_key=key, patches=(PATCH,), request_timeout_seconds=900)
            harnesses[k] = h
        return h


def describe(event):
    """Turns one session event into (step label, answer text); either may be None.

    The harness reports whole assistant messages, not token deltas: a message that also calls tools is
    progress, a message without tool calls is (part of) the answer.
    """
    kind, data = event.get("type"), event.get("data") or {}
    if kind == "tool/call":
        try:
            args = json.loads(data.get("arguments") or "{}")
        except ValueError:
            args = {}
        arg = next((args[k] for k in ("command", "pattern", "path", "file_path", "query") if args.get(k)), "")
        return f"{data.get('name', 'tool')} {str(arg)[:160]}", None
    if kind == "assistant/message":
        content = (data.get("message") or {}).get("content") or []
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        if any(c.get("type") == "tool-call" for c in content):
            return (text.strip()[:160] or None), None
        return None, text or None
    return None, None


class Handler(BaseHTTPRequestHandler):
    def emit(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()

    def do_GET(self):
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        project, model = req.get("project"), req.get("model") if req.get("model") in MODELS else "flash"
        if project not in CODE or not req.get("prompt") or not req.get("session"):
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        q = queue.Queue()
        streamed = []

        def on_note(n):
            if n.method == "session.event" and isinstance(n.payload.get("event"), dict):
                if os.environ.get("BRIDGE_DEBUG"):
                    print(json.dumps(n.payload["event"], ensure_ascii=False)[:600], flush=True)
                step, text = describe(n.payload["event"])
                if step:
                    q.put({"s": step})
                if text:
                    streamed.append(text)
                    q.put({"t": text})

        def work():
            sid = req["session"] + "-" + model  # a harness process is bound to one model, so each model keeps its own conversation
            with glock:
                lock = busy.setdefault(sid, threading.Lock())
            try:
                with lock:
                    h = harness_for(project, model, req.get("api_key"))
                    stored = bool(glob.glob(os.path.join(HOME, "sessions", "*", sid)))
                    session = h.resume_session(sid) if stored and sid not in h_seen else h.start_session(sid)
                    h_seen.add(sid)
                    prompt = req["prompt"] if stored else (req.get("intro") or "") + req["prompt"]
                    result = session.run(prompt, on_notification=on_note)
                    if not streamed and result.final_response:
                        q.put({"t": result.final_response})
                    if result.finish_reason not in (None, "completed"):
                        q.put({"error": "结束原因：" + str(result.finish_reason)})
            except Exception as e:  # report to the page, the harness may still be usable for the next turn
                q.put({"error": f"{type(e).__name__}: {e}"})
            q.put(None)

        threading.Thread(target=work, daemon=True).start()
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                self.emit(item)
            self.emit({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


h_seen = set()

if __name__ == "__main__":
    os.makedirs(HOME, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
