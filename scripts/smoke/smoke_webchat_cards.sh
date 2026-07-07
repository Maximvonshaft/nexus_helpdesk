#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

if [ "$DRY_RUN" = "1" ]; then
  info "dry-run: would verify the public WebChat demo/card entry stays reachable without private provider access"
  pass "webchat cards smoke dry-run"
  exit 0
fi

need_cmd curl
require_live_api

STATUS="$(curl -s -o /tmp/nexusdesk-webchat-demo.html -w '%{http_code}' "${API_URL%/}/webchat/demo/" || true)"
case "$STATUS" in
  200) ;;
  *) fail "webchat demo returned unexpected status: $STATUS" ;;
esac

grep -Eiq 'webchat|nexus' /tmp/nexusdesk-webchat-demo.html || fail "webchat demo did not include expected public entry markers"
pass "webchat cards smoke"
