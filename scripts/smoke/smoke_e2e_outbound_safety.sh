#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd python3

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: validate outbound safety cases through Python import or static fallback"
  pass "outbound safety smoke dry-run"
  exit 0
fi

python3 - <<'PY'
import pathlib
import sys

root = pathlib.Path.cwd()
sys.path.insert(0, str(root / 'backend'))
from app.services.outbound_safety import evaluate_outbound_safety

class Ticket:
    id = 1

cases = [
    ('empty', '', 'manual', False, 'block'),
    ('secret', 'Please see SECRET_KEY and token in stack trace', 'manual', False, 'block'),
    ('logistics_en', 'Your parcel will arrive today.', 'manual', False, 'review'),
    ('logistics_cn', '包裹今天送达。', 'manual', False, 'review'),
    ('ai_default', 'We have checked your parcel.', 'ai_auto_reply', False, 'review'),
    ('safe_manual', 'We have received your request and will check it shortly.', 'manual', False, 'allow'),
]

for name, body, source, evidence, expected in cases:
    decision = evaluate_outbound_safety(Ticket(), body, source, has_fact_evidence=evidence)
    if decision.level != expected:
        raise SystemExit(f'{name}: expected {expected}, got {decision.level}, reasons={decision.reasons}')
    print(f'PASS {name}: {decision.level}')
print('PASS outbound safety smoke')
PY
