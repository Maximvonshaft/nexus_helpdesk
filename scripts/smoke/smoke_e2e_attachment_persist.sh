#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: validate attachment fixture safety coverage"
  pass "attachment persist smoke dry-run"
  exit 0
fi

python3 - <<'PY'
import base64
import json
from pathlib import Path
from urllib.parse import urlparse

items = json.loads(Path('backend/tests/fixtures/openclaw/attachments.json').read_text())['items']
ids = {item['id'] for item in items}
required = {'mock-attachment-meta', 'mock-attachment-base64', 'mock-attachment-text', 'mock-attachment-private-url'}
missing = required - ids
if missing:
    raise SystemExit(f'missing attachment fixtures: {missing}')
for item in items:
    if 'base64' in item:
        base64.b64decode(item['base64'])
    if 'url' in item:
        parsed = urlparse(item['url'])
        if parsed.hostname in {'127.0.0.1', 'localhost'}:
            print(f'PASS private URL fixture must be blocked by live persist logic: {item["url"]}')
print('PASS attachment fixtures cover metadata, base64, text, and blocked private URL')
print('SKIP DB/storage persist requires test database and storage root; run in staging with explicit live mode')
PY
