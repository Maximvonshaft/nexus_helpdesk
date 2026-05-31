#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
umask 077

PROJECT_DIR="${PROJECT_DIR:-/opt/nexus_helpdesk}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/deploy/docker-compose.server.yml}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env.prod}"
SUPPORT_BASE="${SUPPORT_BASE:-https://www.leakle.com}"
VOICE_HOST="${VOICE_HOST:-voice.leakle.com}"
EXPECTED_LIVEKIT_URL="${EXPECTED_LIVEKIT_URL:-wss://voice.leakle.com}"
APP_BASE="${APP_BASE:-http://127.0.0.1:18081}"
RUN_SYNTHETIC_E2E="${RUN_SYNTHETIC_E2E:-0}"
ADMIN_USER_ID="${ADMIN_USER_ID:-2}"

TS="$(date +%Y%m%d_%H%M%S)"
OUT="${OUT:-/tmp/nexus_webcall_livekit_custom_domain_probe_${TS}}"
mkdir -p "$OUT"/{health,tls,wss,env,provider,e2e,logs,final,sensitive}
chmod 700 "$OUT" "$OUT/sensitive"
exec > >(tee -a "$OUT/probe.log") 2>&1

section() { echo; echo "===== $* ====="; }
redact() {
  sed -E \
    -e 's#(LIVEKIT_API_KEY=).*#\1<REDACTED>#' \
    -e 's#(LIVEKIT_API_SECRET=).*#\1<REDACTED>#' \
    -e 's#("participant_token"[[:space:]]*:[[:space:]]*")[^"]+#\1<REDACTED>#g' \
    -e 's#("visitor_token"[[:space:]]*:[[:space:]]*")[^"]+#\1<REDACTED>#g' \
    -e 's#(Bearer )[A-Za-z0-9._~+/=-]+#\1<REDACTED>#g'
}
add_reason() { echo "- $1: $2" >> "$OUT/final/reasons.tmp"; }
: > "$OUT/final/reasons.tmp"

cd "$PROJECT_DIR" 2>/dev/null || true

if [ -f "$COMPOSE_FILE" ] && [ -f "$ENV_FILE" ]; then
  DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
else
  DC=(docker compose)
fi

http_code() {
  curl -sS -m 12 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000
}

section "0. INPUT"
echo "OUT=$OUT"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "SUPPORT_BASE=$SUPPORT_BASE"
echo "VOICE_HOST=$VOICE_HOST"
echo "EXPECTED_LIVEKIT_URL=$EXPECTED_LIVEKIT_URL"
echo "RUN_SYNTHETIC_E2E=$RUN_SYNTHETIC_E2E"

section "1. HEALTH"
APP_READY="$(http_code "$APP_BASE/readyz")"
PUBLIC_READY="$(http_code "$SUPPORT_BASE/readyz")"
echo "APP_READY=$APP_READY" | tee "$OUT/health/summary.txt"
echo "PUBLIC_READY=$PUBLIC_READY" | tee -a "$OUT/health/summary.txt"
[ "$APP_READY" = "200" ] && add_reason OK "local app readyz is 200" || add_reason NO_GO "local app readyz is $APP_READY"
[ "$PUBLIC_READY" = "200" ] && add_reason OK "public readyz is 200" || add_reason WARN "public readyz is $PUBLIC_READY"

section "2. RUNTIME CONFIG"
curl -sS -m 12 "$APP_BASE/api/webchat/voice/runtime-config" > "$OUT/health/runtime_config_local.json" || true
curl -sS -m 12 "$SUPPORT_BASE/api/webchat/voice/runtime-config" > "$OUT/health/runtime_config_public.json" || true
cat "$OUT/health/runtime_config_local.json"; echo
cat "$OUT/health/runtime_config_public.json"; echo

