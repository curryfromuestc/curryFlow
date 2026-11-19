#!/usr/bin/env python3
"""聊天页的模型节点：跑在有模型的 Mac 上，按需启动、停止模型服务（mlx_vlm.server 或 llama-server），转发对话请求。

只听 127.0.0.1:8090，经 ssh -R 映射到 curryPlan 机的 127.0.0.1:18090，由那里的 site/chat/server.py 调用。
每个请求都要带 X-LLM-Key（与 node.key 文件内容相同；curryPlan 机上写在 ~/curry-chat/chat.env 的 NODE_KEY）。

GET  /api/status            -> {state: running|loading|stopped|external, model, since, mem_gb}
POST /api/start, /api/stop  -> 启动、停止模型（OpenAI 兼容的模型服务，在 127.0.0.1:MODEL_PORT）
POST /v1/chat/completions {messages, max_tokens?, temperature?} -> 模型的 SSE 原样流回；调用方断开时关掉上游，生成随之停下
POST /agent/run {sid, text, images?: [{data, mimeType}], cwd?, system?}
     -> 在本机跑 pi（--mode rpc，一个会话一个进程，闲置 AGENT_IDLE 秒后关掉，下次用 --continue 接上），
        把 pi 的事件瘦身成 SSE `data: {k: think|text|turn|tool|tool_out|tool_end|note|error|done, ...}` 流回；
        没事件时每 15 秒发一行 `: ping`；调用方断开就给 pi 发 abort。同一时间只跑一个（模型只有一个），忙时 409
POST /agent/reset {sid}     -> 关掉这个会话的 pi 进程、删掉它的 pi 会话记录（工作目录不删）

Agent 的工具（bash、读写文件）直接在这台 Mac 上以当前用户执行，不经沙箱；门禁就是 curryPlan 的口令。
工作目录默认 AGENT_WORK/<sid>，会话可以另设；pi 的会话记录在 ~/llm-web/agent/<sid>/。
pi 的模型配置在 ~/.pi/agent/models.json（指向 127.0.0.1:MODEL_PORT）。

部署：~/llm-web/node.py，配置在同目录 node.json：
  {"model_path", "model_name", "server_bin", "server_env"?: {模型进程的环境变量，如 APC_*}, "server_args"?: [...],
   "server_argv"?: [完整的启动命令，"{port}" 换成 MODEL_PORT；给了就不用 server_bin 和 server_args，如 llama-server],
   "pi_bin"?, "agent_work"?, "agent_env"?: {pi 进程的环境变量，如代理}}；LaunchAgent com.curry.llm-web。
"""
import hmac
import http.client
import json
import os
import queue
import re
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LISTEN = ("127.0.0.1", 8090)
MODEL_PORT = 8081
CFG = json.load(open(os.path.join(HERE, "node.json")))
MODEL_PATH = os.path.expanduser(CFG["model_path"])
MODEL_NAME = CFG["model_name"]
SERVER_BIN = os.path.expanduser(CFG.get("server_bin", "~/.local/bin/mlx_vlm.server"))
KEY = open(os.path.join(HERE, "node.key")).read().strip()
MODEL_LOG = os.path.join(HERE, "logs", "model-server.log")
MAX_BODY = 64 * 1024 * 1024
LOCK = threading.Lock()
PROC = {"p": None, "started": None}
PI_BIN = os.path.expanduser(CFG.get("pi_bin", "/opt/homebrew/bin/pi"))
AGENT_WORK = os.path.expanduser(CFG.get("agent_work", "~/agent-work"))
AGENT_STATE = os.path.join(HERE, "agent")
AGENT_LOG = os.path.join(HERE, "logs", "agent.log")
AGENT_IDLE = 900
SID = re.compile(r"^[a-z0-9]{12}$")
AGENTS = {}                   # sid -> Agent
AGENTS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()   # 同一时间只跑一个 agent
RUNNING = {"sid": None}


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def model_ready():
    try:
        c = http.client.HTTPConnection("127.0.0.1", MODEL_PORT, timeout=2)
        c.request("GET", "/v1/models")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def footprint_gb(pid):
    """macOS footprint 的 Footprint 一行，单位可能是 KB、MB、GB。"""
    try:
        out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if "Footprint:" in line:
            n, unit = line.split("Footprint:")[1].split()[:2]
            return round(float(n) / {"KB": 1024 * 1024, "MB": 1024, "GB": 1}.get(unit.upper()[:2], 1), 1)
    return None


