#!/usr/bin/env python3
"""聊天页的模型节点：跑在有模型的 Mac 上，按需启动、停止 mlx_vlm.server，转发对话请求。

只听 127.0.0.1:8090，经 ssh -R 映射到 curryPlan 机的 127.0.0.1:18090，由那里的 site/chat/server.py 调用。
每个请求都要带 X-LLM-Key（与 node.key 文件内容相同；curryPlan 机上写在 ~/curry-chat/chat.env 的 NODE_KEY）。

GET  /api/status            -> {state: running|loading|stopped|external, model, since, mem_gb}
POST /api/start, /api/stop  -> 启动、停止模型（mlx_vlm.server 在 127.0.0.1:MODEL_PORT）
POST /v1/chat/completions {messages, max_tokens?, temperature?} -> 模型的 SSE 原样流回；调用方断开时关掉上游，生成随之停下

部署：~/llm-web/node.py，配置在同目录 node.json：{"model_path", "model_name", "server_bin"}；LaunchAgent com.curry.llm-web。
"""
import hmac
import http.client
import json
import os
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
            "mem_gb": footprint_gb(PROC["p"].pid) if alive() else None}


def start_model():
    with LOCK:
        if alive():
            return
        if port_open(MODEL_PORT):
            raise RuntimeError("端口 %d 被别的程序占着" % MODEL_PORT)
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        PROC["p"] = subprocess.Popen(
            [SERVER_BIN, "--host", "127.0.0.1", "--port", str(MODEL_PORT), "--model", MODEL_PATH, "--enable-thinking"],
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


def main():
    os.makedirs(os.path.dirname(MODEL_LOG), exist_ok=True)

    def bye(*_):
        stop_model()
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    srv = ThreadingHTTPServer(LISTEN, Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
