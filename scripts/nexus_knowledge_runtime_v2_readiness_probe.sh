#!/usr/bin/env bash
set -Eeuo pipefail

WAYBILL="${SPEEDAF_MCP_TEST_WAYBILL_CODE:-}"
CALLER="${SPEEDAF_MCP_TEST_CALLER_ID:-}"

if [ -z "$WAYBILL" ] || [ -z "$CALLER" ]; then
  echo "ERROR: set SPEEDAF_MCP_TEST_WAYBILL_CODE and SPEEDAF_MCP_TEST_CALLER_ID from GitHub Secrets."
  exit 2
fi

python - <<'PY'
from __future__ import annotations

import json
import os

from app.services.tracking_fact_service import lookup_tracking_fact


def redact(text: str) -> str:
    for secret in (os.environ["SPEEDAF_MCP_TEST_WAYBILL_CODE"], os.environ["SPEEDAF_MCP_TEST_CALLER_ID"]):
        if secret:
            text=text.replace(secret, "[REDACTED]")
    return text


def safe_payload():
    waybill=os.environ["SPEEDAF_MCP_TEST_WAYBILL_CODE"]
    caller=os.environ["SPEEDAF_MCP_TEST_CALLER_ID"]
    return {"waybill": waybill, "caller": caller}


payload = safe_payload()
result = lookup_tracking_fact(
    tracking_number=payload["waybill"],
    caller_id=payload["caller"],
    request_id="knowledge-runtime-readiness",
)
report = {
    "ok": result.ok,
    "fact_evidence_present": result.fact_evidence_present,
    "tool_status": result.tool_status,
    "failure_reason": result.failure_reason,
}
print(redact(json.dumps(report, ensure_ascii=False)))
assert result.ok and result.fact_evidence_present and result.tool_status == "success", "speedaf_direct_lookup_failed"
PY
