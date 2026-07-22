#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:${CANDIDATE_APP_PORT:-18082}}"
OUT_DIR="${OUT_DIR:-$(mktemp -d -t nexus-candidate-smoke.XXXXXX)}"
REQUIRE_RELEASE_METADATA_COMPLETE="${REQUIRE_RELEASE_METADATA_COMPLETE:-true}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
curl_json /api/webchat/voice/runtime-config "$OUT_DIR/voice_runtime.json"
curl_text /webchat/demo/ "$OUT_DIR/webchat_demo.html"
curl_text /webchat/voice-entry.js "$OUT_DIR/voice-entry.js"

python3 - "$OUT_DIR/healthz.json" "$OUT_DIR/readyz.json" "$OUT_DIR/voice_runtime.json" <<'PY'
import json
import os
import sys
from pathlib import Path

healthz = json.loads(Path(sys.argv[1]).read_text())
readyz = json.loads(Path(sys.argv[2]).read_text())
voice_runtime = json.loads(Path(sys.argv[3]).read_text())

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

if voice_runtime.get("media_plane") not in {"livekit", "mock"}:
    errors.append(f"voice_media_plane={voice_runtime.get('media_plane')}")
serialized_voice = json.dumps(voice_runtime, sort_keys=True)
for forbidden in ("LIVEKIT_API_SECRET", "LIVEKIT_API_KEY", "livekit_api_secret", "livekit_api_key"):
    if forbidden in serialized_voice:
        errors.append(f"voice_runtime_secret_field={forbidden}")

if errors:
    raise SystemExit("\n".join(errors))
PY

for file in "$OUT_DIR/webchat_demo.html" "$OUT_DIR/voice-entry.js"; do
  if grep -Eiq '/webchat/live/ws|LIVE_VOICE_UPSTREAM|nexus_media_edge|data-live-voice-mode=["'"']edge-card["'"']' "$file"; then
    echo "legacy live-voice media path present in $(basename "$file")" >&2
    exit 2
  fi
done

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

if [[ "${CHECK_WEBCHAT_WS_UPGRADE:-false}" =~ ^(1|true|yes|on)$ ]]; then
  python3 "$SCRIPT_DIR/websocket_upgrade_probe.py" \
    --base-url "${BASE_URL%/}" \
    --path "/api/webchat/ws" \
    --timeout-seconds "${WEBCHAT_WS_TIMEOUT_SECONDS:-10}"
fi

echo "CANDIDATE_SMOKE_PASS=true"
echo "base_url=$BASE_URL"
echo "evidence_dir=$OUT_DIR"
