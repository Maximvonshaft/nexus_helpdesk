#!/usr/bin/env bash
set -euo pipefail

MODE_FULL=1
MOCK_WEBHOOKS=0
MOCK_INBOUND=0
ROLLBACK_CHECK=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mock-webhooks) MOCK_WEBHOOKS=1 ;;
    --mock-inbound) MOCK_INBOUND=1 ;;
    --rollback-check) ROLLBACK_CHECK=1 ;;
    --no-full) MODE_FULL=0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

: "${BASE_URL:?Set BASE_URL, e.g. https://nexus.example.com}"
: "${AUTH_TOKEN:?Set AUTH_TOKEN bearer token}"
: "${TICKET_ID:?Set TICKET_ID}"
: "${EMAIL_ACCOUNT_ID:?Set EMAIL_ACCOUNT_ID}"
: "${TEST_RECIPIENT:?Set TEST_RECIPIENT}"

EVIDENCE_DIR="${EVIDENCE_DIR:-email_e2e_evidence_$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$EVIDENCE_DIR"

AUTH_HEADER="Authorization: Bearer ${AUTH_TOKEN}"
JSON_HEADER="Content-Type: application/json"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

json_payload() {
  python3 - "$@" <<'PY'
import json, sys
it = iter(sys.argv[1:])
payload = {}
for key, value in zip(it, it):
    if value == "__NULL__":
        payload[key] = None
    elif value in ("__TRUE__", "__FALSE__"):
        payload[key] = value == "__TRUE__"
    elif value.startswith("__JSON__:"):
        payload[key] = json.loads(value[len("__JSON__:"):])
    else:
        payload[key] = value
print(json.dumps(payload, ensure_ascii=False))
PY
}

curl_json() {
  local method="$1"
  local path="$2"
  local out="$3"
  local data="${4:-}"
  if [ -n "$data" ]; then
    curl -fsS -X "$method" "$BASE_URL$path" \
      -H "$AUTH_HEADER" -H "$JSON_HEADER" \
      --data "$data" | tee "$EVIDENCE_DIR/$out" >/dev/null
  else
    curl -fsS -X "$method" "$BASE_URL$path" \
      -H "$AUTH_HEADER" | tee "$EVIDENCE_DIR/$out" >/dev/null
  fi
}

json_get() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    obj = json.load(f)
cur = obj
for part in key.split("."):
    if isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
print("" if cur is None else cur)
PY
}

require_file_contains() {
  local file="$1"
  local needle="$2"
  if ! grep -q "$needle" "$file"; then
    echo "Expected '$needle' in $file" >&2
    exit 1
  fi
}

log "Checking health"
curl -fsS "$BASE_URL/healthz" | tee "$EVIDENCE_DIR/healthz.json" >/dev/null
curl -fsS "$BASE_URL/readyz" | tee "$EVIDENCE_DIR/readyz.json" >/dev/null

log "Reading Email account readiness"
curl_json GET "/api/admin/email/accounts/${EMAIL_ACCOUNT_ID}/readiness" "email_account_readiness.json"
require_file_contains "$EVIDENCE_DIR/email_account_readiness.json" '"ready"'

log "Running Email account test send"
TEST_PAYLOAD="$(json_payload to_email "$TEST_RECIPIENT" subject "NexusDesk Email test send" body "This is a NexusDesk Email channel smoke test." confirm "__TRUE__")"
curl_json POST "/api/admin/email/accounts/${EMAIL_ACCOUNT_ID}/test-send" "email_account_test_send.json" "$TEST_PAYLOAD"
require_file_contains "$EVIDENCE_DIR/email_account_test_send.json" 'provider_message_id'

log "Reading ticket outbound channel capabilities"
curl_json GET "/api/tickets/${TICKET_ID}/outbound/channels/capabilities" "ticket_capabilities_before.json"
require_file_contains "$EVIDENCE_DIR/ticket_capabilities_before.json" '"email"'

if [ "$MODE_FULL" = "1" ]; then
  log "Sending customer Email reply"
  SEND_PAYLOAD="$(json_payload channel "email" to_email "$TEST_RECIPIENT" subject "Re: NexusDesk smoke ticket ${TICKET_ID}" body "Customer-service Email outbound smoke for ticket ${TICKET_ID}." confirm_external "__TRUE__")"
  printf '%s\n' "$SEND_PAYLOAD" > "$EVIDENCE_DIR/send_payload.json"
  curl_json POST "/api/tickets/${TICKET_ID}/outbound/send" "send_result.json" "$SEND_PAYLOAD"
  require_file_contains "$EVIDENCE_DIR/send_result.json" '"external_provider_send"'

  log "Reading queue summary after send"
  curl_json GET "/api/admin/queues/summary" "queue_summary_after_send.json"

  log "Reading ticket timeline after send"
  curl_json GET "/api/tickets/${TICKET_ID}/timeline?limit=50" "timeline_after_send.json"
  require_file_contains "$EVIDENCE_DIR/timeline_after_send.json" 'email'
