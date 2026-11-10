"""Checks that the constants in push.py's selftest are the RFCs' own: with whitespace and page breaks removed, each
must occur verbatim in the RFC text.  Usage: vectors_vs_rfc.py rfc8291.txt rfc8292.txt push.py"""
import re, sys, base64
rfc8291 = open(sys.argv[1], encoding="utf-8").read()
rfc8292 = open(sys.argv[2], encoding="utf-8").read()
src = open(sys.argv[3], encoding="utf-8").read()
def squash(t):
    t = re.sub(r"\n+Thomson.*?\[Page \d+\]\n\x0c?\nRFC 829\d.*?\n", "\n", t, flags=re.S)   # 分页的页脚页眉
    return re.sub(r"\s+", "", t)
a, b = squash(rfc8291), squash(rfc8292)
body = src[src.index("    v = {"):src.index("    private = lambda")]
consts = dict(re.findall(r'"(\w+)":\s*((?:"[^"]*"\s*)+)', body))
bad = 0
for name, raw in consts.items():
    val = "".join(re.findall(r'"([^"]*)"', raw))
    hit = val in a
    bad += not hit
    print(("in RFC 8291  " if hit else "MISSING      ") + name, len(val), "chars")
for name in ("rfc_t", "rfc_k"):
    m = re.search(name + r' = \(?((?:\s*"[^"]*")+)', src)
    val = "".join(re.findall(r'"([^"]*)"', m.group(1)))
    hit = val in b
    bad += not hit
    print(("in RFC 8292  " if hit else "MISSING      ") + name, len(val), "chars")
val = "".join(re.findall(r'"([^"]*)"', consts["body"]))
print("section 5 body decodes to", len(base64.urlsafe_b64decode(val + "=" * (-len(val) % 4))), "octets; the RFC's example header says Content-Length:", re.search(r"Content-Length: (\d+)\n\s+Content-Encoding: aes128gcm\n\n\s+DGv6", rfc8291).group(1))
sys.exit(1 if bad else 0)
