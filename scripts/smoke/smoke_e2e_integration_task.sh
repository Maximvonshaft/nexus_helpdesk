#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd curl
need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: would POST /api/v1/integration/task with Idempotency-Key and validate ticket reuse"
  pass "integration task smoke dry-run"
  exit 0
fi

require_env NEXUSDESK_INTEGRATION_CLIENT_ID NEXUSDESK_INTEGRATION_CLIENT_KEY
TRACKING="${SMOKE_PREFIX}-TRK-001"
CONTACT="${SMOKE_PREFIX}-contact-001"
IDEMPOTENCY="${SMOKE_PREFIX}-idem-001"
PAYLOAD="$(python3 - <<PY
import json, os
print(json.dumps({
  'contact_id': os.environ.get('CONTACT', '$CONTACT'),
  'customer_name': 'Round A Smoke Customer',
  'phone': '+41000000001',
  'tracking_number': os.environ.get('TRACKING', '$TRACKING'),
  'issue_type': 'delivery_inquiry',
  'priority': 'normal',
  'description': 'Round A integration smoke task. Do not use in production live customer flows.',
  'country_code': 'CH'
}))
PY
)"

call_task() {
  curl -fsS -X POST "${API_URL%/}/api/v1/integration/task" \
    -H 'content-type: application/json' \
    -H "X-Client-Key-Id: $NEXUSDESK_INTEGRATION_CLIENT_ID" \
    -H "X-Client-Key: $NEXUSDESK_INTEGRATION_CLIENT_KEY" \
    -H "Idempotency-Key: $1" \
    --data "$PAYLOAD"
}

RESP1="$(call_task "$IDEMPOTENCY")"
RESP2="$(call_task "$IDEMPOTENCY")"
ID1="$(printf '%s' "$RESP1" | json_get 'ticket_id' 2>/dev/null || printf '%s' "$RESP1" | json_get 'id')"
ID2="$(printf '%s' "$RESP2" | json_get 'ticket_id' 2>/dev/null || printf '%s' "$RESP2" | json_get 'id')"
[ -n "$ID1" ] || fail "first response did not include ticket id: $RESP1"
[ "$ID1" = "$ID2" ] || fail "idempotency mismatch: first=$ID1 second=$ID2"

RESP3="$(call_task "${SMOKE_PREFIX}-idem-002")"
ID3="$(printf '%s' "$RESP3" | json_get 'ticket_id' 2>/dev/null || printf '%s' "$RESP3" | json_get 'id')"
[ -n "$ID3" ] || fail "third response did not include ticket id: $RESP3"
info "same contact/tracking returned ticket ids: $ID1 / $ID3"
pass "integration task smoke"
