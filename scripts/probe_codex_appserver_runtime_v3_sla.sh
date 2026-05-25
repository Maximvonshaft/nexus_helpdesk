#!/usr/bin/env bash
set -Eeuo pipefail

BRIDGE_URL="${CODEX_APP_SERVER_BRIDGE_URL:-http://127.0.0.1:18794/reply}"
TOKEN_FILE="${CODEX_APP_SERVER_TOKEN_FILE:-/run/nexus/codex_app_server_bridge_token}"
ORIGIN="${CODEX_APP_SERVER_PROBE_ORIGIN:-https://nexusdesk.local}"
OUT_DIR="${OUT_DIR:-/tmp/nexus_codex_runtime_v3_sla_$(date -u '+%Y%m%dT%H%M%SZ')}"
SEQUENTIAL="${CODEX_APPSERVER_SLA_SEQUENTIAL:-30}"
PARALLEL="${CODEX_APPSERVER_SLA_PARALLEL:-20}"
mkdir -p "$OUT_DIR"

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "SLA_STATUS=SKIPPED token_file_unreadable"
  exit 1
fi
BRIDGE_TOKEN="$(sed -e 's/^Bearer[[:space:]]*//I' "$TOKEN_FILE" | head -n1)"

payload() {
  local token="$1"
  cat <<JSON
{"login":{"type":"chatgptAuthTokens","accessToken":"$token","chatgptAccountId":"sla-account","chatgptPlanType":"plus"},"body":"Hello, please reply with strict JSON.","messages":[],"contract":"speedaf_webchat_fast_reply_v1","tracking_fact_summary":null,"tracking_fact_evidence_present":false,"tenant_id":"default","channel_key":"website","session_id":"sla-probe"}
JSON
}

run_one() {
  local idx="$1"
  local token="${NEXUS_CODEX_ACCESS_TOKEN:-dummy-token}"
  local out="$OUT_DIR/response_$idx.json"
  local meta="$OUT_DIR/meta_$idx.txt"
  local start end code elapsed backend
  start="$(date +%s%3N)"
  code="$(curl -sS -o "$out" -w '%{http_code}' \
    -H "Authorization: Bearer $BRIDGE_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Origin: $ORIGIN" \
    --data "$(payload "$token")" \
    "$BRIDGE_URL" || true)"
  end="$(date +%s%3N)"
  elapsed=$((end-start))
  backend="$(grep -ao 'nexus_codex_appserver_runtime\|python_cli_pool\|codex_app_server' "$out" | head -n1 || true)"
  printf 'code=%s elapsed_ms=%s backend=%s\n' "$code" "$elapsed" "$backend" > "$meta"
}

for i in $(seq 1 "$SEQUENTIAL"); do
  run_one "seq_$i"
done

for i in $(seq 1 "$PARALLEL"); do
  run_one "par_$i" &
done
wait

dummy_out="$OUT_DIR/response_dummy_negative.json"
dummy_code="$(curl -sS -o "$dummy_out" -w '%{http_code}' \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Origin: $ORIGIN" \
  --data "$(payload "dummy-token")" \
  "$BRIDGE_URL" || true)"
printf 'code=%s elapsed_ms=0 backend=dummy_negative\n' "$dummy_code" > "$OUT_DIR/meta_dummy_negative.txt"

python - "$OUT_DIR" <<'PY'
import json, math, re, sys
from pathlib import Path

out = Path(sys.argv[1])
elapsed = []
success = error = timeout = invalid_json = leaks = dummy_success = 0
secret_re = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}|Bearer [A-Za-z0-9._-]{20,}|accessToken", re.I)
for meta in out.glob("meta_*.txt"):
    fields = dict(item.split("=", 1) for item in meta.read_text().strip().split())
    code = int(fields.get("code", "0"))
    elapsed.append(int(fields.get("elapsed_ms", "0")))
    body_path = out / meta.name.replace("meta_", "response_").replace(".txt", ".json")
    text = body_path.read_text(errors="replace") if body_path.exists() else ""
    if secret_re.search(text):
        leaks += 1
    if "dummy_negative" in meta.name and code == 200:
        dummy_success += 1
    if code == 200:
        success += 1
    else:
        error += 1
    if code == 504:
        timeout += 1
    try:
        json.loads(text)
    except Exception:
        invalid_json += 1
elapsed.sort()
def pct(p):
    if not elapsed:
        return 0
    return elapsed[min(len(elapsed)-1, math.ceil(len(elapsed)*p)-1)]
summary = {
    "min_ms": elapsed[0] if elapsed else 0,
    "p50_ms": pct(0.50),
    "p95_ms": pct(0.95),
    "p99_ms": pct(0.99),
    "max_ms": elapsed[-1] if elapsed else 0,
    "success_count": success,
    "error_count": error,
    "timeout_count": timeout,
    "invalid_json_count": invalid_json,
    "token_leakage_count": leaks,
    "dummy_token_success_count": dummy_success,
}
print(json.dumps(summary, indent=2, sort_keys=True))
(out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
failed = summary["p50_ms"] > 2500 or summary["p95_ms"] > 8000 or summary["max_ms"] > 12000 or leaks != 0 or dummy_success != 0
sys.exit(1 if failed else 0)
PY
