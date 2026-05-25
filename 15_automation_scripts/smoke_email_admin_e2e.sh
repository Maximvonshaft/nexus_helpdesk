#!/usr/bin/env bash
set -euo pipefail

# NexusDesk Email Admin E2E Smoke
#
# Required env:
#   BASE_URL     e.g. http://127.0.0.1:18081
#   TOKEN        Bearer token without the "Bearer " prefix
#   TICKET_ID    Controlled test ticket id
#   TEST_EMAIL   Controlled recipient email
#
# Optional env:
#   SUBJECT      Smoke email subject
#   BODY         Smoke email body
#   OUT_DIR      Evidence output directory

BASE_URL="${BASE_URL:-http://127.0.0.1:18081}"
TOKEN="${TOKEN:-}"
TICKET_ID="${TICKET_ID:-}"
TEST_EMAIL="${TEST_EMAIL:-}"
SUBJECT="${SUBJECT:-NexusDesk Email smoke}"
BODY="${BODY:-This is a controlled Email outbound smoke test.}"
OUT_DIR="${OUT_DIR:-email_smoke_evidence_$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ -z "$TOKEN" || -z "$TICKET_ID" || -z "$TEST_EMAIL" ]]; then
  echo "ERROR: Set TOKEN, TICKET_ID, TEST_EMAIL" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

json_payload() {
  if command -v jq >/dev/null 2>&1; then
    jq -n \
      --arg channel "email" \
      --arg subject "$SUBJECT" \
      --arg to_email "$TEST_EMAIL" \
      --arg body "$BODY" \
      '{channel:$channel, subject:$subject, to_email:$to_email, body:$body}'
  else
    python3 - "$SUBJECT" "$TEST_EMAIL" "$BODY" <<'PY'
import json
import sys
subject, to_email, body = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "channel": "email",
    "subject": subject,
    "to_email": to_email,
    "body": body,
}, ensure_ascii=False))
PY
  fi
}

echo "[1] Health"
curl -fsS "$BASE_URL/healthz" | tee "$OUT_DIR/healthz.json"
curl -fsS "$BASE_URL/readyz" | tee "$OUT_DIR/readyz.json"

echo "[2] List Email accounts"
curl -fsS "${AUTH[@]}" "$BASE_URL/api/admin/email/channel-accounts" | tee "$OUT_DIR/email_accounts.json"

echo "[3] Ticket channel capabilities"
curl -fsS "${AUTH[@]}" "$BASE_URL/api/tickets/$TICKET_ID/outbound/channels/capabilities" | tee "$OUT_DIR/capabilities.json"

echo "[4] Send controlled Email reply"
payload="$(json_payload)"
printf '%s\n' "$payload" | tee "$OUT_DIR/send_payload.json"
curl -fsS "${AUTH[@]}" -X POST "$BASE_URL/api/tickets/$TICKET_ID/outbound/send" \
  -d "$payload" | tee "$OUT_DIR/send_result.json"

echo "[5] Queue summary"
curl -fsS "${AUTH[@]}" "$BASE_URL/api/admin/queues/summary" | tee "$OUT_DIR/queue_summary.json"

echo "[6] Evidence summary"
cat > "$OUT_DIR/README.md" <<EOF
# Email smoke evidence

- BASE_URL: $BASE_URL
- TICKET_ID: $TICKET_ID
- TEST_EMAIL: $TEST_EMAIL
- SUBJECT: $SUBJECT
- Generated at UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)

Manual follow-up:
1. Check worker logs for Email provider dispatch.
2. Check SES/provider console for accepted/delivered/bounce events.
3. Check Nexus ticket timeline for queued/accepted/delivery cards.
4. If rollback test is being performed, compare Email queue counts before/after.
EOF

echo "Smoke complete. Evidence directory: $OUT_DIR"
