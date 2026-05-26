#!/usr/bin/env bash
# CI trigger: ready-for-review event did not create workflow runs; this no-op comment forces pull_request synchronize.
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

api_multipart() {
  local method="$1"
  local path="$2"
  shift 2
  local headers=()
  if [[ -n "$NEXUS_API_TOKEN" ]]; then
    headers+=(-H "Authorization: Bearer $NEXUS_API_TOKEN")
  fi
  curl -fsS -X "$method" "${BASE_URL%/}$path" "${headers[@]}" "$@"
}

restore_persona() {
  if [[ "$PUBLISH_TEMP_PERSONA" != "1" || -z "$PROFILE_ID" || -z "$NEXUS_API_TOKEN" ]]; then
    return 0
  fi
  log "Temp persona created; rollback is not automatic because API rollback version is tenant data. Review profile_id=$PROFILE_ID if cleanup is required."
}
trap restore_persona EXIT

publish_temp_persona() {
  if [[ "$PUBLISH_TEMP_PERSONA" != "1" ]]; then
    return 0
  fi
  if [[ -z "$NEXUS_API_TOKEN" ]]; then
    log "PUBLISH_TEMP_PERSONA=1 requires NEXUS_API_TOKEN/API_TOKEN"
    exit 2
  fi
  local payload
  payload="$($PYTHON_BIN - <<PY
import json, os, time
brand=os.environ.get('BRAND_NAME', '猴王山')
assistant=os.environ.get('ASSISTANT_NAME', f'{brand} AI 客服')
key='probe.identity.' + str(int(time.time()))
print(json.dumps({
    'profile_key': key,
    'name': assistant,
    'channel': os.environ.get('CHANNEL_KEY', 'website'),
    'language': None,
    'is_active': True,
    'draft_summary': f'{assistant} identity probe persona.',
    'draft_content_json': {
        'brand_name': brand,
        'assistant_name': assistant,
        'role_label': 'AI 客服',
        'identity_statement': f'我是{assistant}，可以协助处理订单、物流、售后和转人工。',
        'identity_answer_rule': f'客户询问身份时，必须明确回答自己是{assistant}。',
        'capabilities': ['订单咨询', '物流咨询', '售后问题记录', '联系方式说明', '必要时转人工'],
        'disallowed_identity_claims': ['NexusDesk', '不是' + brand + '客服'],
    }
}, ensure_ascii=False))
PY
)"
  local create_out="$LOG_DIR/create_persona_$RUN_ID.json"
  api_json POST /api/persona-profiles "$payload" "$create_out"
  PROFILE_ID="$($PYTHON_BIN - <<PY
import json
print(json.load(open('$create_out', encoding='utf-8'))['id'])
PY
)"
  api_json POST "/api/persona-profiles/$PROFILE_ID/publish" '{"notes":"persona identity e2e probe"}' "$LOG_DIR/publish_persona_$RUN_ID.json"
  log "Published temp persona profile_id=$PROFILE_ID"
}

publish_temp_persona

QUESTIONS=("你是谁" "你是什么客服" "你是哪里的客服" "你是否是${BRAND_NAME}的客服")
FAIL=0
: > "$JSONL_FILE"
printf 'question\thttp_status\tidentity_ok\tno_nexusdesk\tno_negative\treply_source\treply\n' > "$SUMMARY_TSV"

for idx in "${!QUESTIONS[@]}"; do
  q="${QUESTIONS[$idx]}"
  req="$LOG_DIR/request_${RUN_ID}_${idx}.json"
  resp="$LOG_DIR/response_${RUN_ID}_${idx}.json"
  status_file="$LOG_DIR/status_${RUN_ID}_${idx}.txt"
  $PYTHON_BIN - "$req" "$q" "$SESSION_ID" "$idx" <<'PY'
import json, sys
path, body, session_id, idx = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
open(path, 'w', encoding='utf-8').write(json.dumps({
    'tenant_key': 'default',
    'channel_key': 'website',
    'session_id': f'{session_id}_{idx}',
    'client_message_id': f'{session_id}_msg_{idx}',
    'body': body,
    'recent_context': [],
    'visitor': {'name': 'Persona Identity Probe'},
}, ensure_ascii=False))
PY
  code="$(curl -sS -o "$resp" -w '%{http_code}' -X POST "${BASE_URL%/}/api/webchat/fast-reply" -H 'Content-Type: application/json' --data-binary "@$req" || true)"
  printf '%s' "$code" > "$status_file"
  if ! $PYTHON_BIN - "$resp" "$code" "$q" "$BRAND_NAME" "$ASSISTANT_NAME" "$JSONL_FILE" "$SUMMARY_TSV" <<'PY'
import json, sys
from pathlib import Path
resp, code, question, brand, assistant, jsonl, tsv = sys.argv[1:]
try:
    data = json.loads(Path(resp).read_text(encoding='utf-8'))
except Exception as exc:
    data = {'ok': False, 'error_code': 'response_not_json', 'reply': str(exc)}
reply = str(data.get('reply') or data.get('customer_reply') or '')
identity_ok = (brand in reply) or (assistant in reply)
no_nexusdesk = 'NexusDesk' not in reply
no_negative = ('不是' not in reply) and ('无法代表' not in reply) and ('不能代表' not in reply)
row = {
    'question': question,
    'http_status': code,
    'identity_ok': identity_ok,
    'no_nexusdesk': no_nexusdesk,
    'no_negative': no_negative,
    'reply_source': data.get('reply_source'),
    'ai_generated': data.get('ai_generated'),
    'error_code': data.get('error_code'),
    'reply': reply,
}
with open(jsonl, 'a', encoding='utf-8') as f:
    f.write(json.dumps(row, ensure_ascii=False) + '\n')
with open(tsv, 'a', encoding='utf-8') as f:
    safe_reply = reply.replace('\t', ' ').replace('\n', ' ')
    f.write(f"{question}\t{code}\t{identity_ok}\t{no_nexusdesk}\t{no_negative}\t{data.get('reply_source')}\t{safe_reply}\n")
print(json.dumps(row, ensure_ascii=False))
if code != '200' or not identity_ok or not no_nexusdesk or not no_negative:
    raise SystemExit(1)
PY
  then
    FAIL=1
  fi
done

$PYTHON_BIN - "$JSONL_FILE" "$SUMMARY_JSON" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding='utf-8') if line.strip()]
summary = {
    'total': len(rows),
    'passed': sum(1 for r in rows if r['http_status'] == '200' and r['identity_ok'] and r['no_nexusdesk'] and r['no_negative']),
    'rows': rows,
}
open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

log "summary_json=$SUMMARY_JSON"
log "summary_tsv=$SUMMARY_TSV"
if [[ "$FAIL" != "0" ]]; then
  log "PERSONA_IDENTITY_E2E_FAILED"
  exit 1
fi
log "PERSONA_IDENTITY_E2E_OK"
