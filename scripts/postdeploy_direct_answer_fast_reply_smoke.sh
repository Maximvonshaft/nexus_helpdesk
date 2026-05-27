#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

BASE_URL="${BASE_URL:-${NEXUS_BASE_URL:-http://127.0.0.1:18081}}"
TOKEN="${NEXUS_TOKEN:-${TOKEN:-}}"
TENANT_KEY="${TENANT_KEY:-default}"
CHANNEL_KEY="${CHANNEL_KEY:-website}"
ORIGIN="${WEBCHAT_ORIGIN:-https://www.leakle.com}"
QUERY="${QUERY:-瑞士海运时效是多少}"
EXPECTED_REPLY="${EXPECTED_REPLY:-瑞士海运时效为 15 天。}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-$$}"
OUT_DIR="${OUT_DIR:-artifacts/postdeploy_direct_answer_smoke_${RUN_ID}}"

if [[ -z "$TOKEN" ]]; then
  echo "NEXUS_TOKEN or TOKEN is required" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
SUMMARY="$OUT_DIR/summary.tsv"
LOG="$OUT_DIR/smoke.log"
TMP="$OUT_DIR/tmp"
mkdir -p "$TMP"
: > "$LOG"
printf 'severity\ttest\tstatus\tdetail\n' > "$SUMMARY"

exec > >(tee -a "$LOG") 2>&1

ITEM_ID=""
ARCHIVED="false"

record() {
  local severity="$1"
  local test="$2"
  local status="$3"
  local detail="$4"
  printf '%s\t%s\t%s\t%s\n' "$severity" "$test" "$status" "$detail" | tee -a "$SUMMARY"
}

json_field() {
  local path="$1"
  local key="$2"
  python - "$path" "$key" <<'PY'
import json, sys
path, key = sys.argv[1:]
try:
    payload = json.load(open(path, encoding='utf-8'))
except Exception:
    raise SystemExit(0)
cur = payload
for part in key.split('.'):
    if isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
if isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False, sort_keys=True))
elif cur is not None:
    print(cur)
PY
}

api_post() {
  local path="$1"
  local body_file="$2"
  local out_file="$3"
  curl -sS -X POST "${BASE_URL%/}${path}" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOKEN}" \
    --data-binary "@${body_file}" \
    -w '\nHTTP_CODE=%{http_code}\n' \
    -o "$out_file"
}

api_patch() {
  local path="$1"
  local body_file="$2"
  local out_file="$3"
  curl -sS -X PATCH "${BASE_URL%/}${path}" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${TOKEN}" \
    --data-binary "@${body_file}" \
    -w '\nHTTP_CODE=%{http_code}\n' \
    -o "$out_file"
}

cleanup() {
  if [[ -n "$ITEM_ID" && "$ARCHIVED" != "true" ]]; then
    echo "===== AUTO-ARCHIVE TEMP KB item_id=$ITEM_ID ====="
    cat > "$TMP/archive.json" <<'JSON'
{"status":"archived"}
JSON
    set +e
    api_patch "/api/knowledge-items/${ITEM_ID}" "$TMP/archive.json" "$TMP/archive.out.json" | tee "$TMP/archive.http.txt"
    local rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
      ARCHIVED="true"
      record "INFO" "temp_kb_archive" "PASS" "item_id=${ITEM_ID}"
    else
      record "WARN" "temp_kb_archive" "FAIL" "item_id=${ITEM_ID} rc=${rc}"
    fi
  fi
}
trap cleanup EXIT

echo "===== POSTDEPLOY DIRECT ANSWER SMOKE START $(date -Is) ====="
echo "OUT_DIR=$OUT_DIR"
echo "BASE_URL=$BASE_URL"
echo "TENANT_KEY=$TENANT_KEY CHANNEL_KEY=$CHANNEL_KEY ORIGIN=$ORIGIN"

HEALTHZ="$TMP/healthz.json"
curl -sS -f "${BASE_URL%/}/healthz" -o "$HEALTHZ"
RUNTIME_SHA="$(json_field "$HEALTHZ" git_sha)"
if [[ -n "$EXPECTED_SHA" && "$RUNTIME_SHA" != "$EXPECTED_SHA" ]]; then
  record "CRITICAL" "runtime_git_sha" "FAIL" "actual=${RUNTIME_SHA} expected=${EXPECTED_SHA}"
  exit 4
fi
record "CRITICAL" "runtime_git_sha" "PASS" "$RUNTIME_SHA"

ITEM_KEY="probe.pr259.direct-answer.${RUN_ID//[^A-Za-z0-9_.-]/-}"
CREATE_BODY="$TMP/create_kb.json"
python - "$ITEM_KEY" "$QUERY" "$EXPECTED_REPLY" > "$CREATE_BODY" <<'PY'
import json, sys
item_key, query, answer = sys.argv[1:]
print(json.dumps({
    "item_key": item_key,
    "title": "PR259 direct answer smoke temporary fact",
    "summary": "Temporary approved business fact for direct-answer fast-reply smoke. Archived by this script after fast-reply probe.",
    "status": "draft",
    "source_type": "text",
    "knowledge_kind": "business_fact",
    "channel": "website",
    "audience_scope": "customer",
    "language": "zh",
    "priority": 1,
    "fact_question": query,
    "fact_answer": answer,
    "fact_aliases_json": ["瑞士海运多久", "瑞士海运要几天", "Swiss sea freight SLA"],
    "fact_status": "approved",
    "answer_mode": "direct_answer",
    "citation_metadata_json": {"source": "pr259_direct_answer_smoke", "temporary": True},
}, ensure_ascii=False))
PY

