#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-${NEXUS_BASE_URL:-http://127.0.0.1:8000}}"
TOKEN="${NEXUS_TOKEN:-${TOKEN:-}}"
QUERY="${QUERY:-${PROBE_QUERY:-Swiss address change fee}}"
EXPECT="${EXPECT:-${PROBE_EXPECT:-8 CHF}}"
CHANNEL="${CHANNEL:-website}"
AUDIENCE="${AUDIENCE:-customer}"
LANGUAGE="${LANGUAGE:-en}"
TENANT_KEY="${TENANT_KEY:-default}"
LIMIT="${LIMIT:-5}"
OUT_DIR="${OUT_DIR:-artifacts/knowledge_retrieval_probe}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
SKIP_RUNTIME_SHA_GATE="${SKIP_RUNTIME_SHA_GATE:-false}"
CREATE_TEMP_KB="false"
RUN_ID="${RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
REQUEST_ID="knowledge-probe-${RUN_ID}"

usage() {
  printf '%s\n' "Usage: $0 [--base-url URL] [--token TOKEN] [--query TEXT] [--expect TEXT] [--channel KEY] [--audience SCOPE] [--language CODE] [--out-dir DIR] [--create-temp-kb]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --query) QUERY="$2"; shift 2 ;;
    --expect) EXPECT="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --audience) AUDIENCE="$2"; shift 2 ;;
    --language) LANGUAGE="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --create-temp-kb) CREATE_TEMP_KB="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  echo "NEXUS_TOKEN or --token is required for admin knowledge and audit endpoints." >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
JSONL="$OUT_DIR/knowledge_probe_${RUN_ID}.jsonl"
TSV="$OUT_DIR/knowledge_probe_${RUN_ID}.tsv"
MD="$OUT_DIR/knowledge_probe_${RUN_ID}.md"
TMP="$OUT_DIR/tmp_${RUN_ID}"
mkdir -p "$TMP"
AUTH_HEADER="Authorization: Bearer ${TOKEN}"

json_escape() {
  python - "$1" <<'PY'
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

json_field() {
  local path="$1"
  local key="$2"
  python - "$path" "$key" <<'PY'
import json
import sys

path, key = sys.argv[1:]
try:
    payload = json.load(open(path, encoding="utf-8"))
except Exception:
    raise SystemExit(0)
value = payload.get(key)
print("" if value is None else str(value))
PY
}

write_tsv_header() {
  printf 'step\tstatus\tkey\tvalue\n' > "$TSV"
}

record_step() {
  local step="$1"
  local status="$2"
  local file="$3"
  python - "$step" "$status" "$file" "$JSONL" "$TSV" <<'PY'
import json, sys
step, status, path, jsonl, tsv = sys.argv[1:]
try:
    payload = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    payload = {"error": type(exc).__name__, "detail": str(exc)}
with open(jsonl, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"step": step, "status": status, "payload": payload}, ensure_ascii=False, sort_keys=True) + "\n")
with open(tsv, "a", encoding="utf-8") as fh:
    fh.write(f"{step}\t{status}\trequest_id\t{payload.get('request_id', '')}\n")
    if isinstance(payload, dict):
        if "total" in payload:
            fh.write(f"{step}\t{status}\ttotal\t{payload.get('total')}\n")
        if "reply" in payload:
            fh.write(f"{step}\t{status}\treply\t{str(payload.get('reply') or '').replace(chr(9), ' ')[:300]}\n")
PY
}

api_post() {
  local path="$1"
  local body_file="$2"
  local out_file="$3"
  curl -sS -f -X POST "${BASE_URL%/}${path}" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -H "X-Request-Id: $REQUEST_ID" \
    --data-binary "@$body_file" \
    -o "$out_file"
}

