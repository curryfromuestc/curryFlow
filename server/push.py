#!/usr/bin/env python3
"""Web Push for curryPlan: the channel only (who triggers a notification is the caller's business).

Sending follows RFC 8030, the payload is encrypted per RFC 8291 (aes128gcm, one record) and each request is
signed per RFC 8292 (VAPID, ES256).  server.py imports this module for /push/* and /notify;
`python push.py selftest` checks the crypto against the values printed in the RFCs.

Files, all under ~/curry-site/:
  vapid.pem       the VAPID private key, made once, mode 600 (a new key invalidates every subscription)
  notify.key      shared secret for POST /notify, made once, mode 600: nginx forwards /api/* from 127.0.0.1
                  as well, so the peer address cannot tell a local caller from one on the internet
  push-subs.json  the subscriptions, mode 600 (an endpoint URL is a capability); one the push service calls gone
                  (404/410) is dropped, unless it was made in the last ten minutes
  push.env        optional.  FCM_PROXY_CMD=<command with %h and %p>: its stdin/stdout become the TCP stream to
                  fcm.googleapis.com:443, like ssh's ProxyCommand (TLS still ends at Google).  This server
                  cannot reach Google directly.
  push.log        one line per send, without endpoints
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import shlex
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DATA_DIR = os.path.expanduser("~/curry-site")
SITE = "https://plan.example:8443"   # the VAPID "sub": the site itself, never a person's address
SUBS = os.path.join(DATA_DIR, "push-subs.json")
LOG = os.path.join(DATA_DIR, "push.log")
CST = timezone(timedelta(hours=8))
RS = 4096                             # record size in the aes128gcm header
MAX_PLAINTEXT = 3993                  # RFC 8291 section 4: 4096 - 86 (header) - 1 (padding delimiter) - 16 (tag)
TOKEN_LIFE = 12 * 3600                # RFC 8292: "exp" at most 24 hours ahead
TTL = 86400                           # the push service drops a notification it could not deliver within a day
TIMEOUT = 10
FRESH = 600                           # a subscription this young is not dropped on 404/410, see send()
MAX_SUBS = 50
FCM_HOST = "fcm.googleapis.com"
# endpoints are only accepted on these hosts: the server POSTs to whatever a subscription names
SERVICES = (("web.push.apple.com", "苹果"), (".push.apple.com", "苹果"), (FCM_HOST, "谷歌"),
            (".push.services.mozilla.com", "Mozilla"), (".notify.windows.com", "微软"))
_lock = threading.Lock()
_vapid = None


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    text = "".join(str(text).split())
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def point(public_key):
    """The 65-octet uncompressed form (0x04 || X || Y) both RFCs use for P-256 public keys."""
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def from_point(octets):
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), octets)


# ---- RFC 8291: message encryption -----------------------------------------------------------------------------

def _hkdf(salt, ikm, info, length):
    """HKDF-SHA-256 for outputs of at most one block, written out as RFC 8291 section 3.4 does."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return prk, hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _keys(ecdh_secret, auth_secret, salt, ua_public, as_public, trace):
    key_info = b"WebPush: info\x00" + ua_public + as_public
    prk_key, ikm = _hkdf(auth_secret, ecdh_secret, key_info, 32)
    prk, cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    _, nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if trace is not None:
        trace.update(ecdh_secret=ecdh_secret, prk_key=prk_key, key_info=key_info, ikm=ikm, prk=prk, cek=cek, nonce=nonce)
    return cek, nonce


