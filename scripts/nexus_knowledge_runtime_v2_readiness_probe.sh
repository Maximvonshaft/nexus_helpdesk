#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WAYBILL="${SPEEDAF_MCP_TEST_WAYBILL_CODE:-CH020000006856}"
CALLER_ID="${SPEEDAF_MCP_TEST_CALLER_ID:-}"

echo "== Nexus Knowledge Runtime v2 readiness probe =="
echo "base_url=${BASE_URL}"

curl -fsS "${BASE_URL}/healthz" >/tmp/nexus_healthz.json
curl -fsS "${BASE_URL}/readyz" >/tmp/nexus_readyz.json
echo "healthz_ok=true"
echo "readyz_ok=true"

PYTHONPATH=backend python - <<'PY'
from app.services.tracking_fact_service import extract_tracking_number
value = extract_tracking_number("CH020000006856这是我的订单号")
assert value == "CH020000006856", value
print("tracking_extraction=CH020000006856")
PY

python backend/scripts/production_knowledge_runtime_fixup.py >/tmp/nexus_knowledge_fixup.json
PYTHONPATH=backend python - <<'PY'
import json
payload=json.load(open("/tmp/nexus_knowledge_fixup.json", encoding="utf-8"))
assert payload["ok"] is True, payload
assert payload["speedaf_persona"] == "speedaf_support_webchat_default", payload
print("production_fixup_ok=true")
PY

if [ -n "${CALLER_ID}" ]; then
  PYTHONPATH=backend python - <<PY
from app.services.tracking_fact_service import lookup_tracking_fact
result = lookup_tracking_fact(tracking_number="${WAYBILL}", caller_id="${CALLER_ID}", request_id="knowledge-runtime-v2-readiness")
assert result.ok and result.fact_evidence_present and result.tool_status == "success", result
print("speedaf_direct_lookup_ok=true")
PY
else
  echo "speedaf_direct_lookup_skipped=missing_SPEEDAF_MCP_TEST_CALLER_ID"
fi

curl -fsS -X POST "${BASE_URL}/api/webchat/fast-reply" \
  -H 'Content-Type: application/json' \
  -d "{\"tenant_key\":\"default\",\"channel_key\":\"website\",\"session_id\":\"readiness-v2\",\"client_message_id\":\"readiness-v2-1\",\"body\":\"${WAYBILL}这是我的订单号\",\"visitor\":{\"phone\":\"${CALLER_ID}\"}}" \
  >/tmp/nexus_fast_reply.json

PYTHONPATH=backend python - <<'PY'
import json
payload=json.load(open("/tmp/nexus_fast_reply.json", encoding="utf-8"))
assert payload.get("reply_source") == "server_tracking_fact", payload
assert payload.get("tracking_number") == "CH020000006856", payload
assert payload.get("tracking_fact", {}).get("fact_evidence_present") is True, payload
assert payload.get("error_code") != "all_providers_failed", payload
text=json.dumps(payload, ensure_ascii=False)
assert "猴王山" not in text and "[PROBE]" not in text, text
print("fast_reply_truth_routing_ok=true")
PY

echo "readiness_probe_ok=true"
