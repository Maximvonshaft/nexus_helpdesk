#!/usr/bin/env bash
set -Eeuo pipefail

SMOKE_PREFIX="${NEXUSDESK_SMOKE_PREFIX:-nxd-round-a-$(date +%Y%m%d%H%M%S)}"
SMOKE_MODE="${NEXUSDESK_SMOKE_MODE:-mock}"
OPENCLAW_MOCK_MODE="${OPENCLAW_MOCK_MODE:-1}"
API_URL="${NEXUSDESK_API_URL:-http://127.0.0.1:18081}"
DRY_RUN=0

usage_common() {
  cat <<'USAGE'
Common options:
  --dry-run              Print planned checks without mutating anything.
  --api-url URL          NexusDesk API base URL. Defaults to NEXUSDESK_API_URL or http://127.0.0.1:18081.
  --prefix PREFIX        Unique test-data prefix. Defaults to NEXUSDESK_SMOKE_PREFIX or timestamp.
  --help                 Show help.

Common environment:
  NEXUSDESK_API_URL
  NEXUSDESK_ADMIN_EMAIL
  NEXUSDESK_ADMIN_PASSWORD
  NEXUSDESK_INTEGRATION_CLIENT_ID
  NEXUSDESK_INTEGRATION_CLIENT_KEY
  NEXUSDESK_SMOKE_MODE=mock|live
  NEXUSDESK_SMOKE_PREFIX
  OPENCLAW_MOCK_MODE=1|0
USAGE
}

parse_common_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --api-url) shift; API_URL="${1:?missing --api-url value}" ;;
      --prefix) shift; SMOKE_PREFIX="${1:?missing --prefix value}" ;;
      --help|-h) usage_common; exit 0 ;;
      *) echo "FAIL unknown option: $1" >&2; usage_common; exit 2 ;;
    esac
    shift || true
  done
}

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*" >&2; exit 1; }
skip() { echo "SKIP $*"; exit 0; }
info() { echo "INFO $*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

json_get() {
  python3 -c 'import json,sys; data=json.load(sys.stdin); path=sys.argv[1].split("."); cur=data
for p in path:
    if p == "": continue
    cur = cur[int(p)] if isinstance(cur, list) else cur[p]
print(cur)' "$1"
}

api_get() {
  local path="$1"
  curl -fsS "${API_URL%/}$path"
}

require_live_api() {
  if [ "$DRY_RUN" = "1" ]; then
    info "dry-run: would call live NexusDesk API at $API_URL"
    exit 0
  fi
}

require_env() {
  local missing=0
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "SKIP missing env: $name" >&2
      missing=1
    fi
  done
  [ "$missing" = "0" ] || exit 0
}

ensure_safe_mode() {
  if [ "${NEXUSDESK_SMOKE_MODE:-mock}" = "live" ] && [ "${OPENCLAW_MOCK_MODE:-1}" != "1" ]; then
    info "live mode requested; this script must not send to real customers unless explicitly designed for it"
  fi
}