def alive():
    p = PROC["p"]
    return p is not None and p.poll() is None


def status():
    if alive():
        state = "running" if model_ready() else "loading"
    else:
        state = "external" if port_open(MODEL_PORT) else "stopped"
    return {"state": state, "model": MODEL_NAME, "since": PROC["started"] if alive() else None,
            "mem_gb": footprint_gb(PROC["p"].pid) if alive() else None, "agent_busy": RUNNING["sid"]}


def start_model():
    with LOCK:
        if alive():
            return
        if port_open(MODEL_PORT):
            raise RuntimeError("端口 %d 被别的程序占着" % MODEL_PORT)
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env.update({k: str(v) for k, v in CFG.get("server_env", {}).items()})
        argv = ([os.path.expanduser(str(a)).replace("{port}", str(MODEL_PORT)) for a in CFG["server_argv"]]
                if CFG.get("server_argv") else
                [SERVER_BIN, "--host", "127.0.0.1", "--port", str(MODEL_PORT), "--model", MODEL_PATH, "--enable-thinking"]
                + [str(a) for a in CFG.get("server_args", [])])
        PROC["p"] = subprocess.Popen(argv,
            stdout=open(MODEL_LOG, "ab"), stderr=subprocess.STDOUT, cwd=HERE, env=env, start_new_session=True)
        PROC["started"] = int(time.time())


def stop_model():
    with LOCK:
        p = PROC["p"]
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=5)
            except ProcessLookupError:
                pass
        PROC["p"] = PROC["started"] = None


# ---------------- Agent（pi --mode rpc） ----------------

def clip(s, head=2000, tail=6000):
    s = s or ""
    return s if len(s) <= head + tail else s[:head] + "\n…（中间省略 %d 字）…\n" % (len(s) - head - tail) + s[-tail:]


def short_args(a):
    """工具参数里的长字符串（如 write 的整个文件内容）截短，免得把会话撑大。"""
    if not isinstance(a, dict):
        return a
    return {k: (clip(v, 1500, 1500) if isinstance(v, str) else v) for k, v in a.items()}


def result_text(r):
    return "".join(c.get("text", "") for c in ((r or {}).get("content") or []) if c.get("type") == "text")


