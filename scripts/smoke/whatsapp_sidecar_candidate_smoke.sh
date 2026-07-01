#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${WA_SIDECAR_BASE_URL:-http://127.0.0.1:${CANDIDATE_WA_SIDECAR_PORT:-18795}}"
DEFAULT_ACCOUNT_LIST="${WA_SIDECAR_AUTO_START_ACCOUNTS:-}"
DEFAULT_ACCOUNT_ID="${DEFAULT_ACCOUNT_LIST%%,*}"
ACCOUNT_ID="${WA_ACCOUNT_ID:-${DEFAULT_ACCOUNT_ID:-wa-main}}"
OUT_DIR="${OUT_DIR:-$(mktemp -d -t nexus-wa-sidecar-smoke.XXXXXX)}"
TOKEN="${WA_SIDECAR_INTERNAL_TOKEN:-${WHATSAPP_SIDECAR_TOKEN:-}}"
START_LOGIN="${WA_SIDECAR_SMOKE_START_LOGIN:-false}"
EXPECT_MODE="${WA_SIDECAR_EXPECT_MODE:-}"
EXPECT_ACCOUNT_STATUS="${WA_SIDECAR_EXPECT_ACCOUNT_STATUS:-}"
EXPECT_QR_OR_CONNECTED="${WA_SIDECAR_EXPECT_QR_OR_CONNECTED:-false}"
WAIT_SECONDS="${WA_SIDECAR_WAIT_SECONDS:-90}"
POLL_INTERVAL_SECONDS="${WA_SIDECAR_POLL_INTERVAL_SECONDS:-3}"
CHECK_SEND="${WA_SIDECAR_SMOKE_SEND:-false}"
ALLOW_LIVE_SEND="${WA_SIDECAR_ALLOW_LIVE_SEND:-false}"
SEND_TARGET="${WA_SIDECAR_SMOKE_SEND_TARGET:-}"
SEND_BODY="${WA_SIDECAR_SMOKE_SEND_BODY:-NexusDesk candidate WhatsApp smoke}"

mkdir -p "$OUT_DIR"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL missing command: $1" >&2
    exit 2
  }
}

is_true() {
  [[ "${1:-}" =~ ^(1|true|yes|on)$ ]]
}

curl_json() {
  local method="$1"
  local path="$2"
  local out="$3"
  shift 3
  curl -fsS --retry 20 --retry-delay 1 --retry-connrefused --max-time 10 \
    -X "$method" \
    -H 'Accept: application/json' \
    "$@" \
    "${BASE_URL%/}${path}" \
    -o "$out"
}

auth_args() {
  if [[ -z "$TOKEN" ]]; then
    echo "FAIL missing WA_SIDECAR_INTERNAL_TOKEN or WHATSAPP_SIDECAR_TOKEN for account checks" >&2
    exit 2
  fi
  printf '%s\0%s\0' -H "Authorization: Bearer ${TOKEN}"
}

json_value() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cur = data
for part in sys.argv[2].split("."):
    if not part:
        continue
    cur = cur[int(part)] if isinstance(cur, list) else cur.get(part)
    if cur is None:
        break
print("" if cur is None else cur)
PY
}

assert_qr_or_connected() {
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
status = payload.get("status")
qr_status = payload.get("qr_status")
has_qr = bool(payload.get("qr") or payload.get("qr_data_url"))
if status == "connected":
    print("connected")
    raise SystemExit(0)
if status == "qr_pending" and qr_status == "pending" and has_qr:
    print("qr_pending")
    raise SystemExit(0)
raise SystemExit(
    "expected qr_pending with QR data or connected, "
    f"got status={status} qr_status={qr_status} has_qr={has_qr} "
    f"session_state={payload.get('session_state')} error={payload.get('last_error_code')}"
)
PY
}

assert_account_metadata() {
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
session_state = payload.get("session_state")
browser = payload.get("browser")
valid_session_states = {"empty", "partial", "linked", "corrupt"}
errors = []
if session_state not in valid_session_states:
    errors.append(f"session_state={session_state!r}")
if not isinstance(browser, list) or len(browser) != 3 or not all(isinstance(item, str) and item for item in browser):
    errors.append(f"browser={browser!r}")
if errors:
    raise SystemExit("missing hardened account metadata: " + ", ".join(errors))
PY
}

need_cmd curl
need_cmd python3

curl_json GET /healthz "$OUT_DIR/healthz.json"
curl_json GET /readyz "$OUT_DIR/readyz.json"

python3 - "$OUT_DIR/healthz.json" "$OUT_DIR/readyz.json" "$EXPECT_MODE" <<'PY'
import json
import sys
from pathlib import Path

health = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ready = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expect_mode = sys.argv[3]
errors = []
if health.get("status") != "ok":
    errors.append(f"healthz_status={health.get('status')}")