runtime_sha_gate() {
  if [[ "$SKIP_RUNTIME_SHA_GATE" == "true" ]]; then
    printf '{"runtime_git_sha":"skipped","reason":"SKIP_RUNTIME_SHA_GATE=true"}' > "$TMP/runtime_git_sha.out.json"
    record_step "runtime_git_sha" "skipped" "$TMP/runtime_git_sha.out.json"
    return 0
  fi

  local healthz_out="$TMP/healthz.out.json"
  curl -sS -f "${BASE_URL%/}/healthz" -o "$healthz_out"
  local healthz_sha
  healthz_sha="$(json_field "$healthz_out" git_sha || true)"
  local expected_sha="${EXPECTED_SHA:-$healthz_sha}"
  local expected_source="healthz-derived"
  if [[ -n "$EXPECTED_SHA" ]]; then
    expected_source="EXPECTED_SHA env override"
  fi
  python - "$healthz_out" "$expected_sha" "$expected_source" "$TMP/runtime_git_sha.out.json" <<'PY'
import json
import sys

healthz_path, expected_sha, expected_source, out_path = sys.argv[1:]
payload = json.load(open(healthz_path, encoding="utf-8"))
actual = str(payload.get("git_sha") or "").strip()
expected = str(expected_sha or "").strip()
result = {
    "runtime_git_sha": actual,
    "expected_sha": expected,
    "source": expected_source,
}
if not actual:
    result["status"] = "fail"
    result["error"] = "healthz git_sha missing"
elif not expected:
    result["status"] = "fail"
    result["error"] = "expected SHA missing"
elif actual.lower() != expected.lower():
    result["status"] = "fail"
    result["error"] = "runtime_git_sha mismatch"
else:
    result["status"] = "pass"
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, sort_keys=True)
if result["status"] != "pass":
    raise SystemExit(f"{result['error']}: actual={actual} expected={expected}")
PY
  record_step "runtime_git_sha" "ok" "$TMP/runtime_git_sha.out.json"
}

write_tsv_header
runtime_sha_gate

if [[ "$CREATE_TEMP_KB" == "true" ]]; then
  ITEM_KEY="probe.business_fact.${RUN_ID}"
  CREATE_BODY="$TMP/create_kb.json"
  python - "$ITEM_KEY" "$CHANNEL" "$AUDIENCE" "$LANGUAGE" "$QUERY" "$EXPECT" > "$CREATE_BODY" <<'PY'
import json, sys
item_key, channel, audience, language, query, expect = sys.argv[1:]
print(json.dumps({
    "item_key": item_key,
    "title": "Temporary probe business fact",
    "summary": "Temporary explicit KB row created only because --create-temp-kb was passed.",
    "status": "draft",
    "source_type": "text",
    "knowledge_kind": "business_fact",
    "channel": channel,
    "audience_scope": audience,
    "language": language or None,
    "priority": 1,
    "fact_question": query,
    "fact_answer": f"Probe expected business fact: {expect}",
    "fact_aliases_json": [query],
    "fact_status": "approved",
    "answer_mode": "direct_answer",
    "citation_metadata_json": {"source": "probe_temp_kb"},
}, ensure_ascii=False))
PY
  CREATE_OUT="$TMP/create_kb.out.json"
  api_post "/api/knowledge-items" "$CREATE_BODY" "$CREATE_OUT"
  record_step "create_temp_kb" "ok" "$CREATE_OUT"
  ITEM_ID="$(python - "$CREATE_OUT" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["id"])
PY
)"
  PUBLISH_BODY="$TMP/publish_kb.json"
  printf '{"notes":"temporary production probe publish"}' > "$PUBLISH_BODY"
  PUBLISH_OUT="$TMP/publish_kb.out.json"
  api_post "/api/knowledge-items/${ITEM_ID}/publish" "$PUBLISH_BODY" "$PUBLISH_OUT"
  record_step "publish_temp_kb" "ok" "$PUBLISH_OUT"
fi

RETRIEVAL_BODY="$TMP/retrieve.json"
python - "$QUERY" "$CHANNEL" "$AUDIENCE" "$LANGUAGE" "$LIMIT" > "$RETRIEVAL_BODY" <<'PY'
import json, sys
q, channel, audience, language, limit = sys.argv[1:]
print(json.dumps({"q": q, "channel": channel or None, "audience_scope": audience or "customer", "language": language or None, "limit": int(limit)}, ensure_ascii=False))
PY
RETRIEVAL_OUT="$TMP/retrieve.out.json"
api_post "/api/knowledge-items/retrieve-test" "$RETRIEVAL_BODY" "$RETRIEVAL_OUT"
record_step "retrieval" "ok" "$RETRIEVAL_OUT"

