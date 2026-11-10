"""Prints a subscription that is well-formed but does not exist, as PushSubscription.toJSON() would give it: an endpoint
shaped like the real ones of that service and a fresh key pair.  Usage: fake_sub.py fcm|apple|mozilla|other|local"""
import base64, json, os, secrets, string, sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
rnd = lambda n: "".join(secrets.choice(string.ascii_letters + string.digits + "-_") for _ in range(n))
endpoint = {"fcm": "https://fcm.googleapis.com/fcm/send/" + rnd(11) + ":APA91b" + rnd(134),
            "apple": "https://web.push.apple.com/Q" + rnd(151),
            "mozilla": "https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB" + rnd(180),
            "other": "https://push.example.net/p/" + rnd(32), "local": "https://127.0.0.1/x"}[sys.argv[1]]
pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(json.dumps({"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": b64u(pub), "auth": b64u(os.urandom(16))}}))
