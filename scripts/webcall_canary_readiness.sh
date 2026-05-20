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
  "$OUT/.venv-test/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$OUT/.venv-test/bin/python" -m pip install -r backend/requirements.txt >/dev/null
  "$OUT/.venv-test/bin/python" -m pip install pytest >/dev/null
  PYTEST_CMD="$OUT/.venv-test/bin/python -m pytest"
fi

log "PYTEST_CMD=$PYTEST_CMD"

$PYTEST_CMD \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_livekit_voice_provider.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
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
log "===== 6. OPTIONAL LIVE HTTP READINESS ====="
if [ -n "$BASE_URL" ]; then
  curl -fsS "$BASE_URL/api/webchat/voice/runtime-config" > "$OUT/runtime_config.json" || fail "runtime-config endpoint failed"
  cat "$OUT/runtime_config.json" | grep -E 'LIVEKIT_API_SECRET|api_secret|secret|password|refresh_token' && fail "runtime-config exposed forbidden secret marker"
  grep -q 'livekit_url' "$OUT/runtime_config.json" || fail "runtime-config missing livekit_url"
  log "runtime_config_http=PASS"

  curl -fsSI "$BASE_URL/webchat-voice" > "$OUT/webchat_voice_headers.txt" || fail "webchat-voice page not reachable"
  log "webchat_voice_http=PASS"
else
  log "live_http_readiness=SKIPPED_NO_NEXUS_CANARY_BASE_URL"
fi

log ""
log "===== FINAL ====="
log "CANARY_RESULT=PASS"
log "OUT=$OUT"
