#!/usr/bin/env bash
set -Eeuo pipefail

# Codex App-Server staging gate evidence collector.
# This script is read-only except for writing a local evidence directory.
# It does not enable production traffic and does not print secret values.

OUT_DIR="${OUT_DIR:-/tmp/codex_app_server_staging_gate_$(date -u '+%Y%m%dT%H%M%SZ')}"
BASE_URL="${BASE_URL:-http://127.0.0.1:18081}"
BRIDGE_URL="${CODEX_APP_SERVER_BRIDGE_URL:-}"
TOKEN_FILE="${CODEX_APP_SERVER_TOKEN_FILE:-}"
PROTOCOL_PATH="${CODEX_APP_SERVER_PROTOCOL_PATH:-/reply}"
TIMEOUT_SECONDS="${CODEX_APP_SERVER_PROBE_TIMEOUT_SECONDS:-8}"

mkdir -p "$OUT_DIR"

write_readme() {
  cat >"$OUT_DIR/README.md" <<EOF
# Codex App-Server Staging Gate Evidence

Time UTC: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
Base URL: $BASE_URL
Bridge URL configured: $([ -n "$BRIDGE_URL" ] && echo yes || echo no)
Token file configured: $([ -n "$TOKEN_FILE" ] && echo yes || echo no)
Protocol path: $PROTOCOL_PATH

No secret values are written by this script.
EOF
}

json_payload() {
  cat <<'JSON'
{
  "request_id": "staging-contract-001",
  "tenant_key": "default",
  "channel_key": "website",
  "session_id": "staging-session-001",
  "body": "Where is my parcel?",
  "recent_context": [],
  "tracking_fact_summary": null,
  "tracking_fact_evidence_present": false,
  "strict_schema": "speedaf_webchat_fast_reply_v1"
}
JSON
}

write_runtime_status() {
  {
    echo "# Runtime baseline"
    echo
    echo "## /healthz"
    curl -fsS "$BASE_URL/healthz" || true
    echo
    echo
    echo "## /readyz"
    curl -fsS "$BASE_URL/readyz" || true
    echo
  } >"$OUT_DIR/provider_runtime_status_sanitized.json"
}

write_contract_probe() {
  json_payload >"$OUT_DIR/contract_probe_request_sanitized.json"

  if [ -z "$BRIDGE_URL" ]; then
    cat >"$OUT_DIR/contract_probe_response_sanitized.json" <<'EOF'
{"status":"SKIPPED","reason":"CODEX_APP_SERVER_BRIDGE_URL is not configured"}
EOF
    return 0
  fi

  AUTH_HEADER_ARGS=()
  if [ -n "$TOKEN_FILE" ] && [ -f "$TOKEN_FILE" ]; then
    TOKEN="$(head -n 1 "$TOKEN_FILE" | tr -d '\r\n')"
    if [ -n "$TOKEN" ]; then
      AUTH_HEADER_ARGS=(-H "Authorization: Bearer ${TOKEN}")
    fi
  fi

  set +e
  curl -fsS \
    --max-time "$TIMEOUT_SECONDS" \
    -H 'Content-Type: application/json' \
    "${AUTH_HEADER_ARGS[@]}" \
    -d "$(json_payload)" \
    "$BRIDGE_URL$PROTOCOL_PATH" \
    >"$OUT_DIR/contract_probe_response_sanitized.json" \
    2>"$OUT_DIR/contract_probe_error_sanitized.txt"
  rc=$?
  set -e

  echo "$rc" >"$OUT_DIR/contract_probe_exit_code.txt"
}

write_failure_matrix() {
  cat >"$OUT_DIR/failure_matrix.tsv" <<'EOF'
case	expected	observed	pass
missing_auth	reject/no_secret_leak	manual_required	TBD
invalid_auth	reject/no_secret_leak	manual_required	TBD
connect_timeout	fail_closed_fallback_available	manual_required	TBD
read_timeout	fail_closed_fallback_available	manual_required	TBD
http_4xx	controlled_provider_error	manual_required	TBD
http_5xx	controlled_provider_error	manual_required	TBD
invalid_json	strict_parser_rejects	manual_required	TBD
missing_strict_key	strict_parser_rejects	manual_required	TBD
extra_unsafe_action_key	reject_or_documented_drop	manual_required	TBD
handoff_true_without_reason	strict_parser_rejects	manual_required	TBD
oversized_reply	reject_or_safe_policy	manual_required	TBD
customer_pii_in_logs	fail_gate	manual_required	TBD
secret_in_logs	fail_gate	manual_required	TBD
EOF
}

write_logs_placeholder() {
  cat >"$OUT_DIR/app_logs_tail_sanitized.txt" <<'EOF'
Collect sanitized staging app logs separately. Do not include tokens, Authorization headers, cookies, or real customer text.
EOF
  cat >"$OUT_DIR/nginx_or_proxy_logs_tail_sanitized.txt" <<'EOF'
Collect sanitized staging proxy logs separately. Do not include tokens, Authorization headers, cookies, or real customer text.
EOF
}

write_verdict() {
  response_file="$OUT_DIR/contract_probe_response_sanitized.json"
  matrix_file="$OUT_DIR/failure_matrix.tsv"

  if grep -q '"status":"SKIPPED"' "$response_file" 2>/dev/null; then
    echo "CODEX_APP_SERVER_STAGING_GATE=FAIL" >"$OUT_DIR/final_verdict.txt"
    echo "Reason: bridge URL not configured" >>"$OUT_DIR/final_verdict.txt"
    return 0
  fi

  if grep -q $'\tTBD$' "$matrix_file" 2>/dev/null; then
    echo "CODEX_APP_SERVER_STAGING_GATE=FAIL" >"$OUT_DIR/final_verdict.txt"
    echo "Reason: failure matrix still contains TBD entries" >>"$OUT_DIR/final_verdict.txt"
    return 0
  fi

  echo "CODEX_APP_SERVER_STAGING_GATE=PASS" >"$OUT_DIR/final_verdict.txt"
}

write_readme
write_runtime_status
write_contract_probe
write_failure_matrix
write_logs_placeholder
write_verdict

cat "$OUT_DIR/final_verdict.txt"
echo "EVIDENCE_DIR=$OUT_DIR"
