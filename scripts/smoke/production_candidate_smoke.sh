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
grep -Fq "widget.setAttribute('data-live-voice-mode', 'off')" "$OUT_DIR/voice-entry.js"
if grep -Fq "widget.setAttribute('data-live-voice-mode', 'edge-card')" "$OUT_DIR/voice-entry.js" \
  || grep -Fq "widget.setAttribute('data-live-voice-ws-path', '/webchat/live/ws')" "$OUT_DIR/voice-entry.js" \
  || grep -Fq 'window.NexusDeskWebChat.open()' "$OUT_DIR/voice-entry.js"; then
  echo "voice-entry enables live voice or opens chat without explicit embed configuration" >&2
  exit 2
fi
if grep -Eq '47\.87\.143\.41|console\.log|\[Speedaf Voice\]|LIVE_VOICE_UPSTREAM_TOKEN|token=' "$OUT_DIR/voice-entry.js"; then
  echo "voice-entry contains production-only, credential, or debug markers" >&2
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

if [[ "${CHECK_LIVE_VOICE_WS_UPGRADE:-false}" =~ ^(1|true|yes|on)$ ]]; then
  python3 - "${BASE_URL%/}" <<'PY'
import base64
import hashlib
import hmac
import os
import socket
import ssl
import sys
from urllib.parse import urlsplit

base_url = sys.argv[1]
parsed = urlsplit(base_url)
if parsed.scheme not in {"http", "https"} or not parsed.hostname:
    raise SystemExit(f"unsupported BASE_URL for websocket probe: {base_url}")

host = parsed.hostname
port = parsed.port or (443 if parsed.scheme == "https" else 80)
host_literal = f"[{host}]" if ":" in host else host
host_header = host_literal if parsed.port is None else f"{host_literal}:{port}"
path_prefix = parsed.path.rstrip("/")
ws_path = f"{path_prefix}/webchat/live/ws"
query = os.getenv(
    "LIVE_VOICE_WS_QUERY",
    "lang_code=en&voice=bm_george&speed=1.0",
).lstrip("?")
if query:
    ws_path = f"{ws_path}?{query}"

websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
origin = f"{parsed.scheme}://{host_header}"
request = "\r\n".join(
    [
        f"GET {ws_path} HTTP/1.1",
        f"Host: {host_header}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {websocket_key}",
        "Sec-WebSocket-Version: 13",
        f"Origin: {origin}",
        "",
        "",
    ]
).encode("ascii")

raw_socket = socket.create_connection((host, port), timeout=10)
probe_socket = raw_socket
try:
    if parsed.scheme == "https":
        probe_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
    probe_socket.settimeout(10)
    probe_socket.sendall(request)
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 65536:
        chunk = probe_socket.recv(4096)
        if not chunk:
            break
        response += chunk
finally:
    probe_socket.close()

header_bytes, separator, _ = response.partition(b"\r\n\r\n")
if not separator:
    raise SystemExit("live voice websocket probe returned incomplete HTTP headers")
lines = header_bytes.decode("iso-8859-1").split("\r\n")
status_parts = lines[0].split(" ", 2)
try:
    status_code = int(status_parts[1])
except (IndexError, ValueError) as exc:
    raise SystemExit(f"invalid websocket status line: {lines[0]}") from exc

headers = {}
for line in lines[1:]:
    if ":" not in line:
        continue
    name, value = line.split(":", 1)
    headers[name.strip().lower()] = value.strip()

upgrade_header = headers.get("upgrade", "")
connection_header = headers.get("connection", "")
if status_code != 101:
    raise SystemExit(f"live voice websocket upgrade failed: status={status_code}")
if upgrade_header.lower() != 'websocket':
    raise SystemExit(f"live voice websocket upgrade header invalid: {upgrade_header!r}")
if "upgrade" not in connection_header.lower():
    raise SystemExit(f"live voice websocket connection header invalid: {connection_header!r}")

expected_accept = base64.b64encode(
    hashlib.sha1(
        (websocket_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    ).digest()
).decode("ascii")
actual_accept = headers.get("sec-websocket-accept", "")
if not hmac.compare_digest(actual_accept, expected_accept):
    raise SystemExit("live voice websocket Sec-WebSocket-Accept validation failed")

print("LIVE_VOICE_WS_UPGRADE_PASS=true")
PY
fi

echo "CANDIDATE_SMOKE_PASS=true"
echo "base_url=$BASE_URL"
echo "evidence_dir=$OUT_DIR"
