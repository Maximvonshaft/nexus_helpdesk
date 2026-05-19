#!/usr/bin/env bash
set -Eeuo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
VOICE_WSS_URL="${VOICE_WSS_URL:-}"
RUN_MUTATING_PROBE="${RUN_MUTATING_PROBE:-0}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

if [[ -z "$PUBLIC_BASE_URL" || -z "$VOICE_WSS_URL" ]]; then
  echo "Usage: PUBLIC_BASE_URL=https://support.example.com VOICE_WSS_URL=wss://voice.example.com bash scripts/probe_webcall_runtime.sh" >&2
  exit 2
fi

PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
VOICE_WSS_URL="${VOICE_WSS_URL%/}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="/tmp/nexus_webcall_probe_${TS}"
mkdir -p "$OUT_DIR"

SECRET_KEY_PATTERNS='(LIVEKIT_API_SECRET|LIVEKIT_API_KEY|API_SECRET|API_KEY)'
TOKEN_VALUE_PATTERN='"(participant_token|visitor_token|access_token|refresh_token|password)"[[:space:]]*:[[:space:]]*"(?!<redacted>)[^"]+"'

curl_capture() {
  local url="$1"
  local out="$2"
  local method="${3:-GET}"
  if [[ $# -ge 3 ]]; then
    shift 3
  else
    shift 2
  fi
  local http_code
  set +e
  http_code="$(curl -sS -L -X "$method" "$url" "$@" -o "$out" -w '%{http_code}' 2>"${out}.stderr")"
  local rc=$?
  set -e
  printf '%s\n' "$http_code" > "${out}.status"
  printf '%s\n' "$rc" > "${out}.rc"
  return 0
}

headers_capture() {
  local url="$1"
  local out="$2"
  set +e
  curl -sS -L -I "$url" -o "$out" 2>"${out}.stderr"
  local rc=$?
  set -e
  printf '%s\n' "$rc" > "${out}.rc"
}

sanitize_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  python3 - "$file" <<'PY'
import re, sys
p = sys.argv[1]
text = open(p, 'r', encoding='utf-8', errors='replace').read()
patterns = [
    r'("(?:participant_token|visitor_token|access_token|refresh_token|password)"\s*:\s*")([^"]+)(")',
    r'("(?:LIVEKIT_API_SECRET|LIVEKIT_API_KEY|API_SECRET|API_KEY)"\s*:\s*")([^"]+)(")',
]
for pattern in patterns:
    text = re.sub(pattern, r'\1<redacted>\3', text, flags=re.I)
open(p, 'w', encoding='utf-8').write(text)
PY
}

contains_forbidden_secret_text() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  if grep -Eiq "$SECRET_KEY_PATTERNS" "$file"; then return 0; fi
  python3 - "$file" <<'PY'
import re, sys
text = open(sys.argv[1], 'r', encoding='utf-8', errors='replace').read()
pattern = re.compile(r'"(?:participant_token|visitor_token|access_token|refresh_token|password)"\s*:\s*"(?!<redacted>)[^"]+"', re.I)
sys.exit(0 if pattern.search(text) else 1)
PY
}

cat > "$OUT_DIR/00_env_sanitized.txt" <<EOF_ENV
probe_timestamp_utc=$TS
PUBLIC_BASE_URL=$PUBLIC_BASE_URL
VOICE_WSS_URL=$VOICE_WSS_URL
RUN_MUTATING_PROBE=$RUN_MUTATING_PROBE
ADMIN_EMAIL_SET=$([[ -n "$ADMIN_EMAIL" ]] && echo true || echo false)
ADMIN_PASSWORD_SET=$([[ -n "$ADMIN_PASSWORD" ]] && echo true || echo false)
EOF_ENV

curl_capture "$PUBLIC_BASE_URL/healthz" "$OUT_DIR/01_healthz.json"
curl_capture "$PUBLIC_BASE_URL/readyz" "$OUT_DIR/02_readyz.json"
curl_capture "$PUBLIC_BASE_URL/api/webchat/voice/runtime-config" "$OUT_DIR/03_runtime_config.json"
headers_capture "$PUBLIC_BASE_URL/" "$OUT_DIR/04_headers_root.txt"
headers_capture "$PUBLIC_BASE_URL/webcall/probe-route" "$OUT_DIR/05_headers_webcall.txt"

{
  echo "# Static asset checks"
  for path in "/webchat/voice-entry.js" "/webcall/probe-route" "/assets/"; do
    out="$OUT_DIR/static_${path//\//_}.txt"
    curl_capture "$PUBLIC_BASE_URL$path" "$out"
    echo "$path status=$(cat "${out}.status" 2>/dev/null || echo unknown) rc=$(cat "${out}.rc" 2>/dev/null || echo unknown)"
  done
} > "$OUT_DIR/06_static_assets.txt"

cat > "$OUT_DIR/07_api_create_voice_session_result.json" <<'EOF_SKIP'
{"skipped":true,"reason":"RUN_MUTATING_PROBE is not 1. This probe is read-only by default."}
EOF_SKIP

if [[ "$RUN_MUTATING_PROBE" == "1" ]]; then
  INIT_BODY='{"tenant_key":"webcall_probe","channel_key":"staging_probe","visitor_name":"WebCall Probe Visitor","page_url":"'$PUBLIC_BASE_URL'/webcall-probe"}'
  curl_capture "$PUBLIC_BASE_URL/api/webchat/init" "$OUT_DIR/07a_init.raw.json" POST -H 'Content-Type: application/json' --data "$INIT_BODY"
  CONVERSATION_ID="$(python3 - "$OUT_DIR/07a_init.raw.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding='utf-8')).get('conversation_id',''))
