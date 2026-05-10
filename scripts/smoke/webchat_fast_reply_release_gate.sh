#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18081}"
CONCURRENCY="${CONCURRENCY:-25}"
REQUESTS="${REQUESTS:-100}"
P95_MS="${P95_MS:-5000}"
MAX_MS="${MAX_MS:-8000}"
DIST_DIR="${DIST_DIR:-webapp/dist}"
STATIC_DIR="${STATIC_DIR:-backend/app/static/webchat}"
REPORT_DIR="${REPORT_DIR:-./outputs/webchat_fast_reply_release_gate}"
OPENCLAW_RESPONSES_URL="${OPENCLAW_RESPONSES_URL:-}"
SKIP_OPENCLAW_PROBE="${SKIP_OPENCLAW_PROBE:-false}"
SKIP_CONCURRENCY_SMOKE="${SKIP_CONCURRENCY_SMOKE:-false}"
SKIP_SECRET_SCAN="${SKIP_SECRET_SCAN:-false}"

mkdir -p "$REPORT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$REPORT_DIR/webchat_fast_reply_release_gate_${TS}.log"
SUMMARY="$REPORT_DIR/webchat_fast_reply_release_gate_${TS}.summary"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$REPORT"
}

run_step() {
  local name="$1"
  shift
  log "STEP_START ${name}"
  "$@" 2>&1 | tee -a "$REPORT"
  log "STEP_PASS ${name}"
}

fail() {
  log "STEP_FAIL $*"
  {
    echo "STATUS=FAIL"
    echo "FAILED_STEP=$*"
    echo "REPORT=$REPORT"
  } > "$SUMMARY"
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "missing_file:${path}"
}

health_check() {
  python - "$BASE_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1].rstrip('/')
for path in ('/healthz', '/readyz'):
    url = base + path
    with urllib.request.urlopen(url, timeout=8) as resp:
        body = resp.read().decode('utf-8', errors='replace')
        if resp.status >= 400:
            raise SystemExit(f'{path} returned HTTP {resp.status}')
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {'raw': body[:200]}
        print(json.dumps({'url': url, 'status': resp.status, 'body': parsed}, ensure_ascii=False, sort_keys=True))
PY
}

api_contract_probe() {
  python - "$BASE_URL" <<'PY'
import json
import sys
import time
import urllib.request
import uuid

base = sys.argv[1].rstrip('/')
url = base + '/api/webchat/fast-reply'
payload = {
    'tenant_key': 'default',
    'channel_key': 'release-gate',
    'session_id': 'release_gate_session_' + uuid.uuid4().hex[:12],
    'client_message_id': 'release_gate_msg_' + uuid.uuid4().hex[:12],
    'body': 'Hi Speedy, please reply briefly.',
    'recent_context': [],
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json', 'Origin': 'http://localhost'},
    method='POST',
)
started = time.perf_counter()
with urllib.request.urlopen(req, timeout=10) as resp:
    body = resp.read().decode('utf-8', errors='replace')
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    parsed = json.loads(body or '{}')
    print(json.dumps({'url': url, 'status': resp.status, 'elapsed_ms': elapsed_ms, 'body': parsed}, ensure_ascii=False, sort_keys=True))
    if resp.status >= 400:
        raise SystemExit(f'fast-reply returned HTTP {resp.status}')
    if parsed.get('ok') is not True or parsed.get('ai_generated') is not True or not parsed.get('reply'):
        raise SystemExit('fast-reply did not return an AI-generated reply; check OpenClaw runtime config')
PY
}

main() {
  : > "$REPORT"
  log "WEBCHAT_FAST_REPLY_RELEASE_GATE_START"
  log "BASE_URL=${BASE_URL}"
  log "CONCURRENCY=${CONCURRENCY} REQUESTS=${REQUESTS} P95_MS=${P95_MS} MAX_MS=${MAX_MS}"
  log "DIST_DIR=${DIST_DIR} STATIC_DIR=${STATIC_DIR} REPORT_DIR=${REPORT_DIR}"

  require_file "scripts/smoke/webchat_fast_reply_concurrency_smoke.py"
  require_file "scripts/smoke/browser_bundle_secret_scan.py"
  require_file "scripts/smoke/openclaw_gateway_private_exposure_probe.py"

  run_step health_readyz health_check

  if [[ "$SKIP_SECRET_SCAN" != "true" ]]; then
    run_step browser_static_secret_scan python scripts/smoke/browser_bundle_secret_scan.py \
      --dist "$DIST_DIR" \
      --static "$STATIC_DIR"
  else
    log "STEP_SKIP browser_static_secret_scan"
  fi

  if [[ "$SKIP_OPENCLAW_PROBE" != "true" ]]; then
    if [[ -z "$OPENCLAW_RESPONSES_URL" ]]; then
      fail "OPENCLAW_RESPONSES_URL_required_for_private_probe"
    fi
    run_step openclaw_private_exposure_probe python scripts/smoke/openclaw_gateway_private_exposure_probe.py \
      --responses-url "$OPENCLAW_RESPONSES_URL"
  else
    log "STEP_SKIP openclaw_private_exposure_probe"
  fi

  run_step fast_reply_api_contract_probe api_contract_probe

  if [[ "$SKIP_CONCURRENCY_SMOKE" != "true" ]]; then
    run_step webchat_fast_reply_concurrency_smoke python scripts/smoke/webchat_fast_reply_concurrency_smoke.py \
      --base-url "$BASE_URL" \
      --concurrency "$CONCURRENCY" \
      --requests "$REQUESTS" \
      --p95-ms "$P95_MS" \
      --max-ms "$MAX_MS"
  else
    log "STEP_SKIP webchat_fast_reply_concurrency_smoke"
  fi

  log "WEBCHAT_FAST_REPLY_RELEASE_GATE_PASS"
  {
    echo "STATUS=PASS"
    echo "REPORT=$REPORT"
    echo "BASE_URL=$BASE_URL"
    echo "CONCURRENCY=$CONCURRENCY"
    echo "REQUESTS=$REQUESTS"
    echo "P95_MS=$P95_MS"
    echo "MAX_MS=$MAX_MS"
  } > "$SUMMARY"
  cat "$SUMMARY"
}

main "$@"
