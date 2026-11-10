"""Faults on the sending side, against the stand-in push service (fake_service.py) behind FCM_PROXY_CMD (relay.py): each check
reproduces a problem found in review of e41974c.  Endpoints here are made up; none is printed.
env: SITE, DATA (the server's ~/curry-site), FAKE (fake_service.py's directory), FAKE_PORT, SERVER_PID.
One line per check ("ok" / "FAIL"); exit 1 when any failed."""
import base64, json, os, secrets, string, subprocess, sys, time, urllib.error, urllib.request
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

SITE, DATA, FAKE = os.environ["SITE"], os.environ["DATA"], os.environ["FAKE"]
RELAY = "FCM_PROXY_CMD=%s %s %s\n" % (sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.py"), os.environ["FAKE_PORT"])
SUBS, LOG, ENV, KEY = (os.path.join(DATA, n) for n in ("push-subs.json", "push.log", "push.env", "notify.key"))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
failed = []


def must(ok, name, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("  " + str(detail)[:400] if detail != "" else ""), flush=True)
    if not ok:
        failed.append(name)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def fake(kind, path=None):
    """A stored subscription as push-subs.json holds it, made long ago (so a 410 drops it)."""
    rnd = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"at": "2026-01-01 00:00:00", "endpoint": "https://fcm.googleapis.com/fcm/send/" + (path or kind + "-" + rnd),
            "p256dh": b64u(pub), "auth": b64u(os.urandom(16)), "ua": "Mozilla/5.0 (X11; Linux x86_64) test"}


