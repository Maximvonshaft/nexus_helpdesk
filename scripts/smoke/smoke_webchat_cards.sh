#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
ORIGIN="${ORIGIN:-http://localhost}"

echo "== WebChat structured cards smoke =="
echo "BASE_URL=$BASE_URL"

need_jq() {
  command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }
}
need_jq

INIT_JSON=$(curl -fsS -H "Origin: $ORIGIN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/init" \
  -d '{"tenant_key":"smoke","channel_key":"website","visitor_name":"Smoke Visitor","origin":"http://localhost","page_url":"http://localhost/webchat-smoke"}')
CONVERSATION_ID=$(echo "$INIT_JSON" | jq -r '.conversation_id')
VISITOR_TOKEN=$(echo "$INIT_JSON" | jq -r '.visitor_token')
test -n "$CONVERSATION_ID" && test "$CONVERSATION_ID" != "null"
test -n "$VISITOR_TOKEN" && test "$VISITOR_TOKEN" != "null"
echo "conversation=$CONVERSATION_ID"

SEND_JSON=$(curl -fsS -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages" \
  -d '{"body":"Hello, I need help tracking my parcel","client_message_id":"smoke-card-1"}')
echo "$SEND_JSON" | jq -e '.ok == true' >/dev/null

POLL_JSON=$(curl -fsS -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" \
  "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages?limit=50")
CARD_ID=$(echo "$POLL_JSON" | jq -r '.messages[] | select(.message_type=="card" and .payload_json.card_type=="quick_replies") | .payload_json.card_id' | head -1)
MESSAGE_ID=$(echo "$POLL_JSON" | jq -r '.messages[] | select(.message_type=="card" and .payload_json.card_type=="quick_replies") | .id' | head -1)
ACTION_ID=$(echo "$POLL_JSON" | jq -r '.messages[] | select(.message_type=="card" and .payload_json.card_type=="quick_replies") | .payload_json.actions[0].id' | head -1)
ACTION_TYPE=$(echo "$POLL_JSON" | jq -r '.messages[] | select(.message_type=="card" and .payload_json.card_type=="quick_replies") | .payload_json.actions[0].action_type' | head -1)
test -n "$CARD_ID" && test -n "$MESSAGE_ID" && test -n "$ACTION_ID"
echo "quick_reply_card=$CARD_ID message=$MESSAGE_ID action=$ACTION_ID"

ACTION_JSON=$(jq -n --argjson message_id "$MESSAGE_ID" --arg card_id "$CARD_ID" --arg action_id "$ACTION_ID" --arg action_type "$ACTION_TYPE" '{message_id:$message_id,card_id:$card_id,action_id:$action_id,action_type:$action_type,payload:{smoke:true}}')
SUBMIT_JSON=$(curl -fsS -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/actions" \
  -d "$ACTION_JSON")
echo "$SUBMIT_JSON" | jq -e '.ok == true and .message.message_type == "action"' >/dev/null

AFTER_ID=$(echo "$POLL_JSON" | jq -r '.next_after_id')
INCREMENTAL_JSON=$(curl -fsS -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" \
  "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages?after_id=$AFTER_ID&limit=20")
echo "$INCREMENTAL_JSON" | jq -e '.messages | length >= 1' >/dev/null

echo "visitor flow ok"

if [ -n "$ADMIN_TOKEN" ]; then
  TICKET_ID=$(python3 - <<'PY' "$POLL_JSON"
import json,sys
payload=json.loads(sys.argv[1])
print(payload.get('ticket_id') or '')
PY
)
  if [ -n "$TICKET_ID" ]; then
    THREAD_JSON=$(curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE_URL/api/webchat/admin/tickets/$TICKET_ID/thread")
    echo "$THREAD_JSON" | jq -e '.actions | length >= 1' >/dev/null
    echo "admin thread action audit ok"
  else
    echo "ADMIN_TOKEN set but public poll does not expose ticket_id; inspect /api/webchat/admin/conversations manually"
  fi
else
  echo "ADMIN_TOKEN not set; skipped admin thread smoke"
fi

echo "PASS smoke_webchat_cards"
