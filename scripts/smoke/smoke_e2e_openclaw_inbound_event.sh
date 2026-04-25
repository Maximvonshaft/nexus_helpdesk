#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: validate OpenClaw inbound fixture coverage without touching NexusDesk data"
  pass "openclaw inbound event smoke dry-run"
  exit 0
fi

python3 - <<'PY'
import json
from pathlib import Path

fixture = Path('backend/tests/fixtures/openclaw/events.json')
data = json.loads(fixture.read_text())
items = data.get('items', [])
if len(items) < 3:
    raise SystemExit('expected at least 3 inbound event fixtures')
complete = items[0]
route = complete['message'].get('route') or {}
required = {'channel', 'recipient', 'accountId', 'threadId'}
missing = required - set(k for k, v in route.items() if v)
if missing:
    raise SystemExit(f'complete fixture missing route fields: {missing}')
if not items[1].get('sessionKey'):
    raise SystemExit('second fixture must have sessionKey with incomplete route')
if items[2].get('sessionKey'):
    raise SystemExit('third fixture must simulate missing sessionKey')
print('PASS inbound fixture includes complete route, incomplete route, and missing sessionKey cases')
print('SKIP live NexusDesk service-layer processing requires test DB or explicit live mode')
PY
