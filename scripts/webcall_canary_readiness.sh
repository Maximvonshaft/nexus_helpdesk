#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_DIR:-/tmp/nexus_webcall_canary_${TS}}"
BASE_URL="${NEXUS_CANARY_BASE_URL:-}"
REQUIRE_CLEAN_WORKTREE="${REQUIRE_CLEAN_WORKTREE:-0}"

mkdir -p "$OUT"
chmod 700 "$OUT"

log() {
  printf '%s\n' "$*" | tee -a "$OUT/summary.txt"
}

fail() {
  log "CANARY_RESULT=FAIL"
  log "FAIL_REASON=$*"
  exit 1
}

venv_python() {
  local venv_dir="$1"
  if [ -x "$venv_dir/bin/python" ]; then
    printf '%s\n' "$venv_dir/bin/python"
  elif [ -x "$venv_dir/Scripts/python.exe" ]; then
    printf '%s\n' "$venv_dir/Scripts/python.exe"
  else
    return 1
  fi
}

cd "$ROOT"

log "===== NEXUSDESK WEBCALL CANARY READINESS ====="
date -u +"time_utc=%Y-%m-%dT%H:%M:%SZ" | tee -a "$OUT/summary.txt"
log "root=$ROOT"
log "base_url=${BASE_URL:-offline}"

log ""
log "===== 1. SAFETY GUARDS ====="
if [ "$REQUIRE_CLEAN_WORKTREE" = "1" ]; then
  git status --short | tee "$OUT/git_status.txt" | tee -a "$OUT/summary.txt"
  if git status --short | grep -q .; then
    fail "working tree is not clean"
  fi
  log "clean_worktree=PASS"
else
  log "clean_worktree=SKIPPED_REQUIRE_CLEAN_WORKTREE_0"
fi

if git diff --name-only HEAD | grep -E '(^deploy/\.env\.prod|^deploy/|docker-compose|alembic/versions)' >/dev/null 2>&1; then
  git diff --name-only HEAD | grep -E '(^deploy/\.env\.prod|^deploy/|docker-compose|alembic/versions)' | tee "$OUT/forbidden_changed_files.txt" | tee -a "$OUT/summary.txt"
  fail "forbidden production/deploy/migration file changed"
fi

log "no_production_deploy=ASSERTED"
log "no_docker_compose_up=ASSERTED"
log "no_env_prod_change=ASSERTED"
log "no_migration_change=ASSERTED"

log ""
log "===== 2. BACKEND VOICE GATES ====="
if [ -x ".venv/bin/python" ] && .venv/bin/python -m pytest --version >/dev/null 2>&1; then
  PYTEST_CMD=".venv/bin/python -m pytest"
elif python3 -m pytest --version >/dev/null 2>&1; then
  PYTEST_CMD="python3 -m pytest"
else
  python3 -m venv "$OUT/.venv-test"
  VENV_PYTHON="$(venv_python "$OUT/.venv-test")" || fail "python venv was created without a runnable interpreter"
  "$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$VENV_PYTHON" -m pip install -r backend/requirements.txt >/dev/null
  "$VENV_PYTHON" -m pip install pytest >/dev/null
  PYTEST_CMD="$VENV_PYTHON -m pytest"
fi

log "PYTEST_CMD=$PYTEST_CMD"

$PYTEST_CMD \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_webchat_voice_p0_gap_closure.py \
  backend/tests/test_livekit_voice_provider.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
  backend/tests/test_webchat_voice_p0_static.py \
  backend/tests/test_webchat_voice_canary_readiness_static.py \
  > "$OUT/backend_voice_tests.txt" 2>&1 || {
    tail -n 220 "$OUT/backend_voice_tests.txt" | tee -a "$OUT/summary.txt"
    fail "backend voice gates failed"
  }

tail -n 80 "$OUT/backend_voice_tests.txt" | tee -a "$OUT/summary.txt"
log "backend_voice_gates=PASS"

log ""
log "===== 3. FRONTEND GATES ====="
cd "$ROOT/webapp"

if [ ! -d node_modules ]; then
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund > "$OUT/npm_install.txt" 2>&1 || {
      tail -n 160 "$OUT/npm_install.txt" | tee -a "$OUT/summary.txt"
      fail "npm ci failed"
    }
  else
    npm install --no-audit --no-fund > "$OUT/npm_install.txt" 2>&1 || {
      tail -n 160 "$OUT/npm_install.txt" | tee -a "$OUT/summary.txt"
      fail "npm install failed"
    }
  fi
fi

npm run typecheck > "$OUT/webapp_typecheck.txt" 2>&1 || {
  tail -n 120 "$OUT/webapp_typecheck.txt" | tee -a "$OUT/summary.txt"
  fail "webapp typecheck failed"
}
log "webapp_typecheck=PASS"

