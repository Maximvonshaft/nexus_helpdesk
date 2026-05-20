#!/usr/bin/env bash
set -Eeuo pipefail

# Official OpenClaw Gateway staging gate evidence collector.
# Read-only except for writing a local evidence directory.
# Does not print token values.

OUT_DIR="${OUT_DIR:-/tmp/openclaw_gateway_staging_gate_$(date -u '+%Y%m%dT%H%M%SZ')}"
OPENCLAW_BASE_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}"
OPENCLAW_GATEWAY_TOKEN_FILE="${OPENCLAW_GATEWAY_TOKEN_FILE:-}"
NEXUS_BASE_URL="${NEXUS_BASE_URL:-http://127.0.0.1:18081}"
MODEL_ID="${OPENCLAW_MODEL_ID:-openclaw/default}"
TIMEOUT_SECONDS="${OPENCLAW_GATEWAY_PROBE_TIMEOUT_SECONDS:-10}"

mkdir -p "$OUT_DIR"

TOKEN=""
if [ -n "$OPENCLAW_GATEWAY_TOKEN_FILE" ] && [ -f "$OPENCLAW_GATEWAY_TOKEN_FILE" ]; then
  TOKEN="$(head -n 1 "$OPENCLAW_GATEWAY_TOKEN_FILE" | tr -d '\r\n')"
fi

AUTH_HEADER_ARGS=()
if [ -n "$TOKEN" ]; then
  AUTH_HEADER_ARGS=(-H "Authorization: Bearer ${TOKEN}")
fi

cat >"$OUT_DIR/README.md" <<EOF
# OpenClaw Gateway Staging Gate Evidence

Time UTC: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
OpenClaw base URL: $OPENCLAW_BASE_URL
Nexus base URL: $NEXUS_BASE_URL
Model ID: $MODEL_ID
Token file configured: $([ -n "$OPENCLAW_GATEWAY_TOKEN_FILE" ] && echo yes || echo no)

No token value is written by this script.
EOF

cat >"$OUT_DIR/openclaw_responses_request_sanitized.json" <<EOF
{
  "model": "$MODEL_ID",
  "input": "You are a reply-only logistics customer support provider for Nexus. Return only valid JSON with exactly these keys: reply, intent, tracking_number, handoff_required, handoff_reason, recommended_agent_action. Customer message: Where is my parcel?",
  "temperature": 0
}
EOF

cat >"$OUT_DIR/openclaw_chat_completions_request_sanitized.json" <<EOF
{
  "model": "$MODEL_ID",
  "messages": [
    {
      "role": "system",
      "content": "You are a reply-only logistics customer support provider for Nexus. Return only valid JSON with exactly these keys: reply, intent, tracking_number, handoff_required, handoff_reason, recommended_agent_action. No markdown. No tool calls."
    },
    {
      "role": "user",
      "content": "Where is my parcel?"
    }
  ],
  "temperature": 0
}
EOF

{
  echo "# Nexus baseline"
  echo
  echo "## /healthz"
  curl -fsS --max-time "$TIMEOUT_SECONDS" "$NEXUS_BASE_URL/healthz" || true
  echo
  echo
  echo "## /readyz"
  curl -fsS --max-time "$TIMEOUT_SECONDS" "$NEXUS_BASE_URL/readyz" || true
  echo
} >"$OUT_DIR/nexus_runtime_baseline_sanitized.txt"

set +e
openclaw gateway status --json >"$OUT_DIR/openclaw_gateway_status_sanitized.json" 2>"$OUT_DIR/openclaw_gateway_status_error_sanitized.txt"
status_rc=$?
openclaw security audit --deep --json >"$OUT_DIR/openclaw_security_audit_sanitized.json" 2>"$OUT_DIR/openclaw_security_audit_error_sanitized.txt"
audit_rc=$?
set -e

echo "$status_rc" >"$OUT_DIR/openclaw_gateway_status_exit_code.txt"
echo "$audit_rc" >"$OUT_DIR/openclaw_security_audit_exit_code.txt"

