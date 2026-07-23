#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_DIR:-/tmp/nexus_webcall_canary_${TS}}"
BASE_URL="${NEXUS_CANARY_BASE_URL:-}"
REQUIRE_CLEAN_WORKTREE="${REQUIRE_CLEAN_WORKTREE:-0}"

mkdir -p "$OUT"
chmod 700 "$OUT"
cd "$ROOT"

log() {
  printf '%s\n' "$*" | tee -a "$OUT/summary.txt"
}

fail() {
  log "CANARY_RESULT=FAIL"
  log "FAIL_REASON=$*"
  exit 1
}

log "===== NEXUS CANONICAL WEBCALL CANARY READINESS ====="
date -u +"time_utc=%Y-%m-%dT%H:%M:%SZ" | tee -a "$OUT/summary.txt"
log "base_url=${BASE_URL:-offline}"
log "non_deploying=true"

if [ "$REQUIRE_CLEAN_WORKTREE" = "1" ]; then
  git status --short | tee "$OUT/git-status.txt"
  test -z "$(git status --porcelain)" || fail "working tree is not clean"
fi

# This script is evidence-only. It never runs a deployment command or mutates
# production configuration, credentials, migrations, Provider resources, or data.
log "===== STATIC AUTHORITY ====="
python scripts/ci/check_telephony_authority_residue.py \
  2>&1 | tee "$OUT/telephony-residue.txt" || fail "telephony residue gate failed"
python scripts/qualification/service_authority.py \
  --out "$OUT/service-authority.json" \
  2>&1 | tee "$OUT/service-authority.log" || fail "service authority gate failed"

log "===== FOCUSED BACKEND ====="
python -m pytest -q \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_webchat_voice_p0_gap_closure.py \
  backend/tests/test_livekit_voice_provider.py \
  backend/tests/test_livekit_agent_worker.py \
  backend/tests/test_canonical_livekit_telephony.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_public_voice_route_compatibility.py \
  2>&1 | tee "$OUT/backend-focused.txt" || fail "focused backend Voice tests failed"

log "===== FRONTEND ====="
(
  cd webapp
  npm ci --ignore-scripts --no-audit --no-fund
  npm run verify
  npm run e2e
) 2>&1 | tee "$OUT/frontend.txt" || fail "frontend/browser gates failed"

log "===== OPTIONAL ISOLATED HTTP CHECK ====="
if [ -z "$BASE_URL" ]; then
  log "live_http_readiness=SKIPPED_NO_NEXUS_CANARY_BASE_URL"
else
  BASE_URL="${BASE_URL%/}"
  curl --fail --silent --show-error --max-time 12 \
    "$BASE_URL/healthz" | tee "$OUT/healthz.json" \
    || fail "healthz failed"
  curl --fail --silent --show-error --max-time 12 \
    "$BASE_URL/readyz" | tee "$OUT/readyz.json" \
    || fail "readyz failed"
  runtime_headers="$OUT/runtime-config-headers.txt"
  runtime_body="$OUT/runtime-config.json"
  curl --fail --silent --show-error --max-time 12 \
    -D "$runtime_headers" \
    -o "$runtime_body" \
    "$BASE_URL/api/webchat/voice/runtime-config" \
    || fail "runtime-config failed"
  grep -Eq '"media_plane"[[:space:]]*:[[:space:]]*"livekit"|"enabled"[[:space:]]*:[[:space:]]*false' "$runtime_body" \
    || fail "runtime-config is not bounded/canonical"
  if grep -Eqi 'api[_-]?key|api[_-]?secret|participant_token|visitor_token|room_name' "$runtime_body"; then
    fail "runtime-config exposed a secret or topology field"
  fi
  retired_headers="$OUT/retired-route-headers.txt"
  retired_body="$OUT/retired-route-body.txt"
  retired_status="$(curl --silent --show-error --max-time 12 \
    -D "$retired_headers" \
    -o "$retired_body" \
    -w '%{http_code}' \
    "$BASE_URL/webchat/voice/canary-retired")"
  test "$retired_status" = "404" || fail "retired Voice page is still callable"
  grep -Eqi '^permissions-policy:.*microphone=\(\)' "$retired_headers" \
    || fail "retired Voice page did not receive default microphone denial"
  log "live_http_readiness=PASS"
fi

cat > "$OUT/manual-provider-proof-required.txt" <<'EOF'
MANUAL_PROVIDER_PROOF_REQUIRED=YES
Before production authorization, prove with the actual carrier, DID, SIP trunks,
LiveKit project, media worker, STT/TTS models and webhook configuration:
- explicit visitor join on /webcall/{voice_session_id};
- capability- and scope-filtered operator offer and accept;
- one Conversation, Handoff, Voice Session and LiveKit Room;
- two-way audio, hold/resume, DTMF and hangup;
- cold transfer and warm consultation start/complete/cancel;
- Provider failure recovery without fabricated success;
- after-call outcome and deterministic capacity release.
EOF

log "CANARY_RESULT=PASS"
log "evidence_dir=$OUT"
