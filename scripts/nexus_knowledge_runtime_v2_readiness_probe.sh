#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WAYBILL="${SPEEDAF_MCP_TEST_WAYBILL_CODE:-}"
CALLER_ID="${SPEEDAF_MCP_TEST_CALLER_ID:-}"

echo "== Nexus Knowledge Runtime v2 readiness probe =="
echo "base_url=${BASE_URL}"

if [ -z "${WAYBILL}" ] || [ -z "${CALLER_ID}" ]; then
  echo "ERROR: set SPEEDAF_MCP_TEST_WAYBILL_CODE and SPEEDAF_MCP_TEST_CALLER_ID from GitHub Secrets or a production secret store."
  exit 2
fi

curl -fsS "${BASE_URL}/healthz" >/tmp/nexus_healthz.json
curl -fsS "${BASE_URL}/readyz" >/tmp/nexus_readyz.json
echo "healthz_ok=true"
echo "readyz_ok=true"

PYTHONPATH=backend python - <<'PY'
from app.services.tracking_fact_service import extract_tracking_number
value = extract_tracking_number("CH020000006856这是我的订单号")
assert value == "CH020000006856", value
print("tracking_extraction_ok=true")
PY

python backend/scripts/production_knowledge_runtime_fixup.py >/tmp/nexus_knowledge_fixup.json
PYTHONPATH=backend python - <<'PY'
import json
payload=json.load(open("/tmp/nexus_knowledge_fixup.json", encoding="utf-8"))
assert payload["ok"] is True, payload
assert payload["speedaf_persona"] == "speedaf_support_webchat_default", payload
print("production_fixup_ok=true")
PY

PYTHONPATH=backend KNOWLEDGE_RUNTIME_VERSION=v2 KNOWLEDGE_EMBEDDINGS_ENABLED=true KNOWLEDGE_EMBEDDING_PROVIDER=deterministic_hash \
  python backend/scripts/run_knowledge_eval.py \
    --min-recall-at-5 1.0 \
    --max-hallucination-rate 0 \
    --max-unsupported-answer-rate 0 \
    --min-handoff-correctness 1.0 \
  >/tmp/nexus_knowledge_eval.json
PYTHONPATH=backend python - <<'PY'
import json
payload=json.load(open("/tmp/nexus_knowledge_eval.json", encoding="utf-8"))
assert payload["ok"] is True, payload
metrics=payload["metrics"]
for key in ("recall_at_5", "direct_answer_correctness", "unsupported_answer_rate", "hallucination_rate", "handoff_correctness", "p95_latency_ms"):
    assert key in metrics, payload
print("knowledge_eval_ok=true")
PY

if [ -n "${CALLER_ID}" ]; then
  PYTHONPATH=backend python - <<PY
from app.services.tracking_fact_service import lookup_tracking_fact
result = lookup_tracking_fact(tracking_number="${WAYBILL}", caller_id="${CALLER_ID}", request_id="knowledge-runtime-v2-readiness")
assert result.ok and result.fact_evidence_present and result.tool_status == "success", "speedaf_direct_lookup_failed"
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
import os
payload=json.load(open("/tmp/nexus_fast_reply.json", encoding="utf-8"))
waybill=os.environ["SPEEDAF_MCP_TEST_WAYBILL_CODE"]
def safe_payload():
    text=json.dumps(payload, ensure_ascii=False)
    caller=os.environ.get("SPEEDAF_MCP_TEST_CALLER_ID") or ""
    for secret in (waybill, caller):
        if secret:
            text=text.replace(secret, "[REDACTED]")
    return text
def require(condition, message):
    if not condition:
        raise AssertionError(f"{message}: {safe_payload()}")
require(payload.get("reply_source") == "server_tracking_fact", "reply_source_not_server_tracking_fact")
require(payload.get("tracking_number") in (None, ""), "tracking_number_not_redacted")
require(payload.get("tracking_number_suffix") == waybill[-6:], "tracking_number_suffix_mismatch")
require(bool(payload.get("tracking_number_hash")), "tracking_number_hash_missing")
require(payload.get("tracking_fact", {}).get("fact_evidence_present") is True, "tracking_fact_evidence_missing")
require(payload.get("tracking_fact", {}).get("truth_trace", {}).get("source") == "speedaf_trusted_tracking_fact", "truth_trace_source_mismatch")
require(payload.get("tracking_fact", {}).get("truth_trace", {}).get("raw_tracking_number_exposed") is False, "raw_tracking_number_exposed")
require(payload.get("evidence_trace", {}).get("retrieval") == "trusted_tracking_fact", "root_evidence_trace_retrieval_mismatch")
require(payload.get("evidence_trace", {}).get("source") == "speedaf_trusted_tracking_fact", "root_evidence_trace_source_mismatch")
require(payload.get("evidence_trace", {}).get("fact_evidence_present") is True, "root_evidence_trace_fact_missing")
require(payload.get("evidence_trace", {}).get("raw_tracking_number_exposed") is False, "root_evidence_trace_raw_waybill_exposed")
require(payload.get("error_code") != "all_providers_failed", "provider_failure_leaked")
text=json.dumps(payload, ensure_ascii=False)
require(waybill not in text, "waybill_exposed_in_public_payload")
require("猴王山" not in text and "[PROBE]" not in text, "production_data_hygiene_failed")
print("fast_reply_truth_routing_ok=true")
PY

echo "readiness_probe_ok=true"
