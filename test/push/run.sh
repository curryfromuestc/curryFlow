#!/bin/bash
# End-to-end test of the notification channel on this machine: the real server/ and shell/, a stand-in for nginx, and a
# real Chrome that subscribes at Google's push service and shows what the server sends.
#   test/push/run.sh WORKDIR        logs and screenshots land in WORKDIR (created; nothing is written elsewhere)
#   PROXY=127.0.0.1:7890            an HTTP proxy towards Google, when this machine has no direct route
#   OLD=4ba20be                     the revision whose shell/ plays the old client (the one before notifications)
# Needs google-chrome, node >= 20.10, python3 with `cryptography`, curl, nc, and ports 8765, 18443, 9333 free.
# Exit 0 pass, 99 the environment lacks something, anything else fail; the last line is "RESULT ...".
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
[ -n "$1" ] || { echo "usage: $0 WORKDIR"; exit 2; }
mkdir -p "$1" && W=$(cd "$1" && pwd) || exit 2
SITE=http://localhost:18443; OLD=${OLD:-4ba20be}; DATA=$W/home/curry-site; failed=""
lack() { echo "RESULT env: $1"; exit 99; }
python3 -c "import cryptography" 2>/dev/null || lack "python3 has no cryptography"
node --experimental-websocket -e "WebSocket" 2>/dev/null || lack "node cannot do --experimental-websocket"
command -v google-chrome >/dev/null || lack "no google-chrome"
ss -ltn | grep -q -E ':(8765|18443|9333)\s' && lack "port 8765, 18443 or 9333 is taken"
for n in 8291 8292; do [ -s "$W/rfc$n.txt" ] || curl -s -m 30 -o "$W/rfc$n.txt" https://www.rfc-editor.org/rfc/rfc$n.txt || lack "cannot fetch RFC $n"; done
stage() { local name=$1; shift; echo "== $name"; "$@" 2>&1 | grep -v -E 'ExperimentalWarning|trace-warnings' | tee -a "$W/log.txt"; [ "${PIPESTATUS[0]}" = 0 ] || failed="$failed [$name]"; }
: > "$W/log.txt"
stage "push.py selftest (RFC 8291 appendix A, RFC 8292, round trips)" python3 "$REPO/server/push.py" selftest
stage "the selftest's constants are verbatim in the RFC texts" python3 "$HERE/vectors_vs_rfc.py" "$W/rfc8291.txt" "$W/rfc8292.txt" "$REPO/server/push.py"

rm -rf "$W/home" "$W/www" "$W/new" "$W/chrome"; mkdir -p "$DATA" "$W/www/t/p" "$W/new"
for f in $(git -C "$REPO" ls-tree --name-only "$OLD" shell/); do git -C "$REPO" show "$OLD:$f" > "$W/www/$(basename "$f")"; done
cp "$REPO"/shell/* "$W/new/"
echo '{"projects":[{"id":"t","name":"测试项目"}],"pages":[{"project":"t","url":"/t/p/","title":"一个页面","say":"结论","kind":"方案","updated":"2026-11-10 10:00"}]}' | tee "$W/new/catalog.json" > "$W/www/catalog.json"
echo '<!doctype html><meta charset=utf-8><title>页</title><p>页面正文' > "$W/www/t/p/index.html"
cp "$REPO/server/notify" "$DATA/notify"
[ -n "$PROXY" ] && echo "FCM_PROXY_CMD=nc -X connect -x $PROXY %h %p" > "$DATA/push.env" || : > "$DATA/push.env"
HOME=$W/home python3 "$REPO/server/server.py" > "$W/server.out" 2>&1 & SERVER=$!
python3 "$HERE/mock_nginx.py" "$W/www" 18443 > "$W/nginx.out" 2>&1 & NGINX=$!
google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir="$W/chrome" --no-first-run --no-default-browser-check ${PROXY:+--proxy-server=http://$PROXY} --window-size=390,844 about:blank > "$W/chrome.out" 2>&1 & CHROME=$!
trap 'kill $SERVER $NGINX $CHROME 2>/dev/null; sleep 1; rm -rf "$W/chrome"' EXIT
for i in $(seq 1 40); do sleep 0.5; curl -s --noproxy localhost,127.0.0.1 -m 2 -o /dev/null http://127.0.0.1:9333/json/version && curl -s --noproxy localhost,127.0.0.1 -m 2 -o /dev/null $SITE/api/push/key && break; done
export SITE DATA OUT=$W WWW=$W/www NEW=$W/new HOME_FOR_NOTIFY=$W/home NGINX_PID=$NGINX SERVER_PID=$SERVER
cd "$HERE"
stage "an old client picks up the new service worker and page" node --experimental-websocket sw_update.mjs
stage "the switch: on, test, notify, reload, off (a real subscription, a real delivery)" node --experimental-websocket toggle.mjs
stage "the interface: notify's key, made-up endpoints, forwarding that fails" bash api.sh $SITE "$DATA"
stage "the switch in each environment, sw.js on any payload, a click, the site down" node --experimental-websocket environments.mjs
echo "== files the server made"; (cd "$DATA" && stat -c '%a %n' vapid.pem notify.key push-subs.json) | tee -a "$W/log.txt"
[ "$(cd "$DATA" && stat -c '%a' vapid.pem notify.key push-subs.json | sort -u)" = 600 ] || failed="$failed modes"
grep -q '^FAIL' "$W/log.txt" && failed="$failed (see FAIL lines)"
[ -z "$failed" ] && echo "RESULT pass: $(grep -c '^ok' "$W/log.txt") checks" || { echo "RESULT fail:$failed"; exit 1; }
