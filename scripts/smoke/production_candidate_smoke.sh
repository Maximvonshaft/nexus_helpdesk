#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:${CANDIDATE_APP_PORT:-18082}}"
OUT_DIR="${OUT_DIR:-$(mktemp -d -t nexus-candidate-smoke.XXXXXX)}"
REQUIRE_RELEASE_METADATA_COMPLETE="${REQUIRE_RELEASE_METADATA_COMPLETE:-true}"

mkdir -p "$OUT_DIR"

curl_json() {
  local path="$1"
  local out="$2"
  curl -fsS --max-time 10 -H 'Accept: application/json' "${BASE_URL%/}${path}" -o "$out"
}

curl_text() {
  local path="$1"
  local out="$2"
  curl -fsS --max-time 10 "${BASE_URL%/}${path}" -o "$out"
}

curl_json /healthz "$OUT_DIR/healthz.json"
curl_json /readyz "$OUT_DIR/readyz.json"
curl_text /webchat/demo/ "$OUT_DIR/webchat_demo.html"
curl_text /webchat/voice-entry.js "$OUT_DIR/voice-entry.js"

python3 - "$OUT_DIR/healthz.json" "$OUT_DIR/readyz.json" <<'PY'
import json
import os
import sys
from pathlib import Path

healthz = json.loads(Path(sys.argv[1]).read_text())
readyz = json.loads(Path(sys.argv[2]).read_text())

errors = []
if healthz.get("status") != "ok":
    errors.append(f"healthz_status={healthz.get('status')}")
if readyz.get("status") != "ready":
    errors.append(f"readyz_status={readyz.get('status')}")
if readyz.get("database") != "ok":
    errors.append(f"readyz_database={readyz.get('database')}")
if not readyz.get("migration_revision"):
    errors.append("readyz_migration_revision_missing")

if os.getenv("REQUIRE_RELEASE_METADATA_COMPLETE", "true").lower() in {"1", "true", "yes", "on"}:
    if healthz.get("release_metadata_complete") is not True:
        errors.append(f"healthz_release_metadata_missing={healthz.get('release_metadata_missing')}")
    if readyz.get("release_metadata_complete") is not True:
        errors.append(f"readyz_release_metadata_missing={readyz.get('release_metadata_missing')}")

expected_image = os.getenv("EXPECTED_IMAGE_TAG")
if expected_image and healthz.get("image_tag") != expected_image:
    errors.append(f"healthz_image_tag={healthz.get('image_tag')} expected={expected_image}")
if expected_image and readyz.get("image_tag") != expected_image:
    errors.append(f"readyz_image_tag={readyz.get('image_tag')} expected={expected_image}")

expected_sha = os.getenv("EXPECTED_GIT_SHA")
if expected_sha and healthz.get("git_sha") != expected_sha:
    errors.append(f"healthz_git_sha={healthz.get('git_sha')} expected={expected_sha}")
if expected_sha and readyz.get("git_sha") != expected_sha:
    errors.append(f"readyz_git_sha={readyz.get('git_sha')} expected={expected_sha}")

if errors:
    raise SystemExit("\n".join(errors))
PY

grep -q 'data-live-voice-mode="edge-card"' "$OUT_DIR/webchat_demo.html"
grep -q 'data-live-voice-ws-path="/webchat/live/ws"' "$OUT_DIR/webchat_demo.html"
grep -q 'data-live-voice-mode' "$OUT_DIR/voice-entry.js"
grep -q '/webchat/live/ws' "$OUT_DIR/voice-entry.js"
if grep -Eq '47\.87\.143\.41|console\.log|\[Speedaf Voice\]' "$OUT_DIR/voice-entry.js"; then
  echo "voice-entry contains production-only or debug markers" >&2
  exit 2
fi

curl -fsS -i --max-time 10 \
  -X OPTIONS "${BASE_URL%/}/api/webchat/init" \
  -H 'Origin: https://leakle.com' \
  -H 'Access-Control-Request-Method: POST' \
  -o "$OUT_DIR/cors_allowed.txt"

if curl -fsS -i --max-time 10 \
  -X OPTIONS "${BASE_URL%/}/api/webchat/init" \
  -H 'Origin: https://evil.example' \
  -H 'Access-Control-Request-Method: POST' \
  -o "$OUT_DIR/cors_blocked.txt"; then
  echo "unexpected CORS allow for blocked origin" >&2
  exit 2
fi

if [[ "${CHECK_LIVE_VOICE_HEALTH:-false}" =~ ^(1|true|yes|on)$ ]]; then
  curl_json /webchat/live/health "$OUT_DIR/live_voice_health.json"
fi

echo "CANDIDATE_SMOKE_PASS=true"
echo "base_url=$BASE_URL"
echo "evidence_dir=$OUT_DIR"
