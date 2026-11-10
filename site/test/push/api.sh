# The HTTP interface through the stand-in for nginx: who may call /api/notify, what a made-up endpoint gets back from
# the real push service, and what a forwarding command that does not work looks like.  Prints no endpoint or key.
# usage: SERVER_PID=... api.sh SITE DATA_DIR      (DATA_DIR: the server's ~/curry-site)
B=$1; DATA=$2; HERE=$(cd "$(dirname "$0")" && pwd); J='Content-Type: application/json'; rc=0
D=$(mktemp -d "$DATA/../api.XXXXXX"); trap 'rm -rf "$D"' EXIT
c() { env -u http_proxy -u https_proxy -u all_proxy -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY curl -s -m 90 "$@"; }
must() { if echo "$2" | grep -q -- "$3"; then echo "ok    $1  $(echo "$2" | sed -E 's/"id": "[0-9a-f]{8}", //; s/, "error": .*\} \(/} (/' | cut -c1-260)"; else echo "FAIL  $1 (wanted: $3)  $2"; rc=1; fi; }
probe() {  # probe KIND: subscribe a made-up endpoint of that service and send one test; prints the answer to the test
  python3 "$HERE/fake_sub.py" $1 > $D/sub.json
  python3 -c "import json; print(json.dumps({'endpoint': json.load(open('$D/sub.json'))['endpoint']}))" > $D/ep.json
  SUBSCRIBED=$(c -w ' (HTTP %{http_code})' -H "$J" -d @$D/sub.json $B/api/push/subscribe)
  [ -z "$AGED" ] || python3 -c "import json; p='$DATA/push-subs.json'; s=json.load(open(p)); [x.update(at='2026-01-01 00:00:00') for x in s]; json.dump(s, open(p,'w'))"
  c -w ' (HTTP %{http_code}, %{time_total}s)' -H "$J" -d @$D/ep.json $B/api/push/test
  [ -z "$AGED" ] || c -w ' (HTTP %{http_code})' -H "$J" -d @$D/ep.json $B/api/push/test > $D/again
  c -o /dev/null -H "$J" -d @$D/ep.json $B/api/push/unsubscribe
}
for u in $B/api/notify http://127.0.0.1:8765/notify http://127.0.0.1:8765/api/notify; do
  must "notify without the key is refused: ${u#http*://}" "$(c -w ' (HTTP %{http_code})' -H "$J" -d '{"title":"x"}' $u)" 'HTTP 403'
done
must "notify with a wrong key is refused" "$(c -w ' (HTTP %{http_code})' -H 'X-Notify-Key: wrong' -H "$J" -d '{"title":"x"}' $B/api/notify)" 'HTTP 403'
must "notify with the key and no subscription says so" "$(HOME=$(dirname "$DATA") "$DATA/notify" "没人听" 2>&1 | tr '\n' ' '; echo "exit ${PIPESTATUS[0]}")" '没有设备开启通知.*exit 1'
must "a made-up FCM endpoint gets Google's own answer; made just now, it is kept" "$(probe fcm)" '"status": 4[0-9][0-9].*刚建的.*"removed": false'
must "a made-up Apple endpoint gets Apple's own answer" "$(probe apple)" '"status": 4[0-9][0-9]'
must "a made-up Mozilla endpoint gets Mozilla's own answer" "$(probe mozilla)" '"status": 4[0-9][0-9]'
must "the same from a subscription an hour old: gone (404/410) means dropped" "$(AGED=1 probe fcm)" '已删除.*"removed": true'
must "and it is no longer on the server" "$(cat $D/again)" '服务器上没有这个订阅.*HTTP 404'
probe other > /dev/null; must "an endpoint on an unknown host is refused at subscribe" "$SUBSCRIBED" '不认识的推送服务.*HTTP 400'
probe local > /dev/null; must "an endpoint on 127.0.0.1 is refused at subscribe" "$SUBSCRIBED" '不认识的推送服务.*HTTP 400'
cp "$DATA/push.env" $D/push.env
echo 'FCM_PROXY_CMD=/nonexistent/relay %h %p' > "$DATA/push.env"
must "forwarding command missing" "$(probe fcm)" '转发起不来（/nonexistent/relay: No such file or directory）.*HTTP 502'
echo "FCM_PROXY_CMD=sh -c \"echo 'channel 0: open failed: administratively prohibited: open failed' >&2; exit 255\"" > "$DATA/push.env"
must "forwarding refused by the far end" "$(probe fcm)" '转发起不来（channel 0: open failed: administratively prohibited: open failed）.*HTTP 502'
echo 'FCM_PROXY_CMD=sleep 60' > "$DATA/push.env"
must "forwarding command up but silent: gives up after 10 s" "$(probe fcm)" '转发起不来（.*timed out）.*HTTP 502, 10\.'
cp $D/push.env "$DATA/push.env"
must "the server has no forwarding process left" "$(pgrep -P $SERVER_PID | wc -l) children" '^0 children'
exit $rc
