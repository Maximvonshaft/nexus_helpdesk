#!/usr/bin/env bash
set -Eeuo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
SESSION_PUBLIC_ID="${SESSION_PUBLIC_ID:-}"
WINDOW_MINUTES="${WINDOW_MINUTES:-60}"
EXPECTED_STT_PROVIDER="${EXPECTED_STT_PROVIDER:-}"
EXPECTED_TTS_PROVIDER="${EXPECTED_TTS_PROVIDER:-}"

if [[ -z "$PUBLIC_BASE_URL" || -z "$ADMIN_TOKEN" ]]; then
  echo "Usage: PUBLIC_BASE_URL=https://support.example.com ADMIN_TOKEN=<admin bearer token> [SESSION_PUBLIC_ID=wv_...] bash scripts/probe_webcall_ai_spoken_canary.sh" >&2
  exit 2
fi

PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-/tmp/nexus_webcall_ai_spoken_canary_${TS}}"
mkdir -p "$OUT_DIR"

curl_capture() {
  local url="$1"
  local out="$2"
  local http_code
  set +e
  http_code="$(curl -sS -L "$url" -H "Authorization: Bearer ${ADMIN_TOKEN}" -o "$out" -w '%{http_code}' 2>"${out}.stderr")"
  local rc=$?
  set -e
  printf '%s\n' "$http_code" > "${out}.status"
  printf '%s\n' "$rc" > "${out}.rc"
}

sanitize_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  python3 - "$file" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, "r", encoding="utf-8", errors="replace").read()
text = re.sub(r'("(?:participant_token|visitor_token|access_token|refresh_token|password)"\s*:\s*")([^"]+)(")', r'\1<redacted>\3', text, flags=re.I)
text = re.sub(r'((?:Bearer|Token)\s+)[A-Za-z0-9._~+/=-]{12,}', r'\1<redacted>', text, flags=re.I)
open(path, "w", encoding="utf-8").write(text)
PY
}

cat > "$OUT_DIR/00_env_sanitized.txt" <<EOF_ENV
probe_timestamp_utc=$TS
PUBLIC_BASE_URL=$PUBLIC_BASE_URL
SESSION_PUBLIC_ID_SET=$([[ -n "$SESSION_PUBLIC_ID" ]] && echo true || echo false)
WINDOW_MINUTES=$WINDOW_MINUTES
EXPECTED_STT_PROVIDER=${EXPECTED_STT_PROVIDER:-}
EXPECTED_TTS_PROVIDER=${EXPECTED_TTS_PROVIDER:-}
ADMIN_TOKEN_SET=true
EOF_ENV

curl_capture "$PUBLIC_BASE_URL/api/admin/webcall-ai/health" "$OUT_DIR/01_admin_webcall_ai_health.json"
sanitize_file "$OUT_DIR/01_admin_webcall_ai_health.json"

if [[ -n "$SESSION_PUBLIC_ID" ]]; then
  curl_capture "$PUBLIC_BASE_URL/api/admin/webcall-ai/sessions/${SESSION_PUBLIC_ID}/events" "$OUT_DIR/02_session_events.json"
  sanitize_file "$OUT_DIR/02_session_events.json"
else
  cat > "$OUT_DIR/02_session_events.json" <<'EOF_SKIP'
{"skipped":true,"reason":"SESSION_PUBLIC_ID was not provided"}
EOF_SKIP
fi

python3 - "$OUT_DIR/01_admin_webcall_ai_health.json" "$OUT_DIR/02_session_events.json" "$WINDOW_MINUTES" "$EXPECTED_STT_PROVIDER" "$EXPECTED_TTS_PROVIDER" <<'PY'
import json, sys
from pathlib import Path

health_path = Path(sys.argv[1])
events_path = Path(sys.argv[2])
window_minutes = int(sys.argv[3])
expected_stt_provider = sys.argv[4]
expected_tts_provider = sys.argv[5]
health = json.loads(health_path.read_text(encoding="utf-8"))
events_payload = json.loads(events_path.read_text(encoding="utf-8"))
metrics = health.get("metrics") or {}
events_by_type = metrics.get("events_by_type") or {}
session_event_items = [item for item in (events_payload.get("events") or []) if isinstance(item, dict)]
session_events = [item.get("event_type") for item in session_event_items]
payloads_by_type = {}
for item in session_event_items:
    payloads_by_type.setdefault(item.get("event_type"), []).append(item.get("payload") or {})
spoken = int(metrics.get("spoken_count") or 0)
interrupted = int(metrics.get("barge_in_count") or 0)
publish_failed = int(metrics.get("publish_failed_count") or 0)
session_spoken = "webcall_ai.response.spoken" in session_events
session_interrupted = "webcall_ai.response.interrupted" in session_events
session_publish_failed = "webcall_ai.response.publish_failed" in session_events
stt_provider = health.get("stt_provider")
tts_provider = health.get("tts_provider")
provider_mismatch = bool(expected_stt_provider and stt_provider != expected_stt_provider) or bool(expected_tts_provider and tts_provider != expected_tts_provider)
contracts = payloads_by_type.get("webcall_ai.stt.request_contract") or []
latest_contract = contracts[-1] if contracts else {}
audio_inputs = payloads_by_type.get("webcall_ai.stt.audio_input_stats") or []
latest_audio_input = audio_inputs[-1] if audio_inputs else {}
shadow_results = payloads_by_type.get("webcall_ai.stt.shadow_result") or []
shadow_winners = payloads_by_type.get("webcall_ai.stt.shadow_winner") or []
contract_required = expected_stt_provider == "deepgram_streaming"
contract_ok = bool(latest_contract.get("contract_match"))
ok = (spoken > 0 or session_spoken or interrupted > 0 or session_interrupted) and not session_publish_failed and not provider_mismatch and (not contract_required or contract_ok)
summary = {
    "ok": ok,
    "window_minutes": window_minutes,
    "health_status": health.get("status"),
    "smoke_status": health.get("smoke_status"),
    "stt_provider": stt_provider,
    "tts_provider": tts_provider,
    "expected_stt_provider": expected_stt_provider or None,
    "expected_tts_provider": expected_tts_provider or None,
    "provider_mismatch": provider_mismatch,
    "spoken_count": spoken,
    "barge_in_count": interrupted,
    "publish_failed_count": publish_failed,
    "events_by_type": events_by_type,
    "session_spoken": session_spoken,
    "session_interrupted": session_interrupted,
    "session_publish_failed": session_publish_failed,
    "stt_request_contract_seen": bool(contracts),
    "stt_contract_match": latest_contract.get("contract_match"),
    "request_encoding": latest_contract.get("request_encoding"),
    "request_sample_rate": latest_contract.get("request_sample_rate"),
    "request_channels": latest_contract.get("request_channels"),
    "request_model": latest_contract.get("request_model"),
    "input_audio_ms": latest_contract.get("input_audio_ms") or latest_audio_input.get("audio_ms"),
    "low_input_level": latest_contract.get("low_input_level"),
    "shadow_result_count": len(shadow_results),
    "shadow_winner": (shadow_winners[-1].get("shadow_candidate") if shadow_winners else None),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(0 if ok and publish_failed == 0 and not provider_mismatch else 1)
PY

echo "Artifacts: $OUT_DIR"
