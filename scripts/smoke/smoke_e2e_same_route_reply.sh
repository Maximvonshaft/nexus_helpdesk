#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3
need_cmd curl

MOCK_PORT="${OPENCLAW_MOCK_PORT:-18792}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: would start mock OpenClaw and assert messages_send uses original channel/recipient/accountId/threadId"
  pass "same-route reply smoke dry-run"
  exit 0
fi

python3 backend/scripts/mock_openclaw_server.py --port "$MOCK_PORT" >/tmp/nexusdesk-openclaw-mock.log 2>&1 &
MOCK_PID=$!
trap 'kill "$MOCK_PID" >/dev/null 2>&1 || true' EXIT
sleep 1
curl -fsS "$MOCK_URL/healthz" >/dev/null

PAYLOAD='{
  "sessionKey":"mock-session-001",
  "body":"We have received your request and will check it shortly.",
  "route":{
    "channel":"whatsapp",
    "recipient":"+41000000001",
    "accountId":"mock-wa-account",
    "threadId":"mock-thread-001"
  }
}'
RESP_FILE="/tmp/nexusdesk-same-route-response.json"
curl -fsS -X POST "$MOCK_URL/messages_send" -H 'content-type: application/json' --data "$PAYLOAD" > "$RESP_FILE"
python3 - "$RESP_FILE" <<'PY'
import json
import sys
from pathlib import Path
resp = json.loads(Path(sys.argv[1]).read_text())
if not resp.get('ok'):
    raise SystemExit(f'mock send failed: {resp}')
route = resp['message']['route']
expected = {'channel':'whatsapp','recipient':'+41000000001','accountId':'mock-wa-account','threadId':'mock-thread-001'}
if route != expected:
    raise SystemExit(f'route mismatch: {route} != {expected}')
print('PASS mock messages_send preserved channel/recipient/accountId/threadId')
PY

BAD='{"sessionKey":"mock-session-002","body":"missing route","route":{"channel":"whatsapp"}}'
STATUS="$(curl -s -o /tmp/nexusdesk-bad-route.json -w '%{http_code}' -X POST "$MOCK_URL/messages_send" -H 'content-type: application/json' --data "$BAD")"
[ "$STATUS" = "400" ] || fail "missing route should return 400, got $STATUS"
pass "same-route reply mock proof"