if ready.get("status") != "ready":
    errors.append(f"readyz_status={ready.get('status')}")
if expect_mode and ready.get("mode") != expect_mode:
    errors.append(f"readyz_mode={ready.get('mode')} expected={expect_mode}")
if errors:
    raise SystemExit("\n".join(errors))
PY

AUTH=()
while IFS= read -r -d '' item; do
  AUTH+=("$item")
done < <(auth_args)

curl_json GET "/accounts/${ACCOUNT_ID}/status" "$OUT_DIR/status.json" "${AUTH[@]}"
assert_account_metadata "$OUT_DIR/status.json"

if [[ -n "$EXPECT_ACCOUNT_STATUS" ]]; then
  actual_status="$(json_value "$OUT_DIR/status.json" status)"
  if [[ "$actual_status" != "$EXPECT_ACCOUNT_STATUS" ]]; then
    echo "FAIL account_status=$actual_status expected=$EXPECT_ACCOUNT_STATUS" >&2
    exit 1
  fi
fi

if is_true "$START_LOGIN"; then
  curl_json POST "/accounts/${ACCOUNT_ID}/start" "$OUT_DIR/start.json" "${AUTH[@]}"
  assert_account_metadata "$OUT_DIR/start.json"
  curl_json GET "/accounts/${ACCOUNT_ID}/qr" "$OUT_DIR/qr.json" "${AUTH[@]}"
  assert_account_metadata "$OUT_DIR/qr.json"
fi

if is_true "$EXPECT_QR_OR_CONNECTED"; then
  deadline=$((SECONDS + WAIT_SECONDS))
  last_error=""
  while (( SECONDS <= deadline )); do
    curl_json GET "/accounts/${ACCOUNT_ID}/status" "$OUT_DIR/status.json" "${AUTH[@]}"
    assert_account_metadata "$OUT_DIR/status.json"
    curl_json GET "/accounts/${ACCOUNT_ID}/qr" "$OUT_DIR/qr.json" "${AUTH[@]}"
    assert_account_metadata "$OUT_DIR/qr.json"
    if state="$(assert_qr_or_connected "$OUT_DIR/qr.json" 2>"$OUT_DIR/qr-wait-error.txt")"; then
      echo "WA_SIDECAR_QR_STATE=$state"
      break
    fi
    last_error="$(cat "$OUT_DIR/qr-wait-error.txt" 2>/dev/null || true)"
    sleep "$POLL_INTERVAL_SECONDS"
  done
  if [[ -z "${state:-}" ]]; then
    if [[ -n "$last_error" ]]; then
      echo "$last_error" >&2
    fi
    exit 1
  fi
fi

if is_true "$CHECK_SEND"; then
  ready_mode="$(json_value "$OUT_DIR/readyz.json" mode)"
  if [[ "$ready_mode" != "mock" ]] && ! is_true "$ALLOW_LIVE_SEND"; then
    echo "FAIL live WhatsApp send requires WA_SIDECAR_ALLOW_LIVE_SEND=true" >&2
    exit 2
  fi
  if [[ -z "$SEND_TARGET" ]]; then
    echo "FAIL WA_SIDECAR_SMOKE_SEND_TARGET is required for send smoke" >&2
    exit 2
  fi
  status_now="$(json_value "$OUT_DIR/status.json" status)"
  if [[ "$status_now" != "connected" ]]; then
    curl_json GET "/accounts/${ACCOUNT_ID}/status" "$OUT_DIR/status-before-send.json" "${AUTH[@]}"
    status_now="$(json_value "$OUT_DIR/status-before-send.json" status)"
  fi
  if [[ "$status_now" != "connected" ]]; then
    echo "FAIL send smoke requires connected account, got status=$status_now" >&2
    exit 1
  fi
  payload="$OUT_DIR/send-payload.json"
  python3 - "$payload" "$SEND_TARGET" "$SEND_BODY" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "idempotency_key": "nexusdesk-candidate-smoke",
    "target": sys.argv[2],
    "body": sys.argv[3],
}, ensure_ascii=False), encoding="utf-8")
PY
  curl_json POST "/accounts/${ACCOUNT_ID}/send" "$OUT_DIR/send.json" \
    "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    --data-binary "@$payload"
  python3 - "$OUT_DIR/send.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("ok") is not True or payload.get("status") != "sent":
    raise SystemExit(f"send smoke failed: status={payload.get('status')} error={payload.get('error_code')}")
PY
fi

echo "WHATSAPP_SIDECAR_CANDIDATE_SMOKE_PASS=true"
echo "base_url=$BASE_URL"
echo "account_id=$ACCOUNT_ID"
echo "evidence_dir=$OUT_DIR"
