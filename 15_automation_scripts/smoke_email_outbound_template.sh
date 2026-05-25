#!/usr/bin/env bash
set -euo pipefail

: "${BASE_URL:?Set BASE_URL}"
: "${TOKEN:?Set TOKEN}"
: "${TICKET_ID:?Set TICKET_ID}"

echo "[1/4] capabilities"
curl -fsS "$BASE_URL/api/tickets/$TICKET_ID/outbound/channels/capabilities"   -H "Authorization: Bearer $TOKEN" | tee email_capabilities.json

echo "[2/4] queue email"
curl -fsS -X POST "$BASE_URL/api/tickets/$TICKET_ID/outbound/send"   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"channel":"email","subject":"NexusDesk Email Smoke","body":"NexusDesk staging smoke email."}' | tee email_send_response.json

echo "[3/4] operator: run/check worker logs according to deployment"
echo "[4/4] operator: confirm provider id, delivery event, and mailbox receipt"