def put(subs):
    with os.fdopen(os.open(SUBS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(subs, f)


def held():
    with open(SUBS) as f:
        return json.load(f)


def call(path, body, key=None, timeout=120):
    heads = {"Content-Type": "application/json"}
    if key is not None:
        heads["X-Notify-Key"] = key
    req = urllib.request.Request(SITE + path, data=json.dumps(body).encode(), headers=heads)
    t = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as r:
            code, text = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read()
    except OSError as e:
        return None, {"error": "no answer: %r" % e}, time.monotonic() - t
    try:
        obj = json.loads(text)
    except ValueError:
        obj = {"raw": text[:200].decode("utf-8", "replace")}
    return code, obj, time.monotonic() - t


def key():
    with open(KEY) as f:
        return f.read().strip()


def log_mark():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def log_since(mark):
    with open(LOG, encoding="utf-8", errors="replace") as f:
        f.seek(mark)
        return f.read()


def cli(*args, timeout=150):
    t = time.monotonic()
    r = subprocess.run([os.path.join(DATA, "notify")] + list(args), capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, HOME=os.path.dirname(DATA)))
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.monotonic() - t


def requests():
    try:
        with open(os.path.join(FAKE, "requests.log")) as f:
            return f.read().split("\n")
    except FileNotFoundError:
        return []


def brief(res):
    return [{k: o.get(k) for k in ("ok", "status", "removed", "reason")} for o in res.get("results", [])] if isinstance(res, dict) else res


saved_env = open(ENV).read()
with open(ENV, "w") as f:
    f.write(RELAY)
try:
    # 1. an endpoint with a space in its path: refused at subscribe; one already stored (older versions took it) fails
    #    alone, is dropped, and its path shows up in nobody's reason nor in the log
    bad = fake("ok", "ok-has a space")
    code, res, _ = call("/api/push/subscribe", {"endpoint": bad["endpoint"], "keys": {"p256dh": bad["p256dh"], "auth": bad["auth"]}})
    must(code == 400, "subscribe refuses an endpoint with a space in its path", "HTTP %s %s" % (code, res.get("error")))
    for ch, name in (("\t", "tab"), ("\x7f", "DEL"), ("\n", "newline")):
        code, res, _ = call("/api/push/subscribe", {"endpoint": bad["endpoint"].replace(" ", ch), "keys": {"p256dh": bad["p256dh"], "auth": bad["auth"]}})
        must(code == 400, "subscribe refuses an endpoint with a %s in its path" % name, "HTTP %s" % code)
    a, b = fake("ok"), fake("ok")
    put([bad, a, b])
    mark = log_mark()
    code, res, _ = call("/api/notify", {"title": "毒化"}, key())
    r = res.get("results", [])
    text = json.dumps(res, ensure_ascii=False) + log_since(mark)
    must(len(r) == 3 and r[1]["ok"] and r[2]["ok"], "a stored bad endpoint does not stop the next ones on the same service", brief(res))
    must(len(r) == 3 and not r[0]["ok"] and r[0]["removed"] and "连不上" not in text, "the bad one fails alone, as itself (not 'cannot reach Google'), and is dropped",
         r[0]["reason"] if r else res)
    must("has a space" not in text and "ok-has" not in text, "its path is in no reason and not in push.log")
    must([s["endpoint"] for s in held()] == [a["endpoint"], b["endpoint"]], "push-subs.json keeps the two good ones only")

    # 2. the push service closes the connection without answering (a kept-alive connection it dropped): once more
    d, c = fake("drop"), fake("ok")
    put([d, c])
    open(os.path.join(FAKE, "requests.log"), "w").close()
    code, res, _ = call("/api/notify", {"title": "断开"}, key())
    must(code == 200 and [o["ok"] for o in res.get("results", [])] == [True, True], "a connection closed before the answer is opened again and the message sent once more", brief(res))
    must([x.split()[2] for x in requests() if x.startswith("drop")] == ["closed", "201"], "the service saw the dropped request, then the retry", [x for x in requests() if x])

    # 3. damaged subscription data (a key that is not a P-256 point, no endpoint): each reported, the rest sent, results kept
    broken, noep, good = fake("ok"), fake("ok"), fake("ok")
    broken["p256dh"] = "AAAA"
    noep["endpoint"] = None
    put([broken, noep, good])
    mark = log_mark()
    code, res, _ = call("/api/notify", {"title": "坏数据"}, key())
    r = res.get("results", [])
    must(code == 200 and len(r) == 3 and r[2]["ok"] and not r[0]["ok"] and not r[1]["ok"], "damaged entries do not stop the delivery (HTTP %s)" % code, brief(res))
    must(len(r) == 3 and r[0]["removed"] and r[1]["removed"] and all("不能用" in o["reason"] for o in r[:2]), "each damaged entry says what is wrong and is dropped",
         [o["reason"] for o in r[:2]])
    h = held()
    must(len(h) == 1 and h[0]["endpoint"] == good["endpoint"] and h[0].get("last", {}).get("ok") is True and log_since(mark).count("notify '坏数据'") == 3,
         "the outcomes are written back to push-subs.json and push.log", [s.get("last") for s in h])

    # 4. a subscription the push service calls gone is remembered by hash; the same endpoint handed in again is told so
    gone = fake("gone")
    put([gone])
    code, res, _ = call("/api/notify", {"title": "失效"}, key())
    must(code == 502 and res.get("removed") == 1, "410 from an hour-old subscription: dropped", brief(res))
    code, res, _ = call("/api/push/subscribe", {"endpoint": gone["endpoint"], "keys": {"p256dh": gone["p256dh"], "auth": gone["auth"]}})
    must(code == 410 and res.get("gone") is True and held() == [], "the page handing it in again is told it is gone, and it is not stored again", "HTTP %s %s" % (code, res))
    store = open(os.path.join(DATA, "push-gone.json")).read() if os.path.exists(os.path.join(DATA, "push-gone.json")) else ""
    must(store and gone["endpoint"].rsplit("/", 1)[1] not in store and "fcm" not in store and oct(os.stat(os.path.join(DATA, "push-gone.json")).st_mode & 0o777) == "0o600",
         "push-gone.json holds hashes only (no endpoint text), mode 600", "%d entries" % len(json.loads(store or "{}")))

    # 5. notify.key empty or short: /notify refuses everybody instead of matching an empty header
    k = open(KEY, "rb").read()
    put([])
    try:
        for content, header, name in ((b"", None, "empty, no header"), (b"\n", "", "a newline, empty header"), (b"short\n", "short", "5 bytes, the same 5 bytes sent")):
            with open(KEY, "wb") as f:
                f.write(content)
            mark = log_mark()
            code, res, _ = call("/api/notify", {"title": "空密钥"}, header)
            must(code == 503 and "notify.key" in res.get("error", "") and "notify.key has" in log_since(mark), "notify.key %s: refused, and push.log says why" % name, "HTTP %s %s" % (code, res.get("error")))
    finally:
        with open(KEY, "wb") as f:
            f.write(k)
    code, res, _ = call("/api/notify", {"title": "钥匙回来了"}, key())
    must(code == 200, "with the key back, notify works again", "HTTP %s %s" % (code, res.get("note")))

    # 6. the url a click opens: this site only
    for url, want in (("https://evil.example/x", 400), ("//evil.example/x", 400), ("/\\evil.example/x", 400), ("http://plan.example:8443/x", 400),
                      ("https://plan.example:8443.evil.example/x", 400), ("javascript:alert(1)", 400), ("/a b", 400),
                      ("/t/p/", 200), ("https://plan.example:8443/t/p/", 200)):
        code, res, _ = call("/api/notify", {"title": "链接", "url": url}, key())
        must(code == want, "notify with url %r: HTTP %d" % (url, want), "HTTP %s %s" % (code, res.get("error", res.get("note"))))

    # 7. a forwarding command whose child keeps stderr open after it is killed: the answer still comes in seconds
    with open(ENV, "w") as f:
        f.write("FCM_PROXY_CMD=sh -c 'sleep 151 & exec sleep 151'\n")
    put([fake("ok")])
    rc, out, err, took = cli("孙进程")
    must(rc == 1 and "转发起不来" in err and took < 30, "forwarding whose grandchild holds stderr: notify answers in %.0f s (exit %d)" % (took, rc), (out + " " + err)[-200:])
    left = subprocess.run(["pgrep", "-f", "sleep 151"], capture_output=True, text=True).stdout.split()
    must(not left, "nothing of the forwarding command is left running", "%d processes" % len(left))
    subprocess.run(["pkill", "-f", "sleep 151"])

    # 8. a service that takes requests and never answers: each such subscription times out on its own, the whole send
    #    stops at 60 s and says which ones it did not get to; the notify command waits longer than that
    with open(ENV, "w") as f:
        f.write(RELAY)
    put([fake("ok")] + [fake("slow") for _ in range(7)])
    rc, out, err, took = cli("慢")
    lines = [x for x in err.split("\n") if "没发出去" in x]
    must(rc == 0 and out == "发出 1，失败 7", "a slow service: the first subscription is delivered, 7 fail (exit %d after %.0f s)" % (rc, took), out)
    must(len(lines) == 7 and all("超时" in x for x in lines) and "连不上" not in err, "each slow one is reported as 超时, none as cannot reach the service",
         [x.split("：", 1)[-1] for x in lines][:1] + [x.split("：", 1)[-1] for x in lines][-1:])
    must(sum("没轮到" in x for x in lines) >= 1 and 55 <= took <= 75, "the send stops at the 60 s limit and names the ones it did not get to (%.0f s)" % took)
finally:
    with open(ENV, "w") as f:
        f.write(saved_env)
    put([])
    subprocess.run(["pkill", "-f", "sleep 151"])
print("faults: %d failed" % len(failed))
sys.exit(1 if failed else 0)
