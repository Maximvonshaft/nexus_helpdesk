#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

BASE_URL="${BASE_URL:-${NEXUS_BASE_URL:-http://127.0.0.1:18081}}"
TOKEN="${NEXUS_TOKEN:-${TOKEN:-}}"
DRY_RUN="${DRY_RUN:-0}"
OUT_DIR="${OUT_DIR:-artifacts/nigeria_direct_answer_kb_upsert_$(date +%Y%m%d_%H%M%S)}"

if [[ -z "$TOKEN" ]]; then
  echo "NEXUS_TOKEN or TOKEN is required" >&2
  exit 2
fi

mkdir -p "$OUT_DIR/tmp"
SUMMARY="$OUT_DIR/summary.tsv"
LOG="$OUT_DIR/upsert.log"
printf 'action\titem_id\titem_key\tfact_answer\tpublished_version\n' > "$SUMMARY"
exec > >(tee -a "$LOG") 2>&1

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
    elif isinstance(cur, list) and part.isdigit():
        idx = int(part)
        cur = cur[idx] if idx < len(cur) else None
    else:
        cur = None
        break
if isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False, sort_keys=True))
elif cur is not None:
    print(cur)
PY
}

api_get() {
  local path="$1"
  local out_file="$2"
  curl -sS -G "${BASE_URL%/}${path}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -w '\nHTTP_CODE=%{http_code}\n' \
    -o "$out_file" \
    --data-urlencode "limit=20"
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

payload_for_fact() {
  local item_key="$1"
  local title="$2"
  local question="$3"
  local answer="$4"
  local alias1="$5"
  local alias2="$6"
  local alias3="$7"
  python - "$item_key" "$title" "$question" "$answer" "$alias1" "$alias2" "$alias3" <<'PY'
import json, sys
item_key, title, question, answer, alias1, alias2, alias3 = sys.argv[1:]
payload = {
    "item_key": item_key,
    "title": title,
    "summary": answer,
    "status": "draft",
    "source_type": "text",
    "knowledge_kind": "business_fact",
    "market_id": None,
    "channel": "website",
    "audience_scope": "customer",
    "language": "zh",
    "priority": 1,
    "fact_question": question,
    "fact_answer": answer,
    "fact_aliases_json": [alias1, alias2, alias3],
    "fact_status": "approved",
    "answer_mode": "direct_answer",
    "citation_metadata_json": {"source": "pr259_nigeria_direct_answer_upsert", "controlled_fact": True},
    "draft_body": None,
    "draft_normalized_text": None,
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
}

find_item_id() {
  local item_key="$1"
  local out_file="$OUT_DIR/tmp/list_${item_key//[^A-Za-z0-9_.-]/_}.json"
  curl -sS -G "${BASE_URL%/}/api/knowledge-items" \
    -H "Authorization: Bearer ${TOKEN}" \
    --data-urlencode "q=${item_key}" \
    --data-urlencode "limit=20" \
    -o "$out_file"
  python - "$out_file" "$item_key" <<'PY'
import json, sys
path, item_key = sys.argv[1:]
payload = json.load(open(path, encoding='utf-8'))
for item in payload.get('items', []):
    if item.get('item_key') == item_key:
        print(item.get('id'))
        raise SystemExit(0)
PY
}

upsert_fact() {
  local item_key="$1"
  local title="$2"
  local question="$3"
  local answer="$4"
  local alias1="$5"
  local alias2="$6"
  local alias3="$7"

  local body="$OUT_DIR/tmp/${item_key}.json"
  payload_for_fact "$item_key" "$title" "$question" "$answer" "$alias1" "$alias2" "$alias3" > "$body"

  local existing_id
  existing_id="$(find_item_id "$item_key" || true)"
  if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" || "$DRY_RUN" == "TRUE" ]]; then
    if [[ -n "$existing_id" ]]; then
      printf 'would_update\t%s\t%s\t%s\t\n' "$existing_id" "$item_key" "$answer" | tee -a "$SUMMARY"
    else
      printf 'would_create\t\t%s\t%s\t\n' "$item_key" "$answer" | tee -a "$SUMMARY"
    fi
    return 0
  fi

  local action item_id http_code out http_file
  if [[ -n "$existing_id" ]]; then
    action="updated"
    item_id="$existing_id"
    out="$OUT_DIR/tmp/update_${item_key}.out.json"
    http_file="$OUT_DIR/tmp/update_${item_key}.http.txt"
    # PATCH schema does not accept item_key, so strip it from update payload.
    python - "$body" > "$OUT_DIR/tmp/update_${item_key}.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding='utf-8'))
payload.pop('item_key', None)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
    api_patch "/api/knowledge-items/${item_id}" "$OUT_DIR/tmp/update_${item_key}.json" "$out" | tee "$http_file"
  else
    action="created"
    out="$OUT_DIR/tmp/create_${item_key}.out.json"
    http_file="$OUT_DIR/tmp/create_${item_key}.http.txt"
    api_post "/api/knowledge-items" "$body" "$out" | tee "$http_file"
    item_id="$(json_field "$out" id)"
  fi
  http_code="$(grep -Eo 'HTTP_CODE=[0-9]+' "$http_file" | tail -1 | cut -d= -f2)"
  if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
    echo "FATAL: ${action} failed for ${item_key}; http=${http_code}" >&2
    cat "$out" >&2 || true
    exit 4
  fi

  local publish_out="$OUT_DIR/tmp/publish_${item_key}.out.json"
  local publish_http="$OUT_DIR/tmp/publish_${item_key}.http.txt"
  local publish_body="$OUT_DIR/tmp/publish_${item_key}.json"
  printf '{"notes":"PR259 Nigeria direct-answer business fact upsert"}' > "$publish_body"
  api_post "/api/knowledge-items/${item_id}/publish" "$publish_body" "$publish_out" | tee "$publish_http"
  http_code="$(grep -Eo 'HTTP_CODE=[0-9]+' "$publish_http" | tail -1 | cut -d= -f2)"
  if [[ "$http_code" != "200" ]]; then
    echo "FATAL: publish failed for ${item_key}; http=${http_code}" >&2
    cat "$publish_out" >&2 || true
    exit 4
  fi
  local version
  version="$(json_field "$publish_out" version)"
  printf '%s\t%s\t%s\t%s\t%s\n' "$action" "$item_id" "$item_key" "$answer" "$version" | tee -a "$SUMMARY"
}

echo "===== NIGERIA DIRECT ANSWER KB UPSERT START $(date -Is) ====="
echo "BASE_URL=$BASE_URL OUT_DIR=$OUT_DIR DRY_RUN=$DRY_RUN"

upsert_fact "fact.ng.shipping-sla.sea" "尼日利亚海运时效" "尼日利亚海运时效是多少？" "尼日利亚海运时效为 15 天。" "尼日利亚海运多久" "尼日利亚海运要几天" "Nigeria sea freight SLA"
upsert_fact "fact.ng.shipping-sla.air" "尼日利亚空运时效" "尼日利亚空运时效是多少？" "尼日利亚空运时效为 10 天。" "尼日利亚空运多久" "尼日利亚空运要几天" "Nigeria air freight SLA"

echo "===== NIGERIA DIRECT ANSWER KB UPSERT DONE $(date -Is) ====="
cat "$SUMMARY"