except Exception:
    print('')
PY
)"
  VISITOR_TOKEN="$(python3 - "$OUT_DIR/07a_init.raw.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding='utf-8')).get('visitor_token',''))
except Exception:
    print('')
PY
)"
  cp "$OUT_DIR/07a_init.raw.json" "$OUT_DIR/07a_init.json"
  sanitize_file "$OUT_DIR/07a_init.json"
  rm -f "$OUT_DIR/07a_init.raw.json"
  if [[ -n "$CONVERSATION_ID" && -n "$VISITOR_TOKEN" ]]; then
    curl_capture "$PUBLIC_BASE_URL/api/webchat/conversations/$CONVERSATION_ID/voice/sessions" "$OUT_DIR/07_api_create_voice_session_result.json" POST -H 'Content-Type: application/json' -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" --data '{"locale":"en","recording_consent":false}'
    sanitize_file "$OUT_DIR/07_api_create_voice_session_result.json"
  else
    cat > "$OUT_DIR/07_api_create_voice_session_result.json" <<'EOF_CREATE_FAIL'
{"ok":false,"error":"failed_to_initialize_probe_conversation"}
EOF_CREATE_FAIL
  fi
fi

cat > "$OUT_DIR/08_admin_voice_sessions_result.json" <<'EOF_ADMIN_SKIP'
{"skipped":true,"reason":"ADMIN_EMAIL/ADMIN_PASSWORD authenticated admin probe is optional and was not executed by this read-only script."}
EOF_ADMIN_SKIP

python3 - "$OUT_DIR/03_runtime_config.json" > "$OUT_DIR/03_runtime_config_summary.txt" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception as exc:
    print(f'enabled=parse_error:{type(exc).__name__}')
    print('provider=parse_error')
    print('livekit_url=parse_error')
else:
    print(f"enabled={data.get('enabled')}")
    print(f"provider={data.get('provider')}")
    print(f"livekit_url={data.get('livekit_url')}")
PY
runtime_enabled="$(grep '^enabled=' "$OUT_DIR/03_runtime_config_summary.txt" | cut -d= -f2- || true)"
runtime_provider="$(grep '^provider=' "$OUT_DIR/03_runtime_config_summary.txt" | cut -d= -f2- || true)"
runtime_livekit_url="$(grep '^livekit_url=' "$OUT_DIR/03_runtime_config_summary.txt" | cut -d= -f2- || true)"

root_microphone_denied=false
webcall_microphone_allowed=false
webcall_csp_has_voice=false
if grep -Eiq 'Permissions-Policy:.*microphone=\(\)' "$OUT_DIR/04_headers_root.txt"; then root_microphone_denied=true; fi
if grep -Eiq 'Permissions-Policy:.*microphone=\(self\)' "$OUT_DIR/05_headers_webcall.txt"; then webcall_microphone_allowed=true; fi
if grep -Fq "$VOICE_WSS_URL" "$OUT_DIR/05_headers_webcall.txt"; then webcall_csp_has_voice=true; fi

secret_leak=false
for file in "$OUT_DIR"/*; do
  if contains_forbidden_secret_text "$file"; then
    secret_leak=true
  fi
done

cat > "$OUT_DIR/FINAL_WEB_CALL_PROBE_REPORT.md" <<EOF_REPORT
# NexusDesk WebCall Runtime Probe

- Timestamp UTC: $TS
- Public base URL: $PUBLIC_BASE_URL
- Voice WSS URL: $VOICE_WSS_URL
- Output directory: $OUT_DIR

## HTTP status summary

| Check | Status | Curl RC |
|---|---:|---:|
| /healthz | $(cat "$OUT_DIR/01_healthz.json.status" 2>/dev/null || echo unknown) | $(cat "$OUT_DIR/01_healthz.json.rc" 2>/dev/null || echo unknown) |
| /readyz | $(cat "$OUT_DIR/02_readyz.json.status" 2>/dev/null || echo unknown) | $(cat "$OUT_DIR/02_readyz.json.rc" 2>/dev/null || echo unknown) |
| /api/webchat/voice/runtime-config | $(cat "$OUT_DIR/03_runtime_config.json.status" 2>/dev/null || echo unknown) | $(cat "$OUT_DIR/03_runtime_config.json.rc" 2>/dev/null || echo unknown) |

## Runtime config

- enabled: $runtime_enabled
- provider: $runtime_provider
- livekit_url: $runtime_livekit_url

## Header gates

- Root path microphone denied: $root_microphone_denied
- /webcall microphone allowed: $webcall_microphone_allowed
- /webcall CSP contains voice WSS: $webcall_csp_has_voice

## Secret hygiene

- Forbidden unredacted secret/token values detected in captured artifacts: $secret_leak

## Mutating probe

- RUN_MUTATING_PROBE: $RUN_MUTATING_PROBE
- Create voice session artifact: 07_api_create_voice_session_result.json

## Verdict

Manual review required before production. Staging proof passes only if:

1. healthz/readyz are 200.
2. runtime config is enabled=true and provider=livekit.
3. runtime livekit_url equals the expected WSS endpoint.
4. root path keeps microphone denied.
5. /webcall allows microphone=(self).
6. /webcall CSP includes the voice WSS URL.
7. no unredacted secrets/tokens are present in probe artifacts.
8. manual browser test confirms visitor and agent join the same room and two-way audio works.
EOF_REPORT

echo "WebCall runtime probe complete: $OUT_DIR"
echo "$OUT_DIR/FINAL_WEB_CALL_PROBE_REPORT.md"
