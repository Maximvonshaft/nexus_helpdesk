#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="${DOMAIN_PROBE_REPORT_DIR:-$ROOT_DIR/artifacts/domain_webchat_shadow_trace_probe}"
REPORT_FILE="$REPORT_DIR/report.json"

mkdir -p "$REPORT_DIR"
cd "$ROOT_DIR"

printf '== Domain WebChat shadow trace E2E probe ==\n'
printf 'root=%s\n' "$ROOT_DIR"
printf 'report=%s\n' "$REPORT_FILE"

printf '== Compile runtime modules ==\n'
PYTHONPATH=backend python -m compileall -q \
  backend/app/services/domain_intelligence \
  backend/app/domain_packs \
  backend/app/services/webchat_fast_ai_service.py \
  backend/scripts/run_domain_runtime_eval.py \
  scripts/probe_domain_webchat_shadow_trace_e2e.py

printf '== Run domain unit tests ==\n'
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_domain_query_understanding.py \
  backend/tests/test_webchat_domain_shadow_trace.py

printf '== Run domain fixture eval ==\n'
PYTHONPATH=backend python backend/scripts/run_domain_runtime_eval.py \
  --fixture backend/tests/fixtures/domain_intent_cases.json \
  --strict

printf '== Run WebChat shadow trace E2E probe ==\n'
PYTHONPATH=backend python scripts/probe_domain_webchat_shadow_trace_e2e.py --out "$REPORT_FILE"

printf '== Probe report ==\n'
cat "$REPORT_FILE"
printf '\n== Domain WebChat shadow trace E2E probe PASSED ==\n'
