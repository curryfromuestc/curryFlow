"""A differential probe of a push service's VAPID checking: the same encrypted message to the same made-up endpoint,
only the Authorization header changes.  If the service answers the broken tokens differently from the good one, it
validated the good one.  Run where push.py lives, with the python that has `cryptography`:
    cd ~/curry-site && venv/bin/python - apple|fcm|mozilla < vapid_probe.py
It signs with that machine's VAPID key and prints neither endpoint nor key."""
import json, os, secrets, string, sys, time
sys.path[:0] = [os.path.expanduser("~/curry-site"), os.path.join(os.path.dirname(os.path.abspath(sys.argv[0] or ".")), "..", "..", "server")]
import push
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric import ec

rnd = lambda n: "".join(secrets.choice(string.ascii_letters + string.digits + "-_") for _ in range(n))
endpoint = {"apple": "https://web.push.apple.com/Q" + rnd(151),
            "fcm": "https://fcm.googleapis.com/fcm/send/" + rnd(11) + ":APA91b" + rnd(134),
            "mozilla": "https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB" + rnd(180)}[sys.argv[1]]
ua, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
u, now = urlsplit(endpoint), time.time()
good = push.vapid_header(endpoint)
t, k = good[len("vapid t="):].split(", k=")
sig = t.rsplit(".", 1)[1]
variants = [
    ("正常的 VAPID 头（服务器实际发的）", good),
    ("签名改掉一个字符", "vapid t=%s, k=%s" % (t[:-len(sig)] + ("A" if sig[0] != "A" else "B") + sig[1:], k)),
    ("exp 已过期 1 小时", push.vapid_header(endpoint, now=now - push.TOKEN_LIFE - 3600)),
    ("exp 在 25 小时后", push.vapid_header(endpoint, now=now + 13 * 3600)),
    ("aud 是别家推送服务", push.vapid_header("https://push.example.net/x")),
    ("别人的密钥签的（k 也是别人的）", push.vapid_header(endpoint, ec.generate_private_key(ec.SECP256R1()))),
    ("不带 Authorization", None),
]
for name, header in variants:
    conn = push.Conn(u.hostname, push.env().get("FCM_PROXY_CMD") if u.hostname == push.FCM_HOST else None)
    heads = {"TTL": "60", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"}
    if header:
        heads["Authorization"] = header
    try:
        conn.request("POST", u.path, push.encrypt(b'{"title":"probe"}', push.point(ua.public_key()), auth), heads)
        r = conn.getresponse()
        print("%-36s -> %d %s" % (name, r.status, " ".join(r.read().decode("utf-8", "replace").split())[:160]))
    except OSError as e:
        print("%-36s -> 没连上：%s" % (name, e))
    finally:
        conn.finish()