python3 - "$OUT" "$EXPECTED_LIVEKIT_URL" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
expected = sys.argv[2]
for name in ["local", "public"]:
    p = out / "health" / f"runtime_config_{name}.json"
    try:
        d = json.loads(p.read_text())
    except Exception as exc:
        (out / "final" / "reasons.tmp").open("a").write(f"- NO_GO: runtime-config {name} invalid: {exc}\n")
        continue
    if d.get("enabled") is True and d.get("provider") == "livekit" and d.get("livekit_url") == expected:
        (out / "final" / "reasons.tmp").open("a").write(f"- OK: runtime-config {name} is enabled livekit at {expected}\n")
    else:
        (out / "final" / "reasons.tmp").open("a").write(f"- NO_GO: runtime-config {name} unexpected: {d}\n")
    text = p.read_text()
    if "API_SECRET" in text or "API_KEY" in text:
        (out / "final" / "reasons.tmp").open("a").write(f"- NO_GO: runtime-config {name} appears to expose secret names\n")
PY

section "3. TLS AND WSS SMOKE"
openssl s_client -connect "$VOICE_HOST:443" -servername "$VOICE_HOST" </dev/null > "$OUT/tls/voice_s_client.raw" 2>&1 || true
grep -E 'subject=|issuer=|notBefore=|notAfter=|Verify return code' "$OUT/tls/voice_s_client.raw" | tee "$OUT/tls/summary.txt" || true
if grep -q 'Verify return code: 0' "$OUT/tls/voice_s_client.raw"; then
  add_reason OK "TLS verification is OK for $VOICE_HOST"
else
  add_reason NO_GO "TLS verification failed for $VOICE_HOST"
fi

python3 - "$VOICE_HOST" > "$OUT/wss/raw_handshake.txt" 2>&1 <<'PY'
import base64, os, socket, ssl, sys
host = sys.argv[1]
for path in ["/", "/rtc", "/rtc?access_token=probe"]:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: https://{host}\r\n"
        "\r\n"
    )
    print(f"===== {path} =====")
    try:
        raw = socket.create_connection((host, 443), timeout=8)
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=host)
        sock.sendall(req.encode())
        resp = sock.recv(4096).decode(errors="ignore")
        sock.close()
        print(resp.split("\r\n\r\n", 1)[0])
    except Exception as exc:
        print(type(exc).__name__, exc)
PY
cat "$OUT/wss/raw_handshake.txt"

section "4. SERVER RUNTIME ENV REDACTED"
if docker ps --format '{{.Names}}' | grep -qx 'deploy-app-1'; then
  docker exec deploy-app-1 sh -lc 'env | grep -E "^(WEBCHAT_VOICE_|LIVEKIT_)=" | sort' | redact | tee "$OUT/env/runtime_env_redacted.txt" || true
fi

section "5. PROVIDER CONFIG PROBE"
"${DC[@]}" run --rm --no-deps -T app python - <<'PY' > "$OUT/provider/provider_probe.json" 2>"$OUT/provider/provider_probe.stderr" || true
import json
from app.webchat_voice_config import load_webchat_voice_runtime_config
from app.services.livekit_voice_provider import LiveKitVoiceProvider
out = {"ok": False, "error": None, "room_status_probe": None, "config": {}}
try:
    c = load_webchat_voice_runtime_config()
    out["config"] = {
        "enabled": c.enabled,
        "provider": c.provider,
        "livekit_url": c.livekit_url,
        "api_key_set": bool(c.livekit_api_key),
        "api_secret_set": bool(c.livekit_api_secret),
        "connect_src": list(c.connect_src),
    }
    provider = LiveKitVoiceProvider.from_config(c)
    try:
        out["room_status_probe"] = provider.get_room_status(room_name="nexus_probe_nonexistent_room")
    except Exception as exc:
        out["room_status_probe"] = f"lookup_error:{type(exc).__name__}:{exc}"
    out["ok"] = True
