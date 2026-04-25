#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"
parse_common_args "$@"

SCRIPTS=(
  smoke_e2e_outbound_safety.sh
  smoke_e2e_openclaw_inbound_event.sh
  smoke_e2e_transcript_sync.sh
  smoke_e2e_same_route_reply.sh
  smoke_e2e_unresolved_event_replay.sh
  smoke_e2e_attachment_persist.sh
  smoke_e2e_runtime_health.sh
  smoke_e2e_integration_task.sh
)

FAILURES=0
SKIPS=0
PASSES=0

for script in "${SCRIPTS[@]}"; do
  echo
  echo "===== $script ====="
  if [ ! -x "$SCRIPT_DIR/$script" ]; then
    chmod +x "$SCRIPT_DIR/$script" 2>/dev/null || true
  fi
  set +e
  if [ "$DRY_RUN" = "1" ]; then
    bash "$SCRIPT_DIR/$script" --dry-run --api-url "$API_URL" --prefix "$SMOKE_PREFIX"
  else
    bash "$SCRIPT_DIR/$script" --api-url "$API_URL" --prefix "$SMOKE_PREFIX"
  fi
  code=$?
  set -e
  case "$code" in
    0) PASSES=$((PASSES+1)) ;;
    "$SKIP_EXIT_CODE") SKIPS=$((SKIPS+1)) ;;
    *) FAILURES=$((FAILURES+1)); echo "FAIL $script exited $code" >&2 ;;
  esac
done

echo
echo "===== ROUND A SMOKE SUMMARY ====="
echo "PASS_COUNT=$PASSES"
echo "SKIP_COUNT=$SKIPS"
echo "FAIL_COUNT=$FAILURES"
[ "$FAILURES" = "0" ] || exit 1
pass "round-a aggregate smoke"