CONTEXT_BODY="$TMP/runtime_context.json"
python - "$TENANT_KEY" "$QUERY" "$CHANNEL" "$AUDIENCE" "$LANGUAGE" "$LIMIT" > "$CONTEXT_BODY" <<'PY'
import json, sys
tenant, q, channel, audience, language, limit = sys.argv[1:]
print(json.dumps({"tenant_key": tenant, "q": q, "channel": channel or None, "audience_scope": audience or "customer", "language": language or None, "limit": int(limit)}, ensure_ascii=False))
PY
CONTEXT_OUT="$TMP/runtime_context.out.json"
api_post "/api/knowledge-items/runtime-context-test" "$CONTEXT_BODY" "$CONTEXT_OUT"
record_step "runtime_context" "ok" "$CONTEXT_OUT"

FAST_BODY="$TMP/fast_reply.json"
python - "$TENANT_KEY" "$CHANNEL" "$RUN_ID" "$QUERY" > "$FAST_BODY" <<'PY'
import json, sys
tenant, channel, run_id, query = sys.argv[1:]
print(json.dumps({
    "tenant_key": tenant,
    "channel_key": channel,
    "session_id": f"knowledge-probe-{run_id}",
    "client_message_id": f"knowledge-probe-msg-{run_id}",
    "body": query,
    "recent_context": [],
    "visitor": {"name": "Knowledge Probe"},
}, ensure_ascii=False))
PY
FAST_OUT="$TMP/fast_reply.out.json"
curl -sS -f -X POST "${BASE_URL%/}/api/webchat/fast-reply" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: $REQUEST_ID" \
  --data-binary "@$FAST_BODY" \
  -o "$FAST_OUT"
record_step "fast_reply" "ok" "$FAST_OUT"

AUDIT_OUT="$TMP/audit.out.json"
curl -sS -f "${BASE_URL%/}/api/admin/provider-runtime/audit/recent?request_id=${REQUEST_ID}&limit=10" \
  -H "$AUTH_HEADER" \
  -o "$AUDIT_OUT" || printf '{"items":[],"total":0,"warning":"audit endpoint unavailable or provider runtime not used"}' > "$AUDIT_OUT"
record_step "audit_trail" "ok" "$AUDIT_OUT"

python - "$RETRIEVAL_OUT" "$CONTEXT_OUT" "$FAST_OUT" "$AUDIT_OUT" "$EXPECT" "$MD" "$JSONL" "$TSV" <<'PY'
import json, sys
retrieval_path, context_path, fast_path, audit_path, expect, md_path, jsonl, tsv = sys.argv[1:]
retrieval = json.load(open(retrieval_path, encoding="utf-8"))
context = json.load(open(context_path, encoding="utf-8"))
fast = json.load(open(fast_path, encoding="utf-8"))
audit = json.load(open(audit_path, encoding="utf-8"))
blob = json.dumps({"retrieval": retrieval, "context": context}, ensure_ascii=False).lower()
reply = str(fast.get("reply") or "")
expect_lower = expect.lower()
assert retrieval.get("hits"), "retrieval returned no hits"
assert expect_lower in blob, f"expected fact {expect!r} not found in retrieval/runtime_context"
assert expect_lower in reply.lower(), f"expected fact {expect!r} not found in fast-reply: {reply!r}"
for marker in ("cannot confirm", "无法确认", "不清楚", "无法核实"):
    assert marker not in reply.lower(), f"unsupported refusal marker present: {marker}"
top = retrieval["hits"][0]
with open(md_path, "w", encoding="utf-8") as fh:
    fh.write("# Knowledge Retrieval Probe\n\n")
    fh.write(f"- Query: `{retrieval.get('query_analysis', {}).get('normalized_query', '')}`\n")
    fh.write(f"- Expected fact: `{expect}`\n")
    fh.write(f"- Top hit: `{top.get('item_key')}` score `{top.get('score')}`\n")
    fh.write(f"- Retrieval method: `{top.get('retrieval_method')}`\n")
    fh.write(f"- Grounding would apply: `{retrieval.get('grounding_would_apply')}`\n")
    fh.write(f"- Fast reply source: `{fast.get('reply_source')}`\n")
    fh.write(f"- Fast reply: {reply}\n")
    fh.write(f"- Audit rows: `{len(audit.get('items', []))}`\n")
    fh.write(f"- JSONL: `{jsonl}`\n")
    fh.write(f"- TSV: `{tsv}`\n")
print(md_path)
PY

echo "Knowledge retrieval probe passed."
echo "JSONL: $JSONL"
echo "TSV: $TSV"
echo "Markdown: $MD"