api_post "/api/knowledge-items" "$CREATE_BODY" "$TMP/create_kb.out.json" | tee "$TMP/create_kb.http.txt"
CREATE_CODE="$(grep -Eo 'HTTP_CODE=[0-9]+' "$TMP/create_kb.http.txt" | tail -1 | cut -d= -f2)"
if [[ "$CREATE_CODE" != "200" && "$CREATE_CODE" != "201" ]]; then
  record "CRITICAL" "temp_business_fact_create" "FAIL" "http=${CREATE_CODE}"
  exit 4
fi
ITEM_ID="$(json_field "$TMP/create_kb.out.json" id)"
record "CRITICAL" "temp_business_fact_create" "PASS" "item_id=${ITEM_ID}"

api_post "/api/knowledge-items/${ITEM_ID}/publish" <(printf '{"notes":"pr259 direct-answer smoke temporary publish"}') "$TMP/publish_kb.out.json" | tee "$TMP/publish_kb.http.txt"
PUBLISH_CODE="$(grep -Eo 'HTTP_CODE=[0-9]+' "$TMP/publish_kb.http.txt" | tail -1 | cut -d= -f2)"
if [[ "$PUBLISH_CODE" != "200" ]]; then
  record "CRITICAL" "temp_business_fact_publish" "FAIL" "http=${PUBLISH_CODE} item_id=${ITEM_ID}"
  exit 4
fi
record "CRITICAL" "temp_business_fact_publish" "PASS" "item_id=${ITEM_ID}"

RETRIEVE_BODY="$TMP/retrieve.json"
python - "$QUERY" > "$RETRIEVE_BODY" <<'PY'
import json, sys
query = sys.argv[1]
print(json.dumps({"query": query, "channel": "website", "audience_scope": "customer", "language": "zh", "limit": 5}, ensure_ascii=False))
PY
api_post "/api/knowledge-items/retrieve-test" "$RETRIEVE_BODY" "$TMP/retrieve.out.json" | tee "$TMP/retrieve.http.txt"
if ! grep -q "$EXPECTED_REPLY" "$TMP/retrieve.out.json"; then
  record "CRITICAL" "controlled_rag_retrieve" "FAIL" "expected direct_answer missing"
  exit 4
fi
record "CRITICAL" "controlled_rag_retrieve" "PASS" "direct_answer present"

RUNTIME_BODY="$TMP/runtime_context.json"
python - "$TENANT_KEY" "$CHANNEL_KEY" "$QUERY" > "$RUNTIME_BODY" <<'PY'
import json, sys
tenant_key, channel_key, query = sys.argv[1:]
print(json.dumps({"tenant_key": tenant_key, "channel_key": channel_key, "body": query, "language": "zh"}, ensure_ascii=False))
PY
api_post "/api/knowledge-items/runtime-context-test" "$RUNTIME_BODY" "$TMP/runtime_context.out.json" | tee "$TMP/runtime_context.http.txt"
if ! grep -q "$EXPECTED_REPLY" "$TMP/runtime_context.out.json"; then
  record "CRITICAL" "controlled_rag_runtime" "FAIL" "expected direct_answer missing"
  exit 4
fi
record "CRITICAL" "controlled_rag_runtime" "PASS" "direct_answer present"

# Critical order: fast-reply is executed before temporary KB archive.
FAST_BODY="$TMP/fast_reply.json"
python - "$TENANT_KEY" "$CHANNEL_KEY" "$QUERY" "$RUN_ID" > "$FAST_BODY" <<'PY'
import json, sys
tenant_key, channel_key, query, run_id = sys.argv[1:]
print(json.dumps({
    "tenant_key": tenant_key,
    "channel_key": channel_key,
    "session_id": f"pr259-direct-answer-smoke-{run_id}",
    "client_message_id": f"pr259-direct-answer-smoke-{run_id}-msg",
    "body": query,
    "recent_context": [],
    "visitor": {"name": "PR259 Direct Answer Smoke"},
}, ensure_ascii=False))
PY
curl -sS -X POST "${BASE_URL%/}/api/webchat/fast-reply" \
  -H 'Content-Type: application/json' \
  -H "Origin: ${ORIGIN}" \
  --data-binary "@${FAST_BODY}" \
  -w '\nHTTP_CODE=%{http_code}\n' \
  -o "$TMP/fast_reply.out.json" | tee "$TMP/fast_reply.http.txt"
FAST_CODE="$(grep -Eo 'HTTP_CODE=[0-9]+' "$TMP/fast_reply.http.txt" | tail -1 | cut -d= -f2)"
FAST_OK="$(json_field "$TMP/fast_reply.out.json" ok)"
FAST_REPLY="$(json_field "$TMP/fast_reply.out.json" reply)"
FAST_SOURCE="$(json_field "$TMP/fast_reply.out.json" reply_source)"
FAST_GROUNDED="$(json_field "$TMP/fast_reply.out.json" grounding_applied)"
FAST_REASON="$(json_field "$TMP/fast_reply.out.json" grounding_reason)"

if [[ "$FAST_CODE" != "200" || "$FAST_OK" != "True" || "$FAST_REPLY" != *"15"* || "$FAST_SOURCE" != "codex_app_server" || "$FAST_GROUNDED" != "True" || "$FAST_REASON" != "locked_facts_provider_generated" ]]; then
  record "CRITICAL" "controlled_rag_fast_reply" "FAIL" "http=${FAST_CODE} ok=${FAST_OK} reply=${FAST_REPLY} source=${FAST_SOURCE} grounded=${FAST_GROUNDED} reason=${FAST_REASON}"
  exit 4
fi
record "CRITICAL" "controlled_rag_fast_reply" "PASS" "reply=${FAST_REPLY} source=${FAST_SOURCE} grounded=${FAST_GROUNDED}"

echo "===== POSTDEPLOY DIRECT ANSWER SMOKE PASS $(date -Is) ====="
