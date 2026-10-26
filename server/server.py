#!/usr/bin/env python3
"""Stores the choices made on the discussion page.

POST /login {pw} -> sets the cookie nginx checks (password and token live in auth.env)
GET  /choices  -> current state {id: {value, note, at}}
POST /choices  {id, value?, note?} -> updates one entry, appends to the history log
GET  /doc/NAME -> {html, at} of an editable document, 404 until first saved
POST /doc/NAME {html} -> saves it; every saved version is also kept under docs/NAME/
GET  /ask/PAGE -> the questions and answers asked on a page since the last reset
POST /ask/PAGE {clear: true} -> starts a new conversation on that page (older records stay in the file)
GET  /c/PAGE -> comments on a page; POST /c/PAGE {anchor, label, text?, sketch?} adds one; {del: id} removes one
POST /ask {page, title, context, question, history} -> streams the model's answer as text/event-stream;
     pages of a project with code go to DeepSeek Harness on the workstation (BRIDGE, reverse ssh tunnel), which keeps
     the conversation itself; when it is unreachable the model calls grep/read/ls on the snapshot under code/<project>/.
     Tool calls are streamed as {s}.
     (OpenAI-compatible endpoint; base, key and model come from llm.env next to this file)
"""
import json
import os
import re
import subprocess
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.path.expanduser("~/curry-site")
STATE = os.path.join(DATA_DIR, "choices.json")
HISTORY = os.path.join(DATA_DIR, "choices-history.jsonl")
DOCS = os.path.join(DATA_DIR, "docs")
ASK_PAGE = re.compile(r"/ask/([a-z0-9_/-]{1,80}?)/?$")
QA = os.path.join(DATA_DIR, "qa")
COMMENTS = os.path.join(DATA_DIR, "comments")
C_PAGE = re.compile(r"/c/([a-z0-9_/-]{1,80}?)/?$")
SYSTEM = ("你是 curryplan 里的讲解助手，读者是这个 GPU 项目的负责人，懂硬件。"
          "根据给出的页面内容回答：先给一句结论，再用最少的话解释；需要时列步骤或给数字。"
          "页面里没有、你也不确定的，直说不知道，不要编。用中文。")
CODE = os.path.join(DATA_DIR, "code")
CODE_NAMES = {"currygpu": "curryGPU 仓库，标签 sm-1.25ns（RTL 在 rtl/src）", "fastarchsim": "FastArchSim 模拟器，提交 9b4e042"}
CODE_HINT = ("\n\n你可以用工具查看{name}的源码快照（只读，路径相对仓库根）。"
             "问到实现细节、参数、时序、为什么时，先 grep 定位再 read 相关行，回答里注明 文件:行号。页面内容和代码不一致时以代码为准并指出。"
             "仓库顶层：{top}")