def encrypt(plaintext, ua_public, auth_secret, salt=None, as_private=None, trace=None):
    """The request body for one push message.  salt and as_private are fresh per message unless a test fixes them."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("payload of %d octets, the limit is %d" % (len(plaintext), MAX_PLAINTEXT))
    if len(auth_secret) != 16:
        raise ValueError("the authentication secret must be 16 octets")
    salt = salt or os.urandom(16)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = point(as_private.public_key())
    ecdh_secret = as_private.exchange(ec.ECDH(), from_point(ua_public))
    cek, nonce = _keys(ecdh_secret, auth_secret, salt, ua_public, as_public, trace)
    header = salt + struct.pack(">IB", RS, len(as_public)) + as_public
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)   # 0x02: the last (only) record, no padding
    if trace is not None:
        trace.update(header=header, ciphertext=ciphertext)
    return header + ciphertext


def decrypt(body, ua_private, auth_secret):
    """What the receiving user agent does with a request body; used to check encrypt()."""
    salt, (rs, idlen) = body[:16], struct.unpack(">IB", body[16:21])
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    if idlen != 65 or len(ciphertext) > rs or len(ciphertext) < 17:
        raise ValueError("not a single-record aes128gcm push message")
    ecdh_secret = ua_private.exchange(ec.ECDH(), from_point(as_public))
    cek, nonce = _keys(ecdh_secret, auth_secret, salt, point(ua_private.public_key()), as_public, None)
    record = AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x00")
    if not record.endswith(b"\x02"):
        raise ValueError("padding delimiter is not 0x02")
    return record[:-1]


# ---- RFC 8292: VAPID ------------------------------------------------------------------------------------------

def _secret_file(name, make):
    """Reads ~/curry-site/NAME; the first call creates it with mode 600.  Callers hold _lock."""
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        data = make()
        os.makedirs(DATA_DIR, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
        return data


def vapid_key():
    global _vapid
    with _lock:
        if _vapid is None:
            pem = _secret_file("vapid.pem", lambda: ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            _vapid = serialization.load_pem_private_key(pem, None)   # a damaged file is an error, not a reason for a new key
        return _vapid


def notify_key():
    with _lock:
        return _secret_file("notify.key", lambda: secrets.token_urlsafe(32).encode() + b"\n").strip()


def setup():
    """Makes the two secrets at service start, so the `notify` command finds its key before the first request."""
    vapid_key()
    notify_key()


def origin(url):
    """The origin of a push resource (RFC 6454 serialization: no path, no default port)."""
    u = urlsplit(url)
    return "https://" + u.hostname + (":%d" % u.port if u.port not in (None, 443) else "")


def vapid_header(endpoint, key=None, now=None):
    key = key or vapid_key()
    part = lambda obj: b64u(json.dumps(obj, separators=(",", ":")).encode())
    signed = part({"typ": "JWT", "alg": "ES256"}) + "." + part(
        {"aud": origin(endpoint), "exp": int(now or time.time()) + TOKEN_LIFE, "sub": SITE})
    r, s = decode_dss_signature(key.sign(signed.encode(), ec.ECDSA(hashes.SHA256())))
    # JWS ES256 (RFC 7518 section 3.4): R and S as two 32-octet big-endian integers, not the DER the library returns
    return "vapid t=%s.%s, k=%s" % (signed, b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big")), b64u(point(key.public_key())))


def vapid_check(header, endpoint, now=None):
    """Checks an Authorization value against RFC 8292 as a push service would; returns the claims or raises ValueError."""
    scheme, _, rest = header.partition(" ")
    params = dict(p.strip().split("=", 1) for p in rest.split(","))
    if scheme != "vapid" or set(params) != {"t", "k"}:
        raise ValueError("not 'vapid t=..., k=...'")
    head, claims, sig = params["t"].split(".")
    k, sig = unb64u(params["k"]), unb64u(sig)
    if len(k) != 65 or k[0] != 4:
        raise ValueError("k is not an uncompressed P-256 point")
    if len(sig) != 64:
        raise ValueError("signature is %d octets, ES256 needs 64" % len(sig))
    try:
        from_point(k).verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                             (head + "." + claims).encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise ValueError("signature does not verify under k")
    head, claims = json.loads(unb64u(head)), json.loads(unb64u(claims))
    if head.get("alg") != "ES256" or head.get("typ") != "JWT":
        raise ValueError("JWT header is %r" % head)
    if claims.get("aud") != origin(endpoint):
        raise ValueError("aud %r is not the origin of the push resource" % claims.get("aud"))
    now = int(now or time.time())
    if type(claims.get("exp")) is not int or not now < claims["exp"] <= now + 86400:
        raise ValueError("exp %r is not within 24 hours of now" % claims.get("exp"))
    if "sub" in claims and not str(claims["sub"]).startswith(("https:", "mailto:")):
        raise ValueError("sub %r is neither https: nor mailto:" % claims["sub"])
    return claims


# ---- subscriptions --------------------------------------------------------------------------------------------

def log(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def service(host):
    host = (host or "").lower()
    for name, label in SERVICES:
        if host == name or (name.startswith(".") and host.endswith(name)):
            return label
    return None


def sub_id(endpoint):
    """A short name for a subscription that is safe to log and return."""
    return hashlib.sha256(endpoint.encode()).hexdigest()[:8]


def device(ua):
    for mark, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                       ("Windows", "Windows"), ("Linux", "Linux")):
        if mark in (ua or ""):
            return name
    return "未知设备"


def load_subs():
    try:
        with open(SUBS, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_subs(subs):
    tmp = SUBS + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SUBS)


def parse_sub(obj):
    """Validates what PushSubscription.toJSON() gave the page; returns (endpoint, p256dh, auth) or raises ValueError."""
    endpoint, keys = obj.get("endpoint"), obj.get("keys") or {}
    if not isinstance(endpoint, str) or not 0 < len(endpoint) <= 2000 or not isinstance(keys, dict):
        raise ValueError("订阅格式不对")
    u = urlsplit(endpoint)
    if u.scheme != "https" or u.username or u.port not in (None, 443) or not service(u.hostname):
        raise ValueError("不认识的推送服务：%s" % (u.hostname or endpoint[:40]))
    try:
        p256dh, auth = unb64u(keys.get("p256dh") or ""), unb64u(keys.get("auth") or "")
        from_point(p256dh)
    except Exception:
        raise ValueError("订阅里的 p256dh 不是 P-256 公钥")
    if len(p256dh) != 65 or len(auth) != 16:
        raise ValueError("订阅里的密钥长度不对")
    return endpoint, b64u(p256dh), b64u(auth)


# ---- sending --------------------------------------------------------------------------------------------------

def env():
    out = {}
    try:
        with open(os.path.join(DATA_DIR, "push.env"), encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


class TunnelError(OSError):
    pass


class Conn(http.client.HTTPSConnection):
    """HTTPS to one push service.  With proxy_cmd the TCP stream is that command's stdin/stdout; the TLS session
    inside it is still verified against the push service's own name."""

    def __init__(self, host, proxy_cmd=None):
        self.ctx = ssl.create_default_context()
        super().__init__(host, 443, timeout=TIMEOUT, context=self.ctx)
        self.proxy_cmd, self.procs = proxy_cmd, []

    def connect(self):
        raw, proc = self.relay() if self.proxy_cmd else (self.dial(), None)
        raw.settimeout(self.timeout)
        try:
            self.sock = self.ctx.wrap_socket(raw, server_hostname=self.host)
        except ssl.SSLCertVerificationError:
            raw.close()
            raise
        except OSError as e:
            raw.close()
            if proc is None:
                raise
            # the command exited, or gave no stream in time: its stderr says why
            raise TunnelError(self.reap(proc) or str(e) or type(e).__name__)

    def dial(self):
        """A TCP connection within self.timeout in total, however many addresses the name has (the library's own
        create_connection waits that long for each of them)."""
        deadline, error = time.monotonic() + self.timeout, None
        for family, kind, proto, _, address in socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            s = socket.socket(family, kind, proto)
            s.settimeout(min(left, max(left / 2, 3)))
            try:
                s.connect(address)
                return s
            except OSError as e:
                error = e
                s.close()
        raise error or TimeoutError("timed out")

    def relay(self):
        argv = [a.replace("%h", self.host).replace("%p", str(self.port)) for a in shlex.split(self.proxy_cmd)]
        ours, theirs = socket.socketpair()
        try:
            proc = subprocess.Popen(argv, stdin=theirs, stdout=theirs, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            ours.close()
            raise TunnelError("%s: %s" % (argv[0], e.strerror or e))
        finally:
            theirs.close()
        self.procs.append(proc)
        return ours, proc

    @staticmethod
    def reap(proc):
        if proc.poll() is None:
            proc.terminate()
        try:
            err = proc.communicate(timeout=3)[1]
        except subprocess.TimeoutExpired:
            proc.kill()
            err = proc.communicate()[1]
        lines = [x.strip() for x in err.decode("utf-8", "replace").splitlines() if x.strip()]
        return "；".join(lines[-2:])[:300]

    def finish(self):
        """close() alone must not stop the command: http.client closes before the last response body is read."""
        self.close()
        for proc in self.procs:
            self.reap(proc)
        self.procs = []


def send(conn, sub, payload):
    """POSTs one message to one subscription over conn.  Returns the outcome; `fatal` means the service was not reached."""
    u = urlsplit(sub["endpoint"])
    label = service(u.hostname)
    out = {"id": sub_id(sub["endpoint"]), "service": label, "device": device(sub.get("ua")), "ok": False, "status": None,
           "reason": "", "removed": False}
    try:
        conn.request("POST", u.path + ("?" + u.query if u.query else ""),
                     encrypt(payload, unb64u(sub["p256dh"]), unb64u(sub["auth"])),
                     {"TTL": str(TTL), "Urgency": "high", "Content-Encoding": "aes128gcm",
                      "Content-Type": "application/octet-stream", "Authorization": vapid_header(sub["endpoint"])})
        r = conn.getresponse()
        text = " ".join(r.read().decode("utf-8", "replace").split())[:300]
    except TunnelError as e:
        conn.close()
        out.update(reason="服务器连不上%s推送：到 %s 的转发起不来（%s）" % (label, u.hostname, e), fatal=True)
        return out
    except ssl.SSLCertVerificationError as e:
        conn.close()
        out.update(reason="%s 的 TLS 证书校验没过（%s）" % (u.hostname, e.verify_message or e), fatal=True)
        return out
    except (OSError, http.client.HTTPException) as e:
        conn.close()
        out.update(reason="服务器连不上%s推送（%s：%s）" % (label, u.hostname, str(e) or type(e).__name__), fatal=True)
        return out
    out["status"] = r.status
    if 200 <= r.status < 300:
        out["ok"] = True
    elif r.status in (404, 410):
        # Gone means gone, except right after the browser made the subscription: Google answered 410 once in testing
        # for one made a second earlier (13 others were fine, the cause is not known).  Losing a device over that
        # would be silent, keeping a dead subscription for ten minutes costs nothing.
        try:
            age = time.time() - datetime.strptime(sub["at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
        except (KeyError, ValueError):
            age = FRESH
        if age < FRESH:
            out["reason"] = "%s推送不认这个订阅（%d：%s），但它是 %d 秒前刚建的，先留着：过几秒再发一次，一直这样就关闭通知再开启" % (
                label, r.status, text, age)
        else:
            out.update(removed=True, reason="订阅已失效，已删除（%s推送返回 %d：%s）" % (label, r.status, text))
    else:
        out["reason"] = "%s推送拒绝了（%d：%s）" % (label, r.status, text)
    return out


def deliver(subs, payload, what):
    """Sends payload to subs: one connection per push service, the services in parallel.  Records each outcome in
    the log and in the subscription file (dead subscriptions are dropped) and returns the outcomes."""
    groups = {}
    for sub in subs:
        groups.setdefault(urlsplit(sub["endpoint"]).hostname, []).append(sub)

    def one_service(item):
        host, members = item
        conn = Conn(host, env().get("FCM_PROXY_CMD") if host == FCM_HOST else None)
        outs, dead = [], None
        try:
            for sub in members:
                # once the service itself is unreachable the rest get the same answer without another wait
                out = dict(dead, id=sub_id(sub["endpoint"]), device=device(sub.get("ua"))) if dead else send(conn, sub, payload)
                dead = out if out.get("fatal") else dead
                outs.append(out)
        finally:
            conn.finish()
        return outs

    outs = []
    if groups:
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            for part in pool.map(one_service, groups.items()):
                outs += part
    at = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    by_id = {o["id"]: o for o in outs}
    with _lock:
        kept = []
        for sub in load_subs():
            o = by_id.get(sub_id(sub["endpoint"]))
            if o:
                sub["last"] = {"at": at, "ok": o["ok"], "reason": o["reason"]}
            if not (o and o["removed"]):
                kept.append(sub)
        save_subs(kept)
        for o in outs:
            o.pop("fatal", None)
            log("%s id=%s %s %s status=%s %s" % (what, o["id"], o["service"], o["device"], o["status"],
                                                 "ok" if o["ok"] else "FAILED " + o["reason"]))
    return outs


def message(title, body="", url=None, tag=None):
    """The JSON the service worker receives.  Returns (payload, truncated); a body too long for one push message is cut."""
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("title 要 1 到 200 个字")
    if not isinstance(body, str):
        raise ValueError("body 要是字符串")
    if url is not None and not (isinstance(url, str) and len(url) <= 500 and
                                (url.startswith(("https://", "http://")) or (url.startswith("/") and not url.startswith("//")))):
        raise ValueError("url 要是 / 开头的站内路径或 http(s) 地址，最长 500")
    if tag is not None and not (isinstance(tag, str) and 0 < len(tag) <= 64):
        raise ValueError("tag 要是 1 到 64 个字的字符串")
    msg = {"title": title.strip(), "body": body, "url": url or "/"}
    if tag:
        msg["tag"] = tag
    encode = lambda: json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    truncated = False
    while len(encode()) > MAX_PLAINTEXT:
        over = len(encode()) - MAX_PLAINTEXT
        if len(msg["body"]) < 2:
            raise ValueError("title、url、tag 加起来超过了一条推送能装的 %d 字节" % MAX_PLAINTEXT)
        msg["body"] = msg["body"][:max(0, len(msg["body"]) - 2 - over // 4)] + "…"
        truncated = True
    return encode(), truncated


# ---- the HTTP interface (paths as server.py sees them: nginx strips /api) -------------------------------------

def api(method, path, body, headers):
    """Returns (status, object) for one request."""
    path = path.rstrip("/")
    if method == "GET" and path == "/push/key":
        return 200, {"key": b64u(point(vapid_key().public_key()))}
    if method != "POST":
        return 404, {"error": "not found"}

    if path == "/push/subscribe":
        try:
            endpoint, p256dh, auth = parse_sub(body)
        except ValueError as e:
            return 400, {"error": str(e)}
        ua = str(headers.get("User-Agent") or "")[:200]
        with _lock:
            subs = load_subs()
            old = next((s for s in subs if s["endpoint"] == endpoint), None)
            if old is None and len(subs) >= MAX_SUBS:
                return 400, {"error": "订阅已经有 %d 个，先关掉不用的" % MAX_SUBS}
            new = dict(old or {"at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")}, endpoint=endpoint, p256dh=p256dh, auth=auth, ua=ua)
            if new != old:
                save_subs([s for s in subs if s is not old] + [new])
                log("subscribe id=%s %s %s%s" % (sub_id(endpoint), service(urlsplit(endpoint).hostname), device(ua), "" if old is None else " (updated)"))
        return 200, {"ok": True, "service": service(urlsplit(endpoint).hostname), "last": new.get("last")}

    if path == "/push/unsubscribe":
        endpoint = body.get("endpoint")
        with _lock:
            subs = load_subs()
            kept = [s for s in subs if s["endpoint"] != endpoint]
            if len(kept) != len(subs):
                save_subs(kept)
                log("unsubscribe id=%s" % sub_id(endpoint))
        return 200, {"ok": True, "removed": len(kept) != len(subs)}

    if path == "/push/test":
        with _lock:
            sub = next((s for s in load_subs() if s["endpoint"] == body.get("endpoint")), None)
        if sub is None:
            return 404, {"ok": False, "error": "服务器上没有这个订阅，重新开启一次", "removed": True}
        payload, _ = message("curryPlan 测试通知", "通道是通的 · " + datetime.now(CST).strftime("%H:%M:%S"), "/", "push-test")
        out = deliver([sub], payload, "test")[0]
        if out["ok"]:
            return 200, out
        return 502, dict(out, error=out["reason"])

    if path in ("/notify", "/api/notify"):
        if not hmac.compare_digest(str(headers.get("X-Notify-Key") or "").encode(), notify_key()):
            return 403, {"error": "notify 只给服务器本机用：要带 ~/curry-site/notify.key 里的密钥（用 ~/curry-site/notify 发）"}
        try:
            payload, truncated = message(body.get("title"), body.get("body") or "", body.get("url"), body.get("tag"))
        except ValueError as e:
            return 400, {"error": str(e)}
        with _lock:
            subs = load_subs()
        outs = deliver(subs, payload, "notify %r" % body["title"][:40])
        sent = sum(o["ok"] for o in outs)
        res = {"sent": sent, "failed": len(outs) - sent, "removed": sum(o["removed"] for o in outs), "truncated": truncated, "results": outs}
        if not outs:
            log("notify %r: no subscriptions" % body["title"][:40])
            return 200, dict(res, note="没有设备开启通知")
        return (200 if sent else 502), res

    return 404, {"error": "not found"}


# ---- selftest -------------------------------------------------------------------------------------------------

def selftest():
    """Checks the crypto in isolation.  The ways it can be wrong, written down before the code:
      1  an HKDF info string is wrong: no NUL terminator, or ua_public and as_public swapped in key_info
      2  the roles in the first HKDF are swapped (the authentication secret is the salt, the ECDH secret the IKM)
      3  the header is laid out wrong: salt(16) || rs(4, big-endian) || idlen(1) || keyid(65)
      4  the padding delimiter is missing or 0x01 instead of 0x02
      5  the nonce is not the first 12 octets, or gets XORed with a non-zero sequence number
      6  a public key goes out as DER/SPKI instead of the 65-octet uncompressed point
      7  base64url without padding does not decode
      8  the JWT signature is DER, or R/S are not left-padded to 32 octets (about 1 signature in 128 has a short one)
      9  aud carries a path or the default port; exp is more than 24 hours ahead, a float or a string
      10 k is not the key that signed
      11 the receiver accepts a tampered message or a wrong padding delimiter
      12 a payload over 3993 octets is not refused
    1-7 are settled by RFC 8291 appendix A (every intermediate value) and section 5 (the body); 8-10 by vapid_check,
    whose own reading of the format is checked on the token printed in RFC 8292 section 2.4; 11-12 by round trips."""
    failed = []

    def check(name, ok, detail=""):
        print(("ok    " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))
        if not ok:
            failed.append(name)

    # RFC 8291 appendix A and section 5, copied from the RFC text
    v = {
        "plaintext": "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24",
        "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
        "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
        "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
        "salt": "DGv6ra1nlYgDCS1FRnbzlw",
        "auth_secret": "BTBZMqHH6r4Tts7J_aSIgg",
        "ecdh_secret": "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs",
        "prk_key": "Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k",
        "key_info": "V2ViUHVzaDogaW5mbwAEJXGyvs3942BVGq8e0PTNNmwRzr5VX4m8t7GGpTM5FzFo7OLr4BhZe9MEebhuPI-OztV3"
                    "ylkYfpJGmQ22ggCLDgT-M_SrDepxkU21WCP3O1SUj0EwbZIHMtu5pZpTKGSCIA5Zent7wmC6HCJ5mFgJkuk5cwAvMBKiiujwa7t45ewP",
        "ikm": "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg",
        "prk": "09_eUZGrsvxChDCGRCdkLiDXrReGOEVeSCdCcPBSJSc",
        "cek": "oIhVW04MRdy2XN9CiKLxTg",
        "nonce": "4h_95klXJ5E_qnoN",
        "header": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
        "ciphertext": "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ",
        "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
    }
    private = lambda text: ec.derive_private_key(int.from_bytes(unb64u(text), "big"), ec.SECP256R1())
    as_private, ua_private = private(v["as_private"]), private(v["ua_private"])
    check("RFC 8291 A: as_public is the point of as_private", b64u(point(as_private.public_key())) == v["as_public"])
    check("RFC 8291 A: ua_public is the point of ua_private", b64u(point(ua_private.public_key())) == v["ua_public"])
    trace = {}
    body = encrypt(unb64u(v["plaintext"]), unb64u(v["ua_public"]), unb64u(v["auth_secret"]), unb64u(v["salt"]), as_private, trace)
    for name in ("ecdh_secret", "prk_key", "key_info", "ikm", "prk", "cek", "nonce", "header", "ciphertext"):
        check("RFC 8291 A: " + name, b64u(trace[name]) == v[name], "" if b64u(trace[name]) == v[name] else "got " + b64u(trace[name]))
    # the body printed in section 5 is 144 octets; the "Content-Length: 145" above it in the RFC is off by one
    check("RFC 8291 5: request body (%d octets)" % len(body), b64u(body) == v["body"])
    check("RFC 8291 5: the receiver's steps on the RFC's body give the plaintext",
          decrypt(unb64u(v["body"]), ua_private, unb64u(v["auth_secret"])) == b"When I grow up, I want to be a watermelon")

    # round trips with fresh keys, as a browser would hold them
    ua, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    for n in (0, 1, 41, 1000, MAX_PLAINTEXT):
        text = os.urandom(n)
        check("round trip, %d octets" % n, decrypt(encrypt(text, point(ua.public_key()), auth), ua, auth) == text)
    payload, _ = message("机器告警", "box-b 磁盘 97%", "/curryplan/machines/", "disk")
    check("round trip, a notification", json.loads(decrypt(encrypt(payload, point(ua.public_key()), auth), ua, auth)) ==
          {"title": "机器告警", "body": "box-b 磁盘 97%", "url": "/curryplan/machines/", "tag": "disk"})
    a, b = encrypt(b"same", point(ua.public_key()), auth), encrypt(b"same", point(ua.public_key()), auth)
    check("two messages share neither salt nor sender key", a[:16] != b[:16] and a[21:86] != b[21:86])
    body = bytearray(encrypt(b"tamper", point(ua.public_key()), auth))
    body[-1] ^= 1
    try:
        decrypt(bytes(body), ua, auth)
        check("a flipped bit is rejected", False)
    except InvalidTag:
        check("a flipped bit is rejected", True)
    try:
        decrypt(encrypt(b"x", point(ua.public_key()), auth), ua, os.urandom(16))
        check("a wrong authentication secret is rejected", False)
    except InvalidTag:
        check("a wrong authentication secret is rejected", True)
    try:   # a record that ends in 0x01 (the "more records follow" delimiter) must be discarded
        salt, sender = os.urandom(16), ec.generate_private_key(ec.SECP256R1())
        cek, nonce = _keys(sender.exchange(ec.ECDH(), ua.public_key()), auth, salt, point(ua.public_key()), point(sender.public_key()), None)
        decrypt(salt + struct.pack(">IB", RS, 65) + point(sender.public_key()) + AESGCM(cek).encrypt(nonce, b"x\x01", None), ua, auth)
        check("padding delimiter 0x01 is rejected", False)
    except ValueError:
        check("padding delimiter 0x01 is rejected", True)
    try:
        encrypt(b"x" * (MAX_PLAINTEXT + 1), point(ua.public_key()), auth)
        check("3994 octets are refused", False)
    except ValueError:
        check("3994 octets are refused", True)
    long, cut = message("长", "字" * 5000)
    check("an over-long body is cut to fit and says so", cut and len(long) <= MAX_PLAINTEXT and json.loads(long)["body"].endswith("…"),
          "%d octets" % len(long))

    # RFC 8292 section 2.4: the printed token must verify under the printed key with the reading vapid_check has
    rfc_t = ("eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9.eyJhdWQiOiJodHRwczovL3B1c2guZXhhbXBsZS5uZXQiLCJleHAiOjE0NTM1MjM3NjgsInN1YiI6Im1ha"
             "Wx0bzpwdXNoQGV4YW1wbGUuY29tIn0.i3CYb7t4xfxCDquptFOepC9GAu_HLGkMlMuCGSK2rpiUfnK9ojFwDXb1JrErtmysazNjjvW2L9OkSSHzvoD1oA")
    rfc_k = "BA1Hxzyi1RUM1b5wjxsn7nGxAszw2u61m164i3MrAIxHF6YK5h4SDYic-dRuU_RCPCfA5aq9ojSwk5Y2EmClBPs"
    try:
        claims = vapid_check("vapid t=%s, k=%s" % (rfc_t, rfc_k), "https://push.example.net/p/JzLQ3raZJfFBR0aqvOMsLrt54w4rJUsV", now=1453523768 - 3600)
        check("RFC 8292 2.4: the example token verifies under the example key", claims == {
            "aud": "https://push.example.net", "exp": 1453523768, "sub": "mailto:push@example.com"})
    except ValueError as e:
        check("RFC 8292 2.4: the example token verifies under the example key", False, str(e))
    key, now = ec.generate_private_key(ec.SECP256R1()), int(time.time())
    for endpoint, aud in (("https://web.push.apple.com/QGk3", "https://web.push.apple.com"),
                          ("https://fcm.googleapis.com:443/fcm/send/a:b", "https://fcm.googleapis.com"),
                          ("https://updates.push.services.mozilla.com/wpush/v2/gAAAA?x=1", "https://updates.push.services.mozilla.com")):
        try:
            claims = vapid_check(vapid_header(endpoint, key, now), endpoint, now)
            check("VAPID for " + aud, claims == {"aud": aud, "exp": now + TOKEN_LIFE, "sub": SITE} and "@" not in json.dumps(claims),
                  "ES256, exp in %d h, sub %s" % (TOKEN_LIFE // 3600, claims["sub"]))
        except ValueError as e:
            check("VAPID for " + aud, False, str(e))
    try:
        for _ in range(600):   # enough signatures to meet R or S with a leading zero octet
            vapid_check(vapid_header("https://web.push.apple.com/x", key, now), "https://web.push.apple.com/x", now)
        check("600 more tokens verify (64-octet signatures every time)", True)
    except ValueError as e:
        check("600 more tokens verify (64-octet signatures every time)", False, str(e))
    check("k is the signing key", unb64u(vapid_header("https://web.push.apple.com/x", key).rsplit("k=", 1)[1]) == point(key.public_key()))
    for name, header, endpoint, at in (
            ("a token for another origin is caught", vapid_header("https://web.push.apple.com/x", key, now), "https://fcm.googleapis.com/x", now),
            ("a token past its exp is caught", vapid_header("https://web.push.apple.com/x", key, now - TOKEN_LIFE - 1), "https://web.push.apple.com/x", now),
            ("a token with exp over 24 h ahead is caught", vapid_header("https://web.push.apple.com/x", key, now + 13 * 3600), "https://web.push.apple.com/x", now)):
        try:
            vapid_check(header, endpoint, at)
            check(name, False)
        except ValueError:
            check(name, True)
    other = vapid_header("https://web.push.apple.com/x", ec.generate_private_key(ec.SECP256R1()), now)
    try:
        vapid_check(vapid_header("https://web.push.apple.com/x", key, now).rsplit("k=", 1)[0] + "k=" + other.rsplit("k=", 1)[1], "https://web.push.apple.com/x", now)
        check("a token under somebody else's k is caught", False)
    except ValueError:
        check("a token under somebody else's k is caught", True)
    print("RESULT " + ("fail: " + "; ".join(failed) if failed else "pass"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else "usage: push.py selftest")