set +e
curl -fsS --max-time "$TIMEOUT_SECONDS" \
  "${AUTH_HEADER_ARGS[@]}" \
  "$OPENCLAW_BASE_URL/v1/models" \
  >"$OUT_DIR/openclaw_models_sanitized.json" \
  2>"$OUT_DIR/openclaw_models_error_sanitized.txt"
models_rc=$?
set -e

echo "$models_rc" >"$OUT_DIR/openclaw_models_exit_code.txt"

set +e
curl -fsS --max-time "$TIMEOUT_SECONDS" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER_ARGS[@]}" \
  -d @"$OUT_DIR/openclaw_responses_request_sanitized.json" \
  "$OPENCLAW_BASE_URL/v1/responses" \
  >"$OUT_DIR/openclaw_responses_response_sanitized.json" \
  2>"$OUT_DIR/openclaw_responses_error_sanitized.txt"
responses_rc=$?
set -e

echo "$responses_rc" >"$OUT_DIR/openclaw_responses_exit_code.txt"

set +e
curl -fsS --max-time "$TIMEOUT_SECONDS" \
  -H "Content-Type: application/json" \
  "${AUTH_HEADER_ARGS[@]}" \
  -d @"$OUT_DIR/openclaw_chat_completions_request_sanitized.json" \
  "$OPENCLAW_BASE_URL/v1/chat/completions" \
  >"$OUT_DIR/openclaw_chat_completions_response_sanitized.json" \
  2>"$OUT_DIR/openclaw_chat_completions_error_sanitized.txt"
chat_rc=$?
set -e

echo "$chat_rc" >"$OUT_DIR/openclaw_chat_completions_exit_code.txt"

cat >"$OUT_DIR/failure_matrix.tsv" <<'EOF'
case	expected	observed	pass
missing_token	gateway_rejects_or_private_loopback_only	manual_required	TBD
invalid_token	gateway_rejects	manual_required	TBD
gateway_down	provider_fail_closed_fallback_available	manual_required	TBD
timeout	provider_fail_closed	manual_required	TBD
invalid_json	strict_parser_rejects	manual_required	TBD
extra_unsafe_keys	strict_parser_rejects	manual_required	TBD
tool_call_emitted	fail_gate	manual_required	TBD
filesystem_shell_browser_access_possible	fail_gate	manual_required	TBD
secret_or_customer_pii_in_logs	fail_gate	manual_required	TBD
EOF

cat >"$OUT_DIR/nexus_strict_parser_result.json" <<'EOF'
{"status":"NOT_RUN","reason":"Wire the selected OpenClaw response text into Nexus strict parser during staging implementation."}
EOF

cat >"$OUT_DIR/logs_tail_sanitized.txt" <<'EOF'
Collect sanitized OpenClaw/Nexus staging logs separately. Do not include tokens, Authorization headers, cookies, or real customer text.
EOF

verdict="FAIL"
reason=""
if [ "$models_rc" -ne 0 ]; then
  reason="/v1/models probe failed"
elif [ "$responses_rc" -ne 0 ] && [ "$chat_rc" -ne 0 ]; then
  reason="both /v1/responses and /v1/chat/completions probes failed"
elif grep -q $'\tTBD$' "$OUT_DIR/failure_matrix.tsv"; then
  reason="failure matrix still contains TBD entries"
else
  verdict="PASS"
fi

if [ "$verdict" = "PASS" ]; then
  echo "OPENCLAW_GATEWAY_STAGING_GATE=PASS" >"$OUT_DIR/final_verdict.txt"
else
  echo "OPENCLAW_GATEWAY_STAGING_GATE=FAIL" >"$OUT_DIR/final_verdict.txt"
  echo "Reason: $reason" >>"$OUT_DIR/final_verdict.txt"
fi

cat "$OUT_DIR/final_verdict.txt"
echo "EVIDENCE_DIR=$OUT_DIR"
