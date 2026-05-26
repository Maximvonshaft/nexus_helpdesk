#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TENANT_KEY="${TENANT_KEY:-default}"
CHANNEL_KEY="${CHANNEL_KEY:-website}"
BRAND_NAME="${BRAND_NAME:-猴王山}"
ASSISTANT_NAME="${ASSISTANT_NAME:-猴王山 AI 客服}"
PUBLISH_TEMP_PERSONA="${PUBLISH_TEMP_PERSONA:-0}"
NEXUS_API_TOKEN="${NEXUS_API_TOKEN:-${API_TOKEN:-}}"
SESSION_ID="${SESSION_ID:-persona_identity_$(date +%s)_$RANDOM}"
LOG_DIR="${LOG_DIR:-artifacts/persona_identity_probe}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

mkdir -p "$LOG_DIR"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_$RANDOM"
LOG_FILE="$LOG_DIR/persona_identity_$RUN_ID.log"
JSONL_FILE="$LOG_DIR/persona_identity_$RUN_ID.jsonl"
SUMMARY_JSON="$LOG_DIR/persona_identity_$RUN_ID.summary.json"
SUMMARY_TSV="$LOG_DIR/persona_identity_$RUN_ID.summary.tsv"
PROFILE_ID=""

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

api_json() {
  local method="$1"
  local path="$2"
  local payload="$3"
  local output="$4"
  local headers=(-H 'Content-Type: application/json')
  if [[ -n "$NEXUS_API_TOKEN" ]]; then
    headers+=(-H "Authorization: Bearer $NEXUS_API_TOKEN")
  fi
  curl -fsS -X "$method" "${BASE_URL%/}$path" "${headers[@]}" --data "$payload" -o "$output"
}

json_value() {
  local file="$1"
  local key="$2"
  "$PYTHON_BIN" - "$file" "$key" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
value = data
for part in sys.argv[2].split("."):
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

cleanup() {
  if [[ -n "$PROFILE_ID" && "$PUBLISH_TEMP_PERSONA" == "1" && -n "$NEXUS_API_TOKEN" ]]; then
    log "Restoring probe state by disabling temporary Persona profile $PROFILE_ID"
    api_json PATCH "/api/persona-profiles/$PROFILE_ID" '{"is_active":false}' "$LOG_DIR/persona_identity_$RUN_ID.restore.json" || true
  fi
}
trap cleanup EXIT

if [[ "$PUBLISH_TEMP_PERSONA" == "1" ]]; then
  if [[ -z "$NEXUS_API_TOKEN" ]]; then
    log "NEXUS_API_TOKEN is required when PUBLISH_TEMP_PERSONA=1"
    exit 2
  fi
  PROFILE_KEY="000.identity.probe.$(date +%s).$RANDOM"
  CREATE_PAYLOAD="$("$PYTHON_BIN" - "$PROFILE_KEY" "$CHANNEL_KEY" "$BRAND_NAME" "$ASSISTANT_NAME" <<'PY'
import json
import sys

profile_key, channel, brand, assistant = sys.argv[1:5]
print(json.dumps({
    "profile_key": profile_key,
    "name": "Persona identity probe",
    "description": None,
    "channel": channel,
    "language": None,
    "is_active": True,
    "draft_summary": "Temporary Persona identity probe.",
    "draft_content_json": {
        "brand_name": brand,
        "assistant_name": assistant,
        "role_label": "AI 客服",
        "identity_statement": f"我是{brand}的{assistant}，可以协助处理客户服务问题。",
        "identity_answer_rule": "身份问题只按本 Persona 的品牌和助手名称回答。",
        "capabilities": ["回答常见问题", "收集必要信息", "需要人工处理时转接客服"],
        "disallowed_identity_claims": [],
        "handoff_boundary": "缺少事实证据或需要人工处理时转人工。"
    }
}, ensure_ascii=False))
PY
)"
  CREATE_OUT="$LOG_DIR/persona_identity_$RUN_ID.create.json"
  PUBLISH_OUT="$LOG_DIR/persona_identity_$RUN_ID.publish.json"
  log "Creating temporary Persona $PROFILE_KEY"
  api_json POST "/api/persona-profiles" "$CREATE_PAYLOAD" "$CREATE_OUT"
  PROFILE_ID="$(json_value "$CREATE_OUT" id)"
  log "Publishing temporary Persona profile $PROFILE_ID"
  api_json POST "/api/persona-profiles/$PROFILE_ID/publish" '{"notes":"persona identity e2e probe"}' "$PUBLISH_OUT"
fi

printf 'question\tok\treply\n' > "$SUMMARY_TSV"
QUESTIONS=("你是谁" "你是什么客服" "你是哪里的客服" "你是否是猴王山的客服")

idx=0
for question in "${QUESTIONS[@]}"; do
  idx=$((idx + 1))
  CLIENT_MESSAGE_ID="persona_identity_${RUN_ID}_$idx"
  REQUEST_PAYLOAD="$("$PYTHON_BIN" - "$TENANT_KEY" "$CHANNEL_KEY" "${SESSION_ID}_$idx" "$CLIENT_MESSAGE_ID" "$question" <<'PY'
import json
import sys

tenant, channel, session_id, client_message_id, body = sys.argv[1:6]
print(json.dumps({
    "tenant_key": tenant,
    "channel_key": channel,
    "session_id": session_id,
    "client_message_id": client_message_id,
    "body": body,
    "recent_context": []
}, ensure_ascii=False))
PY
)"
  RESPONSE_FILE="$LOG_DIR/persona_identity_$RUN_ID.reply_$idx.json"
  log "Probing identity question: $question"
  api_json POST "/api/webchat/fast-reply" "$REQUEST_PAYLOAD" "$RESPONSE_FILE"
  "$PYTHON_BIN" - "$RESPONSE_FILE" "$question" "$BRAND_NAME" "$ASSISTANT_NAME" "$JSONL_FILE" "$SUMMARY_TSV" <<'PY'
import json
import sys

response_file, question, brand, assistant, jsonl_file, tsv_file = sys.argv[1:7]
with open(response_file, encoding="utf-8") as fh:
    data = json.load(fh)
reply = str(data.get("reply") or data.get("customer_reply") or "")
ok = bool(data.get("ok", bool(reply)))
expected = [item for item in (brand, assistant) if item]
if expected and not any(item in reply for item in expected):
    raise SystemExit(f"reply does not contain configured identity: {reply}")
allows_nexusdesk = brand.strip().lower() == "nexusdesk" or assistant.strip().lower() == "nexusdesk"
if not allows_nexusdesk and "NexusDesk" in reply:
    raise SystemExit(f"reply leaked NexusDesk: {reply}")
if brand and f"不是{brand}" in reply:
    raise SystemExit(f"reply denied configured brand: {reply}")
row = {"question": question, "ok": ok, "reply": reply, "raw": data}
with open(jsonl_file, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
safe_reply = reply.replace("\t", " ").replace("\n", " ")
with open(tsv_file, "a", encoding="utf-8") as fh:
    fh.write(f"{question}\t{str(ok).lower()}\t{safe_reply}\n")
PY
done

"$PYTHON_BIN" - "$JSONL_FILE" "$SUMMARY_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    rows = [json.loads(line) for line in fh if line.strip()]
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump({"ok": True, "count": len(rows), "results": rows}, fh, ensure_ascii=False, indent=2)
PY

log "Persona identity probe passed"
log "Log: $LOG_FILE"
log "JSON summary: $SUMMARY_JSON"
log "TSV summary: $SUMMARY_TSV"
