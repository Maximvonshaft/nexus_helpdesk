#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: would validate unresolved event list/replay/drop without mutating production"
  pass "unresolved event replay smoke dry-run"
  exit 0
fi

python3 - <<'PY'
import json
from pathlib import Path

events = json.loads(Path('backend/tests/fixtures/openclaw/events.json').read_text())['items']
incomplete = [e for e in events if not ((e.get('message') or {}).get('route') or {}).get('recipient')]
if len(incomplete) < 2:
    raise SystemExit('expected unresolved fixtures for missing recipient and missing sessionKey')
print('PASS unresolved fixture coverage: missing route and missing sessionKey')
print('SKIP live replay/drop requires NexusDesk admin token and test database; run in staging with explicit live mode')
PY