class Agent:
    def __init__(self, sid, cwd, system):
        self.sid, self.cwd, self.system = sid, cwd, system
        sdir = os.path.join(AGENT_STATE, sid)
        os.makedirs(sdir, exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        args = [PI_BIN, "--mode", "rpc", "--session-dir", sdir]
        if any(n.endswith(".jsonl") for n in os.listdir(sdir)):
            args.append("--continue")
        if system.strip():
            sp = os.path.join(sdir, "system.md")
            with open(sp, "w", encoding="utf-8") as f:
                f.write(system.strip() + "\n")
            args += ["--append-system-prompt", sp]
        env = dict(os.environ)
        env["PATH"] = ":".join(["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin"),
                                "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
        env.update({k: str(v) for k, v in CFG.get("agent_env", {}).items()})
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=open(AGENT_LOG, "ab"), cwd=cwd, env=env, start_new_session=True)
        self.q = queue.Queue()
        self.last = time.time()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:   # pi 的 RPC 只用 \n 分行，二进制 readline 正合适
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put(None)

    def alive(self):
        return self.p.poll() is None

    def send(self, obj):
        self.p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self.p.stdin.flush()

    def kill(self):
        if self.alive():
            try:
                os.killpg(self.p.pid, signal.SIGTERM)
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def agent_get(sid, cwd, system):
    with AGENTS_LOCK:
        a = AGENTS.get(sid)
        if a and (not a.alive() or a.cwd != cwd or a.system != system):
            a.kill()
            a = None
        if not a:
            a = AGENTS[sid] = Agent(sid, cwd, system)
        return a


def agent_drop(sid, wipe=False):
    with AGENTS_LOCK:
        a = AGENTS.pop(sid, None)
    if a:
        a.kill()
    if wipe:
        shutil.rmtree(os.path.join(AGENT_STATE, sid), ignore_errors=True)


def agent_reaper():
    while True:
        time.sleep(60)
        with AGENTS_LOCK:
            idle = [s for s, a in AGENTS.items() if s != RUNNING["sid"] and time.time() - a.last > AGENT_IDLE]
        for s in idle:
            agent_drop(s)


def slim(e, a):
    """pi 的事件 -> 页面要的小事件；None 表示不往外发。对话框类扩展请求直接取消，免得卡住。"""
    t = e.get("type")
    if t == "message_update":
        ae = e.get("assistantMessageEvent") or {}
        if ae.get("type") == "thinking_delta":
            return {"k": "think", "d": ae.get("delta", "")}
        if ae.get("type") == "text_delta":
            return {"k": "text", "d": ae.get("delta", "")}
        return None
    if t == "message_end":
        m = e.get("message") or {}
        if m.get("role") == "assistant" and m.get("stopReason") == "error":
            return {"k": "error", "d": m.get("errorMessage") or "模型出错"}
        return None
    if t == "turn_start":
        return {"k": "turn"}
    if t == "tool_execution_start":
        return {"k": "tool", "id": e.get("toolCallId"), "name": e.get("toolName"), "args": short_args(e.get("args"))}
    if t == "tool_execution_update":
        return {"k": "tool_out", "id": e.get("toolCallId"), "out": result_text(e.get("partialResult"))[-4000:]}
    if t == "tool_execution_end":
        return {"k": "tool_end", "id": e.get("toolCallId"), "out": clip(result_text(e.get("result"))),
                "err": bool(e.get("isError"))}
    if t == "compaction_start":
        return {"k": "note", "d": "上下文快满了，正在压缩历史…"}
    if t == "compaction_end":
        r = e.get("result") or {}
        return {"k": "note", "d": "压缩完成（原 %s token）" % r.get("tokensBefore", "?") if r else
                "压缩失败：%s" % (e.get("errorMessage") or "已取消")}
    if t == "auto_retry_start":
        return {"k": "note", "d": "出错重试（第 %s 次）：%s" % (e.get("attempt"), (e.get("errorMessage") or "")[:200])}
    if t == "response" and not e.get("success", True):
        return {"k": "error", "d": e.get("error") or "pi 拒绝了这条消息"}
    if t == "extension_ui_request" and e.get("method") in ("select", "confirm", "input", "editor"):
        a.send({"type": "extension_ui_response", "id": e.get("id"), "cancelled": True})
        return None
    if t == "agent_end":
        tokens = None
        for m in reversed(e.get("messages") or []):
            if m.get("role") == "assistant" and m.get("usage"):
                tokens = m["usage"].get("totalTokens")
                break
        return {"k": "done", "tokens": tokens}
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return hmac.compare_digest(self.headers.get("X-LLM-Key", ""), KEY)

    def do_GET(self):
        if not self.authed():
            return self.send_json(401, {"error": "bad key"})
        if self.path.rstrip("/") == "/api/status":
            return self.send_json(200, status())
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.authed():
            return self.send_json(401, {"error": "bad key"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send_json(413, {"error": "too large"})
        raw = self.rfile.read(n) if n else b""
        path = self.path.rstrip("/")
        try:
            if path == "/api/start":
                start_model()
                return self.send_json(200, status())
            if path == "/api/stop":
                stop_model()
                return self.send_json(200, status())
            if path == "/v1/chat/completions":
                return self.chat(json.loads(raw or b"{}"))
            if path == "/agent/run":
                return self.agent_run(json.loads(raw or b"{}"))
            if path == "/agent/reset":
                sid = str(json.loads(raw or b"{}").get("sid", ""))
                if not SID.match(sid):
                    return self.send_json(400, {"error": "bad sid"})
                if RUNNING["sid"] == sid:
                    return self.send_json(409, {"error": "这个会话的 Agent 正在跑"})
                agent_drop(sid, wipe=True)
                return self.send_json(200, {"ok": True})
        except Exception as e:
            return self.send_json(500, {"error": str(e)})
        return self.send_json(404, {"error": "not found"})

    def chat(self, req):
        if not (alive() and model_ready()):
            return self.send_json(409, {"error": "模型没有在运行"})
        payload = {"model": MODEL_PATH, "messages": req.get("messages", []), "stream": True,
                   "max_tokens": int(req.get("max_tokens") or 8192), "temperature": float(req.get("temperature", 0.6))}
        conn = http.client.HTTPConnection("127.0.0.1", MODEL_PORT, timeout=900)
        conn.request("POST", "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        up = conn.getresponse()
        self.send_response(up.status)
        self.send_header("Content-Type", up.getheader("Content-Type", "text/event-stream"))
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = up.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    def agent_run(self, req):
        sid = str(req.get("sid", ""))
        if not SID.match(sid):
            return self.send_json(400, {"error": "bad sid"})
        if not (alive() and model_ready()):
            return self.send_json(409, {"error": "模型没有在运行"})
        cwd = os.path.abspath(os.path.expanduser(str(req.get("cwd") or "").strip() or os.path.join(AGENT_WORK, sid)))
        if not RUN_LOCK.acquire(blocking=False):
            return self.send_json(409, {"error": "另一个 Agent 会话正在跑，等它结束或停掉它"})
        try:
            RUNNING["sid"] = sid
            try:
                a = agent_get(sid, cwd, str(req.get("system") or ""))
            except OSError as e:
                return self.send_json(500, {"error": "起不了 pi：%s" % e})
            while not a.q.empty():   # 上次残留的事件
                a.q.get_nowait()
            msg = {"type": "prompt", "message": str(req.get("text") or "")}
            imgs = [{"type": "image", "data": i["data"], "mimeType": i["mimeType"]}
                    for i in (req.get("images") or []) if i.get("data") and i.get("mimeType")]
            if imgs:
                msg["images"] = imgs
            a.send(msg)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            gone, aborted_at = False, None

            def out(b):
                nonlocal gone
                if gone:
                    return
                try:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(b), b))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    gone = True

            def peer_closed():   # 调用方关了连接：socket 可读且读到 EOF
                try:
                    r, _, _ = select.select([self.connection], [], [], 0)
                    return bool(r) and self.connection.recv(1, socket.MSG_PEEK) == b""
                except OSError:
                    return True

            out(("data: %s\n\n" % json.dumps({"k": "start", "cwd": cwd}, ensure_ascii=False)).encode())
            quiet = 0
            while True:
                if not gone and peer_closed():
                    gone = True
                if gone and aborted_at is None:
                    aborted_at = time.time()
                    try:
                        a.send({"type": "abort"})
                    except OSError:
                        break
                if aborted_at and time.time() - aborted_at > 30:
                    agent_drop(sid)   # abort 没收住就整个关掉，下次 --continue 接上
                    break
                try:
                    e = a.q.get(timeout=1)
                    quiet = 0
                except queue.Empty:
                    quiet += 1
                    if quiet % 15 == 0:
                        out(b": ping\n\n")
                    continue
                if e is None:
                    out(("data: %s\n\n" % json.dumps({"k": "error", "d": "pi 进程退出了，详见 ~/llm-web/logs/agent.log"},
                                                     ensure_ascii=False)).encode())
                    agent_drop(sid)
                    break
                ev = slim(e, a)
                if ev:
                    out(("data: %s\n\n" % json.dumps(ev, ensure_ascii=False)).encode())
                if e.get("type") == "agent_end" or (e.get("type") == "response" and not e.get("success", True)):
                    break
            if not gone:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass
        finally:
            a_ = AGENTS.get(sid)
            if a_:
                a_.last = time.time()
            RUNNING["sid"] = None
            RUN_LOCK.release()


def main():
    os.makedirs(os.path.dirname(MODEL_LOG), exist_ok=True)
    threading.Thread(target=agent_reaper, daemon=True).start()

    def bye(*_):
        for s in list(AGENTS):
            agent_drop(s)
        stop_model()
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    srv = ThreadingHTTPServer(LISTEN, Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