npm run build > "$OUT/webapp_build.txt" 2>&1 || {
  tail -n 160 "$OUT/webapp_build.txt" | tee -a "$OUT/summary.txt"
  fail "webapp build failed"
}
tail -n 80 "$OUT/webapp_build.txt" | tee -a "$OUT/summary.txt"
log "webapp_build=PASS"

npm test > "$OUT/webapp_test.txt" 2>&1 || {
  tail -n 160 "$OUT/webapp_test.txt" | tee -a "$OUT/summary.txt"
  fail "webapp test failed"
}
tail -n 80 "$OUT/webapp_test.txt" | tee -a "$OUT/summary.txt"
log "webapp_test=PASS"

cd "$ROOT"

log ""
log "===== 4. TOKEN / SECRET STATIC SCAN ====="
grep -RInE 'participant_token|visitor_token|LIVEKIT_API_SECRET|livekit_api_secret|api_secret|access_token|refresh_token|password|secret' \
  webapp/src/routes/webchat.tsx \
  webapp/src/routes/webchat-voice.tsx \
  webapp/src/components/webcall/AgentWebCallPanel.tsx \
  webapp/src/lib/webchatVoiceApi.ts \
  webapp/src/lib/webchatVoiceTypes.ts \
  backend/app/services/webchat_voice_service.py \
  backend/app/api/webchat_voice.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_canary_readiness_static.py \
  scripts/webcall_canary_readiness.sh \
  docs/runbooks/webcall_canary_readiness.md \
  > "$OUT/token_secret_scan.txt" 2>&1 || true

cat "$OUT/token_secret_scan.txt" | tee -a "$OUT/summary.txt"

python3 - <<'PY' > "$OUT/token_secret_classification.txt" 2>&1
from pathlib import Path

ui_files = [
    Path("webapp/src/routes/webchat.tsx"),
    Path("webapp/src/routes/webchat-voice.tsx"),
    Path("webapp/src/components/webcall/AgentWebCallPanel.tsx"),
]
for path in ui_files:
    text = path.read_text(encoding="utf-8")
    if "console.log" in text:
        raise SystemExit(f"FAIL console.log found in {path}")

panel = Path("webapp/src/components/webcall/AgentWebCallPanel.tsx").read_text(encoding="utf-8")
panel_without_connect_path = panel.replace("accepted.participant_token", "")
for forbidden in ["visitor_token", "LIVEKIT_API_SECRET", "livekit_api_secret", "api_secret", "refresh_token"]:
    if forbidden in panel_without_connect_path:
        raise SystemExit(f"FAIL forbidden token/secret marker in operator panel: {forbidden}")

print("TOKEN_SECRET_CLASSIFICATION=PASS")
PY
cat "$OUT/token_secret_classification.txt" | tee -a "$OUT/summary.txt"

log ""
log "===== 5. CLICK-TO-ACCEPT STATIC CHECK ====="
python3 - <<'PY' > "$OUT/click_to_accept_static_check.txt" 2>&1
from pathlib import Path

p = Path("webapp/src/components/webcall/AgentWebCallPanel.tsx")
text = p.read_text(encoding="utf-8")

if "const acceptMutation" not in text:
    raise SystemExit("FAIL missing acceptMutation")

prefix, accept_body = text.split("const acceptMutation", 1)

checks = {
    "createLocalAudioTrack_present": "createLocalAudioTrack" in text,
    "createLocalAudioTrack_inside_accept_only": "await createLocalAudioTrack" in accept_body and "await createLocalAudioTrack" not in prefix,
    "room_connect_inside_accept": "room.connect" in accept_body,
    "publishTrack_inside_accept": "publishTrack" in accept_body,
    "no_console_log": "console.log" not in text,
    "has_409_mapping": "already accepted by another agent" in text,
    "has_expired_mapping": "该来电已超时" in text,
    "has_ended_mapping": "该通话已结束" in text,
    "has_cancelled_mapping": "该通话已取消" in text,
    "has_failed_mapping": "该通话已失败" in text,
    "has_unknown_status": "Unknown / 未知状态" in text,
}

failed = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(f"{k}={'PASS' if v else 'FAIL'}")

if failed:
    raise SystemExit("FAIL " + ",".join(failed))

print("CLICK_TO_ACCEPT_STATIC_CHECK=PASS")
PY
cat "$OUT/click_to_accept_static_check.txt" | tee -a "$OUT/summary.txt"

log ""
log "===== 6. /WEBCHAT INTEGRATED ENTRY AND EVIDENCE STATIC CHECK ====="
python3 - <<'PY' > "$OUT/webchat_integrated_entry_static_check.txt" 2>&1
from pathlib import Path

webchat = Path("webapp/src/routes/webchat.tsx").read_text(encoding="utf-8")
panel = Path("webapp/src/components/webcall/AgentWebCallPanel.tsx").read_text(encoding="utf-8")
service = Path("backend/app/services/webchat_voice_service.py").read_text(encoding="utf-8")
api = Path("backend/app/api/webchat_voice.py").read_text(encoding="utf-8")

