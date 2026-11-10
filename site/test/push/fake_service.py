"""A stand-in for a push service, reached the way the server reaches FCM: FCM_PROXY_CMD=relay.py PORT gives the
server a TCP stream to this process, which speaks TLS as fcm.googleapis.com with a certificate from a test CA (the
server trusts it through SSL_CERT_FILE, a bundle of the system CAs plus that CA).  What it answers depends on the
endpoint's path, so a test can line up subscriptions that behave differently on one connection:
    .../ok-*     201
    .../gone-*   410
    .../slow-*   reads the request and never answers (the connection stays open until the client gives up)
    .../drop-*   the first request on that path: closes the connection without answering; later ones: 201
and DIR/mode, when it holds "gone", makes every answer 410 (for a real browser subscription).
Every request is one line in DIR/requests.log: "<kind> <sha256(path)[:8]> <answer>"; no path is written.
    fake_service.py certs DIR          makes DIR/{ca.pem,cert.pem,key.pem,bundle.pem}
    fake_service.py serve DIR PORT"""
import datetime, hashlib, os, ssl, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HOSTS = ["fcm.googleapis.com", "web.push.apple.com", "updates.push.services.mozilla.com"]


def certs(d):
    os.makedirs(d, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "curryPlan push test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    open(os.path.join(d, "ca.pem"), "wb").write(pem(ca))
    open(os.path.join(d, "cert.pem"), "wb").write(pem(leaf))
    open(os.path.join(d, "key.pem"), "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                    serialization.NoEncryption()))
    paths = ssl.get_default_verify_paths()
    system = next((p for p in (paths.cafile, paths.openssl_cafile, "/etc/ssl/certs/ca-certificates.crt") if p and os.path.isfile(p)), None)
    if not system:
        sys.exit("no system CA bundle to extend")
    open(os.path.join(d, "bundle.pem"), "wb").write(open(system, "rb").read() + b"\n" + pem(ca))


def serve(d, port):
    seen, lock = set(), threading.Lock()

    def note(kind, path, answer):
        with lock, open(os.path.join(d, "requests.log"), "a") as f:
            f.write("%s %s %s\n" % (kind, hashlib.sha256(path.encode()).hexdigest()[:8], answer))

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            last = self.path.rsplit("/", 1)[-1]
            kind = last.split("-", 1)[0] if "-" in last else "other"
            try:
                mode = open(os.path.join(d, "mode")).read().strip()
            except FileNotFoundError:
                mode = ""
            if mode == "gone":
                kind = "gone"
            if kind == "slow":
                note(kind, self.path, "none")
                self.close_connection = True
                self.connection.settimeout(60)
                try:
                    self.rfile.read(1)        # until the client closes
                except OSError:
                    pass
                return
            if kind == "drop":
                with lock:
                    first = self.path not in seen
                    seen.add(self.path)
                if first:
                    note(kind, self.path, "closed")
                    self.close_connection = True
                    return
            code = 410 if kind == "gone" else 201
            note(kind, self.path, code)
            text = b"push subscription has unsubscribed or expired." if code == 410 else b""
            self.send_response(code)
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *a):
            pass

    class S(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            sock, addr = self.socket.accept()
            return ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(os.path.join(d, "cert.pem"), os.path.join(d, "key.pem"))
    S(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    if sys.argv[1] == "certs":
        certs(sys.argv[2])
    else:
        serve(sys.argv[2], int(sys.argv[3]))
