#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: validate transcript fixture idempotency and role coverage"
  pass "transcript sync smoke dry-run"
  exit 0
fi

python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

messages = json.loads(Path('backend/tests/fixtures/openclaw/messages.json').read_text())['items']
ids = [m.get('id') for m in messages]
counts = Counter(ids)
if counts['mock-message-001'] < 2:
    raise SystemExit('fixture must include duplicate message id for idempotency test')
roles = {m.get('role') for m in messages}
for expected in ('user', 'assistant', 'system'):
    if expected not in roles:
        raise SystemExit(f'missing role fixture: {expected}')
if not any((not (m.get('text') or '').strip()) and m.get('attachments') for m in messages):
    raise SystemExit('fixture must include attachment-only message')
print('PASS transcript fixture covers user, assistant, system, duplicate id, and attachment-only message')
print('SKIP DB-level sync requires temporary test database or explicit live mode')
PY
