#!/usr/bin/env python3
"""curryPlan 聊天页的后端：会话和图片存在本机，模型在 Mac 上（node.py，经 ssh -R 映射到本机端口）。

nginx 把 /api/chat/ 去掉前缀转到 127.0.0.1:PORT，前面挡着 curryPlan 的口令 cookie，所以这里不再验登录；
POST 另外要求 X-Requested-With: curryplan-chat（跨站表单带不上这个头）。

GET  /convs[?q=词]            -> [{id, title, updated, n}]，新的在前；q 在标题和正文里搜
POST /convs {title?}           -> 新会话
GET  /convs/ID                 -> 整个会话 {id, title, system, created, updated, messages}
POST /convs/ID {title?, system?} -> 改标题或设定（system prompt）
POST /convs/ID/delete          -> 删除会话（图片按内容去重、可能被别的会话引用，留在 img/ 不删）
POST /convs/ID/send {text, images: [dataURL], regenerate?, max_tokens?}
     -> 先存用户消息（regenerate 时去掉末尾的回答、复用最后一条用户消息），再把模型的 SSE 原样流给页面，
        结束或页面断开时把回答（含思考过程、用时、速度）存进会话
GET  /img/NAME                 -> 会话里的图片
GET  /model                    -> 模型状态 {state: running|loading|stopped|external|offline, ...}
POST /model/start, /model/stop -> 启动、停止 Mac 上的模型

数据目录 ~/curry-chat：conv/ID.json、img/、chat.env（NODE=http://127.0.0.1:18090，NODE_KEY=与 Mac 上 node.key 相同）。
"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.path.expanduser("~/curry-chat")
CONV = os.path.join(DATA, "conv")
IMG = os.path.join(DATA, "img")
PORT = 8767
MAX_BODY = 48 * 1024 * 1024
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CONV_ID = re.compile(r"^[a-z0-9]{12}$")
IMG_NAME = re.compile(r"^[0-9a-f]{24}\.(jpg|png|webp|gif)$")
MAGIC = [(b"\xff\xd8\xff", "jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
         (b"GIF8", "gif", "image/gif"), (b"RIFF", "webp", "image/webp")]
MIME = {ext: mime for _, ext, mime in MAGIC}
lock = threading.Lock()


def read_env():
    env = {}
    with open(os.path.join(DATA, "chat.env"), encoding="utf-8") as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#"):
                env[k] = v
    return env


def now():
    return int(time.time())


def conv_path(cid):
    return os.path.join(CONV, cid + ".json")


def conv_load(cid):
    with open(conv_path(cid), encoding="utf-8") as f:
        return json.load(f)


def conv_save(c):
    c["updated"] = now()
    tmp = conv_path(c["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, conv_path(c["id"]))


def save_image(data_url):
    """dataURL -> 存到 img/，返回文件名；按内容去重，只认 jpg/png/gif/webp。"""
    m = re.match(r"^data:image/[a-z+.-]+;base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError("图片格式不对")
    raw = base64.b64decode(m.group(1), validate=False)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片太大（上限 8 MB）")
    ext = next((e for sig, e, _ in MAGIC if raw.startswith(sig) and (e != "webp" or raw[8:12] == b"WEBP")), None)
    if not ext:
        raise ValueError("只支持 jpg、png、gif、webp")
    name = hashlib.sha256(raw).hexdigest()[:24] + "." + ext
    path = os.path.join(IMG, name)
    if not os.path.exists(path):
        with open(path + ".tmp", "wb") as f:
            f.write(raw)
        os.replace(path + ".tmp", path)
    return name


def image_data_url(name):
    with open(os.path.join(IMG, name), "rb") as f:
        return "data:%s;base64,%s" % (MIME[name.rsplit(".", 1)[1]], base64.b64encode(f.read()).decode())


def model_messages(c):
    out = []
    if c.get("system", "").strip():
        out.append({"role": "system", "content": c["system"].strip()})
    for m in c["messages"]:
        if m["role"] == "user":
            parts = [{"type": "image_url", "image_url": {"url": image_data_url(n)}} for n in m.get("images", [])]
            if m.get("text"):
                parts.append({"type": "text", "text": m["text"]})
            out.append({"role": "user", "content": parts})
        elif m.get("text"):
            out.append({"role": "assistant", "content": m["text"]})
    return out


def node_call(method, path, body=None, timeout=10):
    env = read_env()
    u = urllib.parse.urlsplit(env["NODE"])
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    headers = {"X-LLM-Key": env["NODE_KEY"]}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, data, headers)
    return conn, conn.getresponse()


def model_status():
    try:
        conn, r = node_call("GET", "/api/status", timeout=5)
        s = json.loads(r.read() or b"{}")
        conn.close()
        return s if r.status == 200 else {"state": "offline", "error": s.get("error", r.status)}
    except Exception as e:
        return {"state": "offline", "error": type(e).__name__}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("请求太大")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p = u.path.rstrip("/")
        try:
            if p == "/convs":
                q = (urllib.parse.parse_qs(u.query).get("q") or [""])[0].strip().lower()
                return self.send_json(200, self.list_convs(q))
            m = re.match(r"^/convs/([a-z0-9]+)$", p)
            if m and CONV_ID.match(m.group(1)):
                return self.send_json(200, conv_load(m.group(1)))
            m = re.match(r"^/img/(.+)$", p)
            if m and IMG_NAME.match(m.group(1)):
                with open(os.path.join(IMG, m.group(1)), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", MIME[m.group(1).rsplit(".", 1)[1]])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=31536000, immutable")
                self.end_headers()
                return self.wfile.write(data)
            if p == "/model":
                return self.send_json(200, model_status())
        except FileNotFoundError:
            return self.send_json(404, {"error": "没有这个会话或图片"})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("X-Requested-With") != "curryplan-chat":
            return self.send_json(403, {"error": "bad request"})
        p = urllib.parse.urlsplit(self.path).path.rstrip("/")
        try:
            if p == "/convs":
                b = self.body()
                c = {"id": secrets.token_hex(6), "title": (b.get("title") or "新对话")[:80], "system": "",
                     "created": now(), "messages": []}
                with lock:
                    conv_save(c)
                return self.send_json(200, c)
            if p in ("/model/start", "/model/stop"):
                conn, r = node_call("POST", "/api/" + p.split("/")[-1], {}, timeout=30)
                s = json.loads(r.read() or b"{}")
                conn.close()
                return self.send_json(r.status, s)
            m = re.match(r"^/convs/([a-z0-9]+)(/(delete|send))?$", p)
            if not m or not CONV_ID.match(m.group(1)):
                return self.send_json(404, {"error": "not found"})
            cid, act = m.group(1), m.group(3)
            if act == "delete":
                with lock:
                    os.remove(conv_path(cid))
                return self.send_json(200, {"ok": True})
            if act == "send":
                return self.send(cid, self.body())
            b = self.body()
            with lock:
                c = conv_load(cid)
                if "title" in b:
                    c["title"] = str(b["title"]).strip()[:80] or c["title"]
                if "system" in b:
                    c["system"] = str(b["system"])[:8000]
                conv_save(c)
            return self.send_json(200, c)
        except FileNotFoundError:
            return self.send_json(404, {"error": "没有这个会话"})
        except (ValueError, KeyError) as e:
            return self.send_json(400, {"error": str(e)})
        except OSError as e:
            return self.send_json(502, {"error": "连不上模型节点：%s" % type(e).__name__})

    def list_convs(self, q):
        out = []
        for name in os.listdir(CONV):
            if not name.endswith(".json"):
                continue
            try:
                c = json.load(open(os.path.join(CONV, name), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if q and q not in c["title"].lower() and not any(q in (m.get("text") or "").lower() for m in c["messages"]):
                continue
            out.append({"id": c["id"], "title": c["title"], "updated": c.get("updated", 0), "n": len(c["messages"])})
        return sorted(out, key=lambda x: -x["updated"])

    def send(self, cid, b):
        with lock:
            c = conv_load(cid)
            if b.get("regenerate"):
                while c["messages"] and c["messages"][-1]["role"] == "assistant":
                    c["messages"].pop()
                if not c["messages"]:
                    raise ValueError("没有可以重新生成的消息")
            else:
                text = str(b.get("text") or "").strip()
                imgs = b.get("images") or []
                if not text and not imgs:
                    raise ValueError("消息是空的")
                if len(imgs) > MAX_IMAGES:
                    raise ValueError("一条消息最多 %d 张图" % MAX_IMAGES)
                names = [save_image(u) for u in imgs]
                if not c["messages"] and c["title"] == "新对话":
                    c["title"] = (text.splitlines()[0][:30] if text else "图片对话") or "新对话"
                c["messages"].append({"role": "user", "text": text, "images": names, "at": now()})
            conv_save(c)
            payload = {"messages": model_messages(c), "max_tokens": int(b.get("max_tokens") or 8192)}
        t0 = time.time()
        reply = {"role": "assistant", "text": "", "reasoning": "", "at": now()}
        tps = None
        try:
            conn, up = node_call("POST", "/v1/chat/completions", payload, timeout=900)
        except OSError as e:
            return self.send_json(502, {"error": "连不上模型节点：%s" % type(e).__name__})
        if up.status != 200:
            err = up.read().decode(errors="replace")[:300]
            conn.close()
            return self.send_json(up.status, {"error": err})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        buf = b""
        client_gone = False
        try:
            while True:
                chunk = up.read1(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:") or line.endswith(b"[DONE]"):
                        continue
                    try:
                        j = json.loads(line[5:])
                    except ValueError:
                        continue
                    d = (j.get("choices") or [{}])[0].get("delta") or {}
                    reply["reasoning"] += d.get("reasoning_content") or d.get("reasoning") or ""
                    reply["text"] += d.get("content") or ""
                    tps = (j.get("timings") or {}).get("predicted_per_second") or tps
                try:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    client_gone = True   # 页面点了停止：关掉上游，模型那边随之停下
                    break
            if not client_gone:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            client_gone = True
        finally:
            conn.close()
            reply["secs"] = round(time.time() - t0, 1)
            if tps:
                reply["tps"] = round(tps, 1)
            if client_gone:
                reply["stopped"] = True
            with lock:
                try:
                    c = conv_load(cid)
                    c["messages"].append(reply)
                    conv_save(c)
                except FileNotFoundError:
                    pass   # 生成过程中会话被删了


def main():
    os.makedirs(CONV, exist_ok=True)
    os.makedirs(IMG, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
