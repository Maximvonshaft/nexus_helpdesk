#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

: "${SPEEDAF_MCP_BASE_URL:=https://uat-api.speedaf.com}"
: "${SPEEDAF_MCP_CONTENT_TYPE:=text/plain}"
: "${SPEEDAF_MCP_DATA_MODE:=string}"
: "${SPEEDAF_MCP_COUNTRY_CODE_DEFAULT:=CH}"

REPORT_DIR="${REPORT_DIR:-/tmp/nexus-speedaf-mcp-smoke}"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/speedaf_mcp_contract_report.txt"

redact() {
  sed -E \
    -e 's/(SPEEDAF_MCP_APP_CODE=).*/\1[REDACTED]/g' \
    -e 's/(SPEEDAF_MCP_SECRET_KEY=).*/\1[REDACTED]/g' \
    -e 's/(callerID[^A-Za-z0-9]*[A-Za-z0-9+_.@-]+)/callerID=[REDACTED]/g' \
    -e 's/("callerID"[[:space:]]*:[[:space:]]*")[^"]*/\1[REDACTED]/g' \
    -e 's/(\\"callerID\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[REDACTED]/g' \
    -e 's/("acceptAddress"[[:space:]]*:[[:space:]]*")[^"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/(\\"acceptAddress\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/("acceptName"[[:space:]]*:[[:space:]]*")[^"]*/\1[NAME-REDACTED]/g' \
    -e 's/(\\"acceptName\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[NAME-REDACTED]/g' \
    -e 's/("acceptMobile"[[:space:]]*:[[:space:]]*")[^"]*/\1[PHONE-REDACTED]/g' \
    -e 's/(\\"acceptMobile\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[PHONE-REDACTED]/g' \
    -e 's/("waybillCode"[[:space:]]*:[[:space:]]*")[^"]*/\1[WAYBILL-REDACTED]/g' \
    -e 's/(\\"waybillCode\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[WAYBILL-REDACTED]/g' \
    -e 's/("senderAddress"[[:space:]]*:[[:space:]]*")[^"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/(\\"senderAddress\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/("senderName"[[:space:]]*:[[:space:]]*")[^"]*/\1[NAME-REDACTED]/g' \
    -e 's/(\\"senderName\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[NAME-REDACTED]/g' \
    -e 's/("senderMobile"[[:space:]]*:[[:space:]]*")[^"]*/\1[PHONE-REDACTED]/g' \
    -e 's/(\\"senderMobile\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[PHONE-REDACTED]/g' \
    -e 's/("receiverAddress"[[:space:]]*:[[:space:]]*")[^"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/(\\"receiverAddress\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[ADDRESS-REDACTED]/g' \
    -e 's/("receiverName"[[:space:]]*:[[:space:]]*")[^"]*/\1[NAME-REDACTED]/g' \
    -e 's/(\\"receiverName\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[NAME-REDACTED]/g' \
    -e 's/("receiverMobile"[[:space:]]*:[[:space:]]*")[^"]*/\1[PHONE-REDACTED]/g' \
    -e 's/(\\"receiverMobile\\"[[:space:]]*:[[:space:]]*\\")[^\\"]*/\1[PHONE-REDACTED]/g' \
    -e 's/([0-9]{6,})/[DIGITS-REDACTED]/g'
}

{
  echo "Speedaf MCP Contract Smoke"
  echo "time_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "base_url=$SPEEDAF_MCP_BASE_URL"
  echo "content_type=$SPEEDAF_MCP_CONTENT_TYPE"
  echo "data_mode=$SPEEDAF_MCP_DATA_MODE"
  echo "country_code=$SPEEDAF_MCP_COUNTRY_CODE_DEFAULT"
  echo "python=$(command -v python3 || command -v python || true)"
  echo
} > "$REPORT"

if [[ -z "${SPEEDAF_MCP_APP_CODE:-}" ]]; then
  echo "SKIP: SPEEDAF_MCP_APP_CODE is not set" | tee -a "$REPORT"
  echo "Report: $REPORT"
  exit 0
fi

PYTHON_BIN="$(command -v python3 || command -v python)"

set +e
"$PYTHON_BIN" - <<'PY' 2>&1 | redact | tee -a "$REPORT"
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

base_url = os.environ.get("SPEEDAF_MCP_BASE_URL", "https://uat-api.speedaf.com").rstrip("/")
app_code = os.environ.get("SPEEDAF_MCP_APP_CODE", "")
content_type = os.environ.get("SPEEDAF_MCP_CONTENT_TYPE", "text/plain")
data_mode = os.environ.get("SPEEDAF_MCP_DATA_MODE", "string")
country_code = os.environ.get("SPEEDAF_MCP_COUNTRY_CODE_DEFAULT", "CH")
test_waybill = os.environ.get("SPEEDAF_MCP_TEST_WAYBILL_CODE", "")
test_caller = os.environ.get("SPEEDAF_MCP_TEST_CALLER_ID", "")
timeout = int(os.environ.get("SPEEDAF_MCP_TIMEOUT_SECONDS", "8"))


def build_body(payload):
    return {"data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))} if data_mode == "string" else {"data": payload}


def post(path, payload):
    timestamp = int(time.time() * 1000)
    url = f"{base_url}{path}?" + urlencode({"appCode": app_code, "timestamp": timestamp})
    body = json.dumps(build_body(payload), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type, "Accept": "application/json"}, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            print(json.dumps({"path": path, "http_status": resp.status, "elapsed_ms": int((time.time() - started) * 1000), "raw_preview": raw[:500]}, ensure_ascii=False))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        print(json.dumps({"path": path, "http_status": exc.code, "elapsed_ms": int((time.time() - started) * 1000), "error_preview": raw[:500]}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"path": path, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))

print("timestamp_ms_check=ok")
if test_caller:
    post("/open-api/mcp/order/waybillCode/query", {"callerID": test_caller, "countryCode": country_code})
else:
    print("waybillCode/query=SKIP missing SPEEDAF_MCP_TEST_CALLER_ID")

if test_waybill:
    payload = {"waybillCode": test_waybill}
    if test_caller:
        payload["callerID"] = test_caller
    post("/open-api/mcp/order/query", payload)
else:
    print("order/query=SKIP missing SPEEDAF_MCP_TEST_WAYBILL_CODE")
PY
STATUS=${PIPESTATUS[0]}
set -e

if grep -E "${SPEEDAF_MCP_APP_CODE:-__NO_APP_CODE__}|${SPEEDAF_MCP_SECRET_KEY:-__NO_SECRET__}" "$REPORT" >/dev/null 2>&1; then
  echo "FAIL: secret leak detected in report" >&2
  exit 2
fi

if grep -Ei 'accept(Address|Name|Mobile)|sender(Address|Name|Mobile)|receiver(Address|Name|Mobile)' "$REPORT" | grep -Ev '\[(ADDRESS|NAME|PHONE)-REDACTED\]' >/dev/null 2>&1; then
  echo "FAIL: PII field leak detected in report" >&2
  exit 3
fi

echo "Report: $REPORT"
exit "$STATUS"