TOOLS = [
    {"type": "function", "function": {"name": "grep", "description": "在仓库里按正则搜索，返回 文件:行号:内容，最多 60 条",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "description": "子目录或文件，默认整个仓库"},
                    "glob": {"type": "string", "description": "文件名过滤，如 *.sv"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "read", "description": "读文件的一段，一次最多 300 行",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "ls", "description": "列目录",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
]
MAX_ROUNDS = 12
BRIDGE = "http://127.0.0.1:8766/run"
DOC_NAME = re.compile(r"/doc/([a-z0-9_-]{1,40})/?$")
PORT = 8765
CST = timezone(timedelta(hours=8))
lock = threading.Lock()


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)



def code_root(page):
    name = page.strip("/").split("/")[0]
    root = os.path.realpath(os.path.join(CODE, name))
    return (name, root) if name in CODE_NAMES and os.path.isdir(root) else (None, None)


def run_tool(root, name, args):
    """Runs one model tool call inside the snapshot; paths may not leave it."""
    rel = str(args.get("path") or ".").lstrip("/")
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        return "路径不在仓库里"
    if not os.path.exists(target):
        return "没有这个路径：" + rel
    if name == "ls":
        if not os.path.isdir(target):
            return "不是目录"
        out = []
        for e in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
            out.append(e.name + "/" if e.is_dir() else f"{e.name}  {e.stat().st_size}B")
        return "\n".join(out[:300])
    if name == "read":
        if not os.path.isfile(target):
            return "不是文件"
        with open(target, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        start = max(1, int(args.get("start") or 1))
        end = min(len(lines), int(args.get("end") or start + 199), start + 299)
        body = "".join(f"{i}\t{lines[i - 1]}" for i in range(start, end + 1))
        return f"{rel}（共 {len(lines)} 行，显示 {start}-{end}）\n" + body
    if name == "grep":
        cmd = ["grep", "-rnIE", "--", str(args.get("pattern") or ""), target]
        if args.get("glob"):
            cmd[1:1] = ["--include=" + str(args["glob"])]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, errors="replace")
        except subprocess.TimeoutExpired:
            return "搜索超时，缩小 path 再试"
        hits = [h[len(root) + 1:][:240] for h in r.stdout.splitlines()]
        more = f"\n……还有 {len(hits) - 60} 条，缩小范围" if len(hits) > 60 else ""
        return "\n".join(hits[:60]) + more if hits else "没有匹配"
    return "未知工具"


def read_env(name):
    env = {}
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def login(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            pw = json.loads(self.rfile.read(min(length, 1000)) or b"{}").get("pw", "")
        except ValueError:
            pw = ""
        auth = read_env("auth.env")
        if not pw or pw != auth.get("LOGIN_PW"):
            return self.send_json(403, {"error": "wrong password"})
        self.send_response(204)
        self.send_header("Set-Cookie", "cpk=" + auth["LOGIN_TOKEN"] + "; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax")
        self.end_headers()

    def doc_get(self, name):
        path = os.path.join(DOCS, name + ".json")
        with lock:
            if not os.path.exists(path):
                return self.send_json(404, {"error": "not saved yet"})
            with open(path, encoding="utf-8") as f:
                self.send_json(200, json.load(f))

    def doc_post(self, name):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4_000_000:
            return self.send_json(400, {"error": "bad length"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send_json(400, {"error": "bad json"})
        if not isinstance(body.get("html"), str):
            return self.send_json(400, {"error": "bad html"})
        now = datetime.now(CST)
        record = {"html": body["html"], "at": now.strftime("%Y-%m-%d %H:%M:%S")}
        with lock:
            os.makedirs(os.path.join(DOCS, name), exist_ok=True)
            text = json.dumps(record, ensure_ascii=False)
            with open(os.path.join(DOCS, name, now.strftime("%Y%m%d-%H%M%S") + ".json"), "w", encoding="utf-8") as f:
                f.write(text)
            tmp = os.path.join(DOCS, name + ".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, os.path.join(DOCS, name + ".json"))
        self.send_json(200, {"at": record["at"]})

    def llm_env(self):
        return read_env("llm.env")

    def qa_file(self, page):
        return os.path.join(QA, page.strip("/").replace("/", "__") + ".jsonl")

    def ask_get(self, page):
        path = self.qa_file(page)
        items = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for x in f:
                    if x.strip():
                        rec = json.loads(x)
                        items = [] if rec.get("reset") else items + [rec]
        self.send_json(200, items)

    def ask_reset(self, page):
        os.makedirs(QA, exist_ok=True)
        with lock, open(self.qa_file(page), "a", encoding="utf-8") as f:
            f.write(json.dumps({"reset": True, "at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        self.send_json(200, [])


    def ask_post(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2_000_000:
            return self.send_json(400, {"error": "bad length"})
        try:
            req = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send_json(400, {"error": "bad json"})
        question = str(req.get("question") or "").strip()
        page = str(req.get("page") or "misc")
        if not question:
            return self.send_json(400, {"error": "empty question"})
        env = self.llm_env()
        project = page.strip("/").split("/")[0]
        if project in CODE_NAMES and self.ask_bridge(req, page, project, question, env):
            return
        project, root = code_root(page)
        system = SYSTEM + "\n\n当前页面：" + str(req.get("title") or "")
        if root:
            system += CODE_HINT.format(name=CODE_NAMES[project], top="、".join(sorted(os.listdir(root))))
        msgs = [{"role": "system", "content": system + "\n\n页面内容：\n" + str(req.get("context") or "")[:200000]}]
        for h in (req.get("history") or [])[-10:]:
            msgs += [{"role": "user", "content": str(h.get("q", ""))}, {"role": "assistant", "content": str(h.get("a", ""))}]
        msgs.append({"role": "user", "content": question})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        emit = lambda obj: (self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()), self.wfile.flush())
        answer, steps = [], []
        try:
            for rnd in range(MAX_ROUNDS + 1):
                payload = {"model": env.get("LLM_MODEL", "deepseek-chat"), "messages": msgs, "stream": True}
                if root and rnd < MAX_ROUNDS:
                    payload["tools"] = TOOLS
                up = urllib.request.Request(env["LLM_BASE"].rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers={
                    "Authorization": "Bearer " + env["LLM_KEY"], "Content-Type": "application/json"})
                calls, text = {}, []
                with urllib.request.urlopen(up, timeout=300) as r:
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:") or line == "data: [DONE]":
                            continue
                        try:
                            delta = json.loads(line[5:])["choices"][0]["delta"]
                        except (ValueError, KeyError, IndexError):
                            continue
                        for tc in delta.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["name"] += fn.get("name") or ""
                            c["args"] += fn.get("arguments") or ""
                        piece = delta.get("content") or ""
                        if piece:
                            text.append(piece)
                            answer.append(piece)
                            emit({"t": piece})
                if not calls:
                    break
                msgs.append({"role": "assistant", "content": "".join(text) or None, "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}} for c in calls.values()]})
                for c in calls.values():
                    try:
                        args = json.loads(c["args"] or "{}")
                    except ValueError:
                        args = {}
                    label = c["name"] + " " + " ".join(str(v) for v in args.values())
                    steps.append(label)
                    emit({"s": label})
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": run_tool(root, c["name"], args)})
        except Exception as e:  # upstream failure: tell the page, keep what arrived
            emit({"error": str(e)})
        emit({"done": True})
        if answer:
            self.save_qa(page, {"q": question, "a": "".join(answer), "steps": steps})

    def save_qa(self, page, rec):
        os.makedirs(QA, exist_ok=True)
        rec["at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        with lock, open(self.qa_file(page), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def ask_bridge(self, req, page, project, question, env):
        """Streams the answer from DeepSeek Harness; False (nothing sent yet) when the workstation is unreachable."""
        resets = 0
        if os.path.exists(self.qa_file(page)):
            with open(self.qa_file(page), encoding="utf-8") as f:
                resets = sum(1 for x in f if x.strip() and json.loads(x).get("reset"))
        intro = ("你在回答 curryplan 页面读者的问题，读者是这个项目的负责人，懂硬件。仓库就是当前目录，只读，不要尝试修改或构建。"
                 "先一句结论，再用最少的话解释；涉及实现时去代码里查，注明 文件:行号。页面和代码不一致时以代码为准并指出。用中文。\n\n")
        # the whole page goes in once per conversation; the part the reader clicked goes with every question
        focus, _, whole = str(req.get("context") or "").rpartition("\n\n【整页】\n")
        if not _:
            focus, whole = "", str(req.get("context") or "")
        intro += "【页面】" + str(req.get("title") or "") + "（" + page + "）\n" + whole[:60000] + "\n\n"
        prompt = (focus[:20000] + "\n\n" if focus else "") + "【问题】" + question
        model = "pro" if req.get("model") == "pro" else "flash"
        body = json.dumps({"project": project, "session": page.strip("/").replace("/", "_") + "-" + str(resets), "model": model,
                           "intro": intro, "prompt": prompt, "api_key": env["LLM_KEY"]}).encode()
        try:
            r = urllib.request.urlopen(urllib.request.Request(BRIDGE, data=body, headers={"Content-Type": "application/json"}), timeout=900)
        except OSError:
            return False
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        answer, steps = [], []
        try:
            with r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    d = json.loads(line[5:])
                    answer.append(d.get("t", ""))
                    if "s" in d:
                        steps.append(d["s"])
                    self.wfile.write((line + "\n\n").encode())
                    self.wfile.flush()
        except Exception as e:  # tunnel dropped mid-answer
            self.wfile.write(("data: " + json.dumps({"error": str(e), "done": True}, ensure_ascii=False) + "\n\n").encode())
        if "".join(answer):
            self.save_qa(page, {"q": question, "a": "".join(answer), "steps": steps, "model": model})
        return True

    def c_file(self, page):
        return os.path.join(COMMENTS, page.strip("/").replace("/", "__") + ".json")

    def c_load(self, page):
        path = self.c_file(page)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def c_post(self, page):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 3_000_000:
            return self.send_json(400, {"error": "bad length"})
        try:
            req = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send_json(400, {"error": "bad json"})
        with lock:
            items = self.c_load(page)
            if req.get("del"):
                items = [c for c in items if c.get("id") != req["del"]]
            else:
                now = datetime.now(CST)
                items.append({"id": now.strftime("%Y%m%d%H%M%S%f"), "at": now.strftime("%Y-%m-%d %H:%M:%S"),
                              "anchor": str(req.get("anchor") or ""), "label": str(req.get("label") or "")[:200],
                              "text": str(req.get("text") or "")[:4000], "sketch": str(req.get("sketch") or "")})
            os.makedirs(COMMENTS, exist_ok=True)
            tmp = self.c_file(page) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(tmp, self.c_file(page))
        self.send_json(200, items)

    def do_GET(self):
        m = C_PAGE.match(self.path)
        if m:
            with lock:
                return self.send_json(200, self.c_load(m.group(1)))
        m = ASK_PAGE.match(self.path)
        if m:
            return self.ask_get(m.group(1))
        m = DOC_NAME.match(self.path)
        if m:
            return self.doc_get(m.group(1))
        if self.path.rstrip("/") != "/choices":
            return self.send_json(404, {"error": "not found"})
        with lock:
            self.send_json(200, load())

    def do_POST(self):
        if self.path.rstrip("/") == "/login":
            return self.login()
        if self.path.rstrip("/") == "/ask":
            return self.ask_post()
        m = ASK_PAGE.match(self.path)
        if m:
            return self.ask_reset(m.group(1))
        m = C_PAGE.match(self.path)
        if m:
            return self.c_post(m.group(1))
        m = DOC_NAME.match(self.path)
        if m:
            return self.doc_post(m.group(1))
        if self.path.rstrip("/") != "/choices":
            return self.send_json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 20000:
            return self.send_json(400, {"error": "bad length"})
        try:
            request = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send_json(400, {"error": "bad json"})
        choice_id = request.get("id")
        value = request.get("value")
        note = request.get("note")
        if not isinstance(choice_id, str) or not 0 < len(choice_id) <= 64:
            return self.send_json(400, {"error": "bad id"})
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            return self.send_json(400, {"error": "bad value"})
        if note is not None and (not isinstance(note, str) or len(note) > 4000):
            return self.send_json(400, {"error": "bad note"})
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        with lock:
            state = load()
            entry = state.get(choice_id, {})
            if value is not None:
                entry["value"] = value
            if note is not None:
                entry["note"] = note
            entry["at"] = now
            state[choice_id] = entry
            save(state)
            record = {"id": choice_id, "at": now}
            if value is not None:
                record["value"] = value
            if note is not None:
                record["note"] = note
            with open(HISTORY, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.send_json(200, state)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
