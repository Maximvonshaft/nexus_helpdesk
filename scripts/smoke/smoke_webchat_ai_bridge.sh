#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18081}"
OPENCLAW_BRIDGE_URL="${OPENCLAW_BRIDGE_URL:-}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

json_get() {
  local file="$1"
  local expr="$2"
  python3 - "$file" "$expr" <<'PY'
import json, sys
path = sys.argv[1]
expr = sys.argv[2]
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)
value = data
for part in expr.split('.'):
    if not part:
        continue
    if part.isdigit():
        value = value[int(part)]
    else:
        value = value[part]
if isinstance(value, bool):
    print('true' if value else 'false')
elif value is None:
    print('null')
else:
    print(value)
PY
}

echo '== preflight =='
curl -fsS "$BASE_URL/healthz" >/dev/null
curl -fsS "$BASE_URL/readyz" >/dev/null
if [ -n "$OPENCLAW_BRIDGE_URL" ]; then
  curl -fsS "$OPENCLAW_BRIDGE_URL/health"
fi

echo

echo '== init webchat =='
curl -fsS "$BASE_URL/api/webchat/init" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_key":"smoke","channel_key":"website","visitor_name":"Smoke Visitor","origin":"https://example.test","page_url":"https://example.test/help"}' \
  > "$TMP_DIR/init.json"
cat "$TMP_DIR/init.json"
CONVERSATION_ID="$(json_get "$TMP_DIR/init.json" conversation_id)"
VISITOR_TOKEN="$(json_get "$TMP_DIR/init.json" visitor_token)"

echo
echo '== send visitor message =='
curl -fsS "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages" \
  -H 'Content-Type: application/json' \
  -d "{\"visitor_token\":\"$VISITOR_TOKEN\",\"body\":\"Where is my parcel?\"}" \
  > "$TMP_DIR/send.json"
cat "$TMP_DIR/send.json"

echo
echo '== wait and poll messages =='
for i in $(seq 1 15); do
  curl -fsS "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages?visitor_token=$VISITOR_TOKEN" > "$TMP_DIR/poll.json"
  cat "$TMP_DIR/poll.json"
  if POLL_FILE="$TMP_DIR/poll.json" python3 - <<'PY'
import json, os
with open(os.environ['POLL_FILE'], 'r', encoding='utf-8') as f:
    payload = json.load(f)
messages = payload.get('messages', [])
if any(m.get('direction') == 'agent' and m.get('author_label') == 'NexusDesk AI Assistant' for m in messages):
    raise SystemExit(0)
raise SystemExit(1)
PY
  then
    break
  fi
  sleep 1
done

echo
echo '== assertions =='
POLL_FILE="$TMP_DIR/poll.json" python3 - <<'PY'
import json, os
with open(os.environ['POLL_FILE'], 'r', encoding='utf-8') as f:
    payload = json.load(f)
messages = payload['messages']
assert any(m['direction'] == 'visitor' and 'Where is my parcel?' in m['body'] for m in messages), 'missing visitor message'
assert any(m['direction'] == 'agent' and 'received your parcel inquiry' in m['body'] for m in messages), 'missing acknowledgement message'
assert any(m['direction'] == 'agent' and m.get('author_label') == 'NexusDesk AI Assistant' for m in messages), 'missing NexusDesk AI Assistant reply'
assert any(m['direction'] == 'agent' and ('tracking number' in m['body'].lower() or 'review' in m['body'].lower()) for m in messages), 'missing AI or safe fallback reply'
print('smoke_ok=true')
PY

echo
echo '== done =='