except Exception as exc:
    out["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
cat "$OUT/provider/provider_probe.json"
python3 - "$OUT/provider/provider_probe.json" "$OUT/final/reasons.tmp" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    open(sys.argv[2], "a").write(f"- NO_GO: provider probe json invalid: {exc}\n")
    raise SystemExit(0)
if d.get("ok") and d.get("config", {}).get("provider") == "livekit" and d.get("config", {}).get("api_key_set") and d.get("config", {}).get("api_secret_set"):
    open(sys.argv[2], "a").write("- OK: provider config loads with livekit credentials present\n")
else:
    open(sys.argv[2], "a").write(f"- NO_GO: provider config probe failed: {d}\n")
PY

section "6. OPTIONAL SYNTHETIC E2E"
if [ "$RUN_SYNTHETIC_E2E" = "1" ]; then
  ADMIN_TOKEN="$("${DC[@]}" run --rm --no-deps -T app python - <<PY 2>/dev/null | tail -n1
from app.auth_service import create_access_token
print(create_access_token(int("$ADMIN_USER_ID")))
PY
)"
  printf '%s\n' "$ADMIN_TOKEN" > "$OUT/sensitive/admin_token.txt"

  curl -sS -m 30 "$APP_BASE/api/webchat/init" \
    -H 'Content-Type: application/json' \
    -d "{\"tenant_key\":\"probe\",\"channel_key\":\"website\",\"visitor_name\":\"WebCall Custom Domain Probe $TS\",\"origin\":\"$SUPPORT_BASE\",\"page_url\":\"$SUPPORT_BASE/webchat/demo/?probe=$TS\"}" \
    > "$OUT/sensitive/init.json"
  CONV_ID="$(python3 -c 'import json;print(json.load(open("'"$OUT"'/sensitive/init.json"))["conversation_id"])')"
  VISITOR_TOKEN="$(python3 -c 'import json;print(json.load(open("'"$OUT"'/sensitive/init.json"))["visitor_token"])')"

  curl -sS -m 45 "$APP_BASE/api/webchat/conversations/$CONV_ID/voice/sessions" \
    -H 'Content-Type: application/json' \
    -H "X-Webchat-Visitor-Token: $VISITOR_TOKEN" \
    -d '{"locale":"en","recording_consent":false}' \
    > "$OUT/sensitive/create_voice.json"
  python3 - "$OUT/sensitive/create_voice.json" "$OUT/e2e/create_voice.redacted.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {"raw": open(sys.argv[1], errors="ignore").read()[:2000]}
if d.get("participant_token"):
    d["participant_token"] = "<REDACTED>"
json.dump(d, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
PY
  cat "$OUT/e2e/create_voice.redacted.json"

  VOICE_ID="$(python3 -c 'import json;d=json.load(open("'"$OUT"'/sensitive/create_voice.json"));print(d.get("voice_session_id") or "")')"
  PROVIDER="$(python3 -c 'import json;d=json.load(open("'"$OUT"'/sensitive/create_voice.json"));print(d.get("provider") or "")')"
  if [ -z "$VOICE_ID" ] || [ "$PROVIDER" != "livekit" ]; then
    add_reason NO_GO "synthetic create voice did not return livekit voice_session_id"
  else
    TICKET_ID="$("${DC[@]}" run --rm --no-deps -T app python - <<PY 2>/dev/null | tail -n1
from app.db import SessionLocal
from app.webchat_models import WebchatConversation
with SessionLocal() as db:
    c = db.query(WebchatConversation).filter(WebchatConversation.public_id == "$CONV_ID").first()
    print(c.ticket_id)
PY
)"
    curl -sS -m 45 "$APP_BASE/api/webchat/admin/tickets/$TICKET_ID/voice/$VOICE_ID/accept" \
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \
      > "$OUT/sensitive/accept_voice.json"
    curl -sS -m 45 "$APP_BASE/api/webchat/admin/tickets/$TICKET_ID/voice/$VOICE_ID/end" \
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \
      > "$OUT/e2e/end_voice.json"
    "${DC[@]}" run --rm --no-deps -T app python - <<PY > "$OUT/e2e/evidence_verify.json" 2>"$OUT/e2e/evidence_verify.stderr"