fi

if [ "$MOCK_WEBHOOKS" = "1" ]; then
  : "${EMAIL_WEBHOOK_TEST_SECRET:?Set EMAIL_WEBHOOK_TEST_SECRET for mock webhook mode}"
  log "Posting mock SES delivery event"
  MOCK_DELIVERY="$(json_payload provider "ses" event_type "Delivery" provider_message_id "mock-${TICKET_ID}" recipient_email "$TEST_RECIPIENT" occurred_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" raw "__JSON__:{\"mail\":{\"messageId\":\"mock-${TICKET_ID}\"},\"eventType\":\"Delivery\"}")"
  curl_json POST "/api/integrations/email/events/ses?test_secret=${EMAIL_WEBHOOK_TEST_SECRET}" "delivery_webhook_result.json" "$MOCK_DELIVERY"

  log "Posting mock SES bounce event"
  MOCK_BOUNCE="$(json_payload provider "ses" event_type "Bounce" provider_message_id "mock-${TICKET_ID}" recipient_email "$TEST_RECIPIENT" occurred_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" raw "__JSON__:{\"mail\":{\"messageId\":\"mock-${TICKET_ID}\"},\"eventType\":\"Bounce\",\"bounce\":{\"bounceType\":\"Permanent\"}}")"
  curl_json POST "/api/integrations/email/events/ses?test_secret=${EMAIL_WEBHOOK_TEST_SECRET}" "bounce_webhook_result.json" "$MOCK_BOUNCE"

  log "Reading suppressions after bounce"
  curl_json GET "/api/admin/email/suppressions?email=${TEST_RECIPIENT}" "suppression_after_bounce.json"
fi

if [ "$MOCK_INBOUND" = "1" ]; then
  : "${EMAIL_INBOUND_TEST_SECRET:?Set EMAIL_INBOUND_TEST_SECRET for mock inbound mode}"
  log "Posting mock inbound plus-address Email"
  INBOUND_PAYLOAD="$(json_payload from_email "$TEST_RECIPIENT" to_email "support+ticket-${TICKET_ID}@example.test" subject "Re: NexusDesk smoke ticket ${TICKET_ID}" text_body "Inbound reply smoke for ticket ${TICKET_ID}." message_id "inbound-${TICKET_ID}@example.test")"
  curl_json POST "/api/integrations/email/inbound/test?test_secret=${EMAIL_INBOUND_TEST_SECRET}" "inbound_plus_address_result.json" "$INBOUND_PAYLOAD"

  log "Posting mock subject-only inbound Email; expected unresolved"
  SUBJECT_ONLY="$(json_payload from_email "$TEST_RECIPIENT" to_email "support@example.test" subject "Random subject without deterministic link" text_body "This should be unresolved." message_id "unresolved-${TICKET_ID}@example.test")"
  curl_json POST "/api/integrations/email/inbound/test?test_secret=${EMAIL_INBOUND_TEST_SECRET}" "inbound_subject_only_unresolved_result.json" "$SUBJECT_ONLY"
  require_file_contains "$EVIDENCE_DIR/inbound_subject_only_unresolved_result.json" 'unresolved'
fi

if [ "$ROLLBACK_CHECK" = "1" ]; then
  log "Capturing rollback before state"
  curl_json GET "/api/admin/queues/summary" "rollback_before.json"
  log "Rollback check is observational. Set OUTBOUND_EMAIL_ENABLED=false outside this script, restart workers, then rerun with --rollback-check and compare rollback_after.json."
  curl_json GET "/api/admin/queues/summary" "rollback_after.json"
fi

cat > "$EVIDENCE_DIR/README.txt" <<EOF
NexusDesk Email full E2E smoke evidence

Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
BASE_URL: $BASE_URL
TICKET_ID: $TICKET_ID
EMAIL_ACCOUNT_ID: $EMAIL_ACCOUNT_ID
TEST_RECIPIENT: $TEST_RECIPIENT

Files:
$(ls -1 "$EVIDENCE_DIR" | sed 's/^/- /')
EOF

log "Evidence written to $EVIDENCE_DIR"
