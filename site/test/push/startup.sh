# The server starts with a data directory it cannot write (no vapid.pem yet, so push.setup() fails and its failure
# cannot be written to push.log either): the existing routes must come up all the same, /push/* says what broke.
# usage: startup.sh REPO WORKDIR      (port 8765 must be free; the server is stopped again before this returns)
REPO=$1; W=$2/ro; rc=0
must() { if [ "$2" = ok ]; then echo "ok    $1  $3"; else echo "FAIL  $1  $3"; rc=1; fi; }
c() { env -u http_proxy -u https_proxy -u all_proxy -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY curl -s -m 10 "$@"; }
rm -rf "$W"; mkdir -p "$W/curry-site"; chmod 555 "$W/curry-site"
HOME=$W python3 "$REPO/server/server.py" > "$W.out" 2>&1 & PID=$!
for i in $(seq 1 20); do sleep 0.3; c -o /dev/null http://127.0.0.1:8765/choices && break; kill -0 $PID 2>/dev/null || break; done
A=$(c -w ' HTTP %{http_code}' http://127.0.0.1:8765/choices)
[ "$A" = "{} HTTP 200" ] && must "read-only data directory: the server is up, /choices answers" ok "$A" || must "read-only data directory: the server is up, /choices answers" no "${A:-no answer}; $(tail -1 "$W.out")"
B=$(c -w ' HTTP %{http_code}' http://127.0.0.1:8765/push/key)
echo "$B" | grep -q 'PermissionError.*HTTP 500' && must "/push/key reports the error instead of dropping the connection" ok "$B" || must "/push/key reports the error instead of dropping the connection" no "${B:-no answer}"
grep -q 'push.log not writable.*setup failed' "$W.out" && must "the setup failure that push.log could not take went to stderr (the journal)" ok || must "the setup failure that push.log could not take went to stderr (the journal)" no "$(tail -2 "$W.out" | tr '\n' ' ')"
kill $PID 2>/dev/null; wait $PID 2>/dev/null; chmod 755 "$W/curry-site"
exit $rc
