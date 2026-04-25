#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

need_cmd curl

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: would check /healthz, /readyz, and safe /metrics policy statuses"
  pass "runtime health smoke dry-run"
  exit 0
fi

require_live_api
curl -fsS "${API_URL%/}/healthz" >/tmp/nexusdesk-healthz.json
curl -fsS "${API_URL%/}/readyz" >/tmp/nexusdesk-readyz.json
grep -q 'ok' /tmp/nexusdesk-healthz.json || fail "healthz did not report ok"
grep -q 'ready' /tmp/nexusdesk-readyz.json || fail "readyz did not report ready"
info "metrics endpoint may be disabled or token-protected; probing safely"
STATUS="$(curl -s -o /tmp/nexusdesk-metrics.txt -w '%{http_code}' "${API_URL%/}/metrics" || true)"
case "$STATUS" in
  200|401|403|404|503) pass "metrics policy returned acceptable status $STATUS" ;;
  *) fail "unexpected metrics status: $STATUS" ;;
esac
pass "runtime health smoke"
