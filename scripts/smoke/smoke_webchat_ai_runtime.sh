#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ORIGIN="${ORIGIN:-http://localhost}"

need_jq() {
  command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }
}
need_jq

curl_json() {
  local label="$1"
  shift
  local out
  if ! out=$(curl -fsS "$@" 2>&1); then
    echo "❌ $label failed" >&2
    echo "$out" >&2
    exit 1
  fi
  printf '%s' "$out"
}

echo "== WebChat AI runtime smoke =="
echo "BASE_URL=$BASE_URL"

INIT_JSON=$(curl_json "init conversation" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/init" \
  -d '{"tenant_key":"smoke-ai-runtime","channel_key":"website","visitor_name":"Smoke AI Runtime Visitor","origin":"http://localhost","page_url":"http://localhost/webchat-ai-runtime-smoke"}')
CONVERSATION_ID=$(echo "$INIT_JSON" | jq -r '.conversation_id')
VISITOR_TOKEN=$(echo "$INIT_JSON" | jq -r '.visitor_token')
test -n "$CONVERSATION_ID" && test "$CONVERSATION_ID" != "null"
test -n "$VISITOR_TOKEN" && test "$VISITOR_TOKEN" != "null"
echo "conversation=$CONVERSATION_ID"

SEND_ONE=$(curl_json "send visitor message" -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages" \
  -d '{"body":"Hello from AI runtime smoke","client_message_id":"smoke-ai-runtime-1"}')
echo "$SEND_ONE" | jq -e '.ok == true and .message.client_message_id == "smoke-ai-runtime-1" and .ai_pending == true and (.ai_turn_id != null)' >/dev/null
MESSAGE_ID=$(echo "$SEND_ONE" | jq -r '.message.id')
AI_TURN_ID=$(echo "$SEND_ONE" | jq -r '.ai_turn_id')

echo "message=$MESSAGE_ID ai_turn=$AI_TURN_ID"

SEND_DUP=$(curl_json "send duplicate visitor message" -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages" \
  -d '{"body":"Hello from AI runtime smoke","client_message_id":"smoke-ai-runtime-1"}')
echo "$SEND_DUP" | jq -e --argjson message_id "$MESSAGE_ID" '.ok == true and .idempotent == true and .message.id == $message_id' >/dev/null

POLL_JSON=$(curl_json "poll messages" -H "Origin: $ORIGIN" -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" \
  "$BASE_URL/api/webchat/conversations/$CONVERSATION_ID/messages?limit=50")
echo "$POLL_JSON" | jq -e --argjson message_id "$MESSAGE_ID" --argjson ai_turn_id "$AI_TURN_ID" '.messages[] | select(.id == $message_id and .client_message_id == "smoke-ai-runtime-1")' >/dev/null
echo "$POLL_JSON" | jq -e --argjson ai_turn_id "$AI_TURN_ID" '.ai_pending == true and .ai_turn_id == $ai_turn_id' >/dev/null

echo "PASS smoke_webchat_ai_runtime"