import json
from app.db import SessionLocal
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatMessage, WebchatEvent
with SessionLocal() as db:
    s = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == "$VOICE_ID").first()
    messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == int("$TICKET_ID"), WebchatMessage.message_type == "voice_call").all()
    events = db.query(WebchatEvent).filter(WebchatEvent.ticket_id == int("$TICKET_ID"), WebchatEvent.event_type.like("voice.%")).order_by(WebchatEvent.id.asc()).all()
    print(json.dumps({
        "ticket_id": int("$TICKET_ID"),
        "voice_session_id": "$VOICE_ID",
        "provider": s.provider if s else None,
        "status": s.status if s else None,
        "voice_call_message_count": len(messages),
        "voice_events": [e.event_type for e in events],
    }, ensure_ascii=False, indent=2))
PY
    cat "$OUT/e2e/evidence_verify.json"
    python3 - "$OUT/e2e/evidence_verify.json" "$OUT/final/reasons.tmp" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
required = ["voice.session.created", "voice.session.ringing", "voice.session.accepted", "voice.session.active", "voice.session.ended"]
if d.get("provider") == "livekit" and d.get("status") == "ended" and d.get("voice_call_message_count") == 1 and all(x in d.get("voice_events", []) for x in required):
    open(sys.argv[2], "a").write("- OK: synthetic E2E create -> accept -> end -> evidence closure passed\n")
else:
    open(sys.argv[2], "a").write(f"- NO_GO: synthetic E2E evidence closure failed: {d}\n")
PY
  fi
else
  add_reason WARN "synthetic E2E skipped; set RUN_SYNTHETIC_E2E=1 to verify create/accept/end/evidence"
fi

section "7. FINAL REPORT"
NO_GO_COUNT="$(grep -c '^- NO_GO:' "$OUT/final/reasons.tmp" || true)"
WARN_COUNT="$(grep -c '^- WARN:' "$OUT/final/reasons.tmp" || true)"
if [ "$NO_GO_COUNT" -gt 0 ]; then
  VERDICT="WEBCALL_LIVEKIT_CUSTOM_DOMAIN_NO_GO"
elif [ "$WARN_COUNT" -gt 0 ]; then
  VERDICT="WEBCALL_LIVEKIT_CUSTOM_DOMAIN_GO_WITH_WARNINGS"
else
  VERDICT="WEBCALL_LIVEKIT_CUSTOM_DOMAIN_OK"
fi
{
  echo "# WebCall LiveKit custom-domain probe report"
  echo
  echo "- Generated: $(date -Is)"
  echo "- Support base: $SUPPORT_BASE"
  echo "- Voice host: $VOICE_HOST"
  echo "- Expected LiveKit URL: $EXPECTED_LIVEKIT_URL"
  echo "- Output: $OUT"
  echo
  echo "## Verdict"
  echo
  echo "$VERDICT"
  echo
  echo "## Findings"
  echo
  cat "$OUT/final/reasons.tmp"
  echo
  echo "## Artifacts"
  echo
  echo "- Runtime config local: $OUT/health/runtime_config_local.json"
  echo "- Runtime config public: $OUT/health/runtime_config_public.json"
  echo "- TLS summary: $OUT/tls/summary.txt"
  echo "- WSS handshake: $OUT/wss/raw_handshake.txt"
  echo "- Provider probe: $OUT/provider/provider_probe.json"
  echo "- E2E output: $OUT/e2e/"
} > "$OUT/final/FINAL_WEBCALL_LIVEKIT_CUSTOM_DOMAIN_PROBE_REPORT.md"
cat "$OUT/final/FINAL_WEBCALL_LIVEKIT_CUSTOM_DOMAIN_PROBE_REPORT.md"
tar -C "$(dirname "$OUT")" -czf "${OUT}.tar.gz" "$(basename "$OUT")" 2>/dev/null || true

echo
echo "===== COMPLETE ====="
echo "OUT=$OUT"
echo "ARCHIVE=${OUT}.tar.gz"
