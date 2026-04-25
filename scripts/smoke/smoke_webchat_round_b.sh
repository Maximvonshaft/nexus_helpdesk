#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-${NEXUSDESK_API_URL:-http://127.0.0.1:18081}}"
PREFIX="${NEXUSDESK_SMOKE_PREFIX:-round-b-$(date +%s)}"
ADMIN_TOKEN="${NEXUSDESK_ADMIN_TOKEN:-}"
DEV_USER_ID="${NEXUSDESK_DEV_USER_ID:-}"

if [[ "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
       NEXUSDESK_ADMIN_TOKEN=<jwt> bash scripts/smoke/smoke_webchat_round_b.sh

Runs Round B Webchat init -> visitor message -> admin reply -> visitor poll smoke.
EOF
  exit 0
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "DRY RUN webchat smoke against ${BASE_URL} with prefix ${PREFIX}"
  exit 0
fi

json_get() {
  python3 -c "import json,sys; data=json.load(sys.stdin); cur=data;\nfor p in sys.argv[1].split('.'):\n    cur=cur[int(p)] if isinstance(cur, list) else cur[p]\nprint(cur)" "$1"
}

curl_json() {
  local method="$1"; shift
  local url="$1"; shift
  curl -fsS -X "$method" "$url" -H 'Content-Type: application/json' "$@"
}

AUTH_HEADERS=()
if [[ -n "$ADMIN_TOKEN" ]]; then
  AUTH_HEADERS=(-H "Authorization: Bearer ${ADMIN_TOKEN}")
elif [[ -n "$DEV_USER_ID" ]]; then
  AUTH_HEADERS=(-H "X-User-Id: ${DEV_USER_ID}")
else
  echo "SKIP admin reply: set NEXUSDESK_ADMIN_TOKEN or NEXUSDESK_DEV_USER_ID for full closure smoke" >&2
  exit 77
fi

echo "== 1) init conversation =="
INIT_BODY=$(cat <<JSON
{"tenant_key":"${PREFIX}","channel_key":"website","visitor_name":"Round B Smoke","origin":"https://smoke.example","page_url":"https://smoke.example/help"}
JSON
)
INIT_RESP=$(curl_json POST "${BASE_URL}/api/webchat/init" --data "$INIT_BODY")
CONVERSATION_ID=$(printf '%s' "$INIT_RESP" | json_get conversation_id)
VISITOR_TOKEN=$(printf '%s' "$INIT_RESP" | json_get visitor_token)
echo "PASS init ${CONVERSATION_ID}"

echo "== 2) visitor sends message =="
SEND_BODY=$(cat <<JSON
{"visitor_token":"${VISITOR_TOKEN}","body":"Round B smoke visitor message: where is my parcel?"}
JSON
)
curl_json POST "${BASE_URL}/api/webchat/conversations/${CONVERSATION_ID}/messages" --data "$SEND_BODY" >/dev/null
echo "PASS visitor send"

echo "== 3) visitor can poll own messages =="
POLL_RESP=$(curl -fsS "${BASE_URL}/api/webchat/conversations/${CONVERSATION_ID}/messages?visitor_token=${VISITOR_TOKEN}")
printf '%s' "$POLL_RESP" | grep -q "Round B smoke visitor message"
echo "PASS visitor poll inbound"

echo "== 4) admin resolves ticket id =="
ADMIN_LIST=$(curl -fsS "${BASE_URL}/api/webchat/admin/conversations" "${AUTH_HEADERS[@]}")
TICKET_ID=$(printf '%s' "$ADMIN_LIST" | python3 -c "import json,sys; cid='$CONVERSATION_ID'; rows=json.load(sys.stdin); print(next(x['ticket_id'] for x in rows if x['conversation_id']==cid))")
echo "PASS ticket ${TICKET_ID}"

echo "== 5) safety gate blocks secret-like reply =="
BLOCK_CODE=$(curl -sS -o /tmp/roundb_block.json -w '%{http_code}' -X POST "${BASE_URL}/api/webchat/admin/tickets/${TICKET_ID}/reply" "${AUTH_HEADERS[@]}" -H 'Content-Type: application/json' --data '{"body":"SECRET_KEY leaked in stack trace token password"}')
[[ "$BLOCK_CODE" == "400" ]]
echo "PASS safety block"

echo "== 6) admin sends safe reply =="
REPLY_BODY='{"body":"We have received your request and will check it shortly."}'
curl -fsS -X POST "${BASE_URL}/api/webchat/admin/tickets/${TICKET_ID}/reply" "${AUTH_HEADERS[@]}" -H 'Content-Type: application/json' --data "$REPLY_BODY" >/dev/null
echo "PASS admin reply"

echo "== 7) visitor sees agent reply =="
POLL_AFTER=$(curl -fsS "${BASE_URL}/api/webchat/conversations/${CONVERSATION_ID}/messages?visitor_token=${VISITOR_TOKEN}")
printf '%s' "$POLL_AFTER" | grep -q "We have received your request"
echo "PASS visitor sees reply"

echo "PASS Round B webchat closure smoke"