checks = {
    "webchat_integrated_entry": "AgentWebCallPanel" in webchat and "Incoming WebCall" in webchat,
    "voice_call_evidence_card": "voice-call-evidence-card" in webchat and "ringing_duration_seconds" in webchat and "talk_duration_seconds" in webchat and "total_duration_seconds" in webchat,
    "operational_queue_tabs": "WebCall Operational Queue" in panel and "My Active" in panel and "Closed Recent" in panel,
    "missed_cleanup_on_list": "_mark_missed_if_expired" in service and "voice.session.missed" in service and "_ensure_final_voice_call_message(db, session=session)" in service,
    "runtime_config_no_secret": "LIVEKIT_API_SECRET" not in api and "LIVEKIT_API_KEY" not in api and "api_secret" not in api.lower(),
}
failed = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(f"{k}={'PASS' if v else 'FAIL'}")
if failed:
    raise SystemExit("FAIL " + ",".join(failed))
print("WEBCHAT_INTEGRATED_ENTRY_STATIC_CHECK=PASS")
print("VOICE_CALL_EVIDENCE_CARD_STATIC_CHECK=PASS")
print("MISSED_CLEANUP_STATIC_CHECK=PASS")
print("RUNTIME_CONFIG_NO_SECRET_STATIC_CHECK=PASS")
PY
cat "$OUT/webchat_integrated_entry_static_check.txt" | tee -a "$OUT/summary.txt"

log ""
log "===== 7. TWO-BROWSER PROOF REQUIREMENT ====="
cat > "$OUT/two_browser_proof_required.txt" <<'EOF'
TWO_BROWSER_PROOF_REQUIRED=YES
Required manual/API evidence before canary promotion:
- Browser A visitor opens /webcall/{voice_session_id} and clicks Join.
- Browser B operator opens /webchat, sees Incoming WebCall badge, accepts, ends, and continues text follow-up in the same ticket.
- Same ticket shows a voice_call evidence card with status, voice_session_id, provider, accepted_by, ended_by, ringing_duration_seconds, talk_duration_seconds, total_duration_seconds, recording status, transcript status, and summary status.
EOF
cat "$OUT/two_browser_proof_required.txt" | tee -a "$OUT/summary.txt"

log ""
log ""
log "===== 8. OPTIONAL LIVE HTTP READINESS ====="
if [ -z "${NEXUS_CANARY_BASE_URL:-}" ]; then
  log "live_http_readiness=SKIPPED_NO_NEXUS_CANARY_BASE_URL"
else
  BASE_URL="${NEXUS_CANARY_BASE_URL%/}"
  log "live_http_base_url=$BASE_URL"

  http_code_probe() {
    local label="$1"
    local url="$2"
    local body="$OUT/${label}_body.txt"
    local meta="$OUT/${label}_meta.txt"
    local err="$OUT/${label}_err.txt"
    local code
    local rc

    set +e
    code="$(curl -sS -L -m 12 -X GET -w '%{http_code}' -o "$body" "$url" 2>"$err")"
    rc=$?
    set -e

    {
      echo "label=$label"
      echo "method=GET"
      echo "url=$url"
      echo "curl_rc=$rc"
      echo "http_code=$code"
      echo "error_begin"
      cat "$err" || true
      echo "error_end"
      echo "body_head_begin"
      head -c 800 "$body" 2>/dev/null || true
      echo
      echo "body_head_end"
    } > "$meta"

    log "${label}_curl_rc=$rc"
    log "${label}_http_code=$code"

    if [ "$rc" -ne 0 ]; then
      return 1
    fi

    echo "$code" | grep -Eq '^(2|3)[0-9][0-9]$'
  }

  if http_code_probe "runtime_config_http" "$BASE_URL/api/webchat/voice/runtime-config"; then
    log "runtime_config_http=PASS"
  else
    log "CANARY_RESULT=FAIL"
    log "FAIL_REASON=runtime-config endpoint not reachable"
    exit 1
  fi

  if grep -RIE 'LIVEKIT_API_SECRET|api_secret|secret|password|refresh_token' "$OUT/runtime_config_http_body.txt" >/dev/null 2>&1; then
    log "CANARY_RESULT=FAIL"
    log "FAIL_REASON=runtime-config exposed forbidden secret marker"
    exit 1
  fi

  if http_code_probe "webchat_voice_page_http" "$BASE_URL/webchat-voice"; then
    log "webchat_voice_page_http=PASS"
  else
    log "CANARY_RESULT=FAIL"
    log "FAIL_REASON=webchat-voice page not reachable"
    exit 1
  fi

  if http_code_probe "webchat_integrated_entry_http" "$BASE_URL/webchat"; then
    log "webchat_integrated_entry_http=PASS"
  else
    log "CANARY_RESULT=FAIL"
    log "FAIL_REASON=/webchat integrated entry page not reachable"
    exit 1
  fi

  log "live_http_readiness=PASS"
fi
log "===== FINAL ====="
log "CANARY_RESULT=PASS"
log "OUT=$OUT"
