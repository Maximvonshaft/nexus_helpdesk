#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deploy/docker-compose.server.yml}"
NGINX_CONF="${NGINX_CONF:-$ROOT_DIR/deploy/nginx/default.conf}"
APP_URL="${APP_URL:-}"

fail() {
  echo "[runtime-smoke][fail] $*" >&2
  exit 1
}

info() {
  echo "[runtime-smoke] $*"
}

case " ${*:-} " in
  *" down "*|*" restart "*|*" rm "*|*" kill "*|*|*" prune "*)
    fail "destructive docker action detected; this smoke is read-only only"
    ;;
esac

[[ -f "$COMPOSE_FILE" ]] || fail "compose file not found: $COMPOSE_FILE"
[[ -f "$NGINX_CONF" ]] || fail "nginx conf not found: $NGINX_CONF"

info "validating docker compose config"
docker compose -f "$COMPOSE_FILE" config >/tmp/nexusdesk-runtime-compose-config.yml

grep -q "gunicorn app.main:app" /tmp/nexusdesk-runtime-compose-config.yml || fail "app command does not contain gunicorn app.main:app"
grep -q "uvicorn.workers.UvicornWorker" /tmp/nexusdesk-runtime-compose-config.yml || fail "app command does not contain UvicornWorker"
if awk '/app:/{flag=1} flag && /command: uvicorn app.main:app/{found=1} END{exit found?0:1}' /tmp/nexusdesk-runtime-compose-config.yml; then
  fail "app command still appears to use single-process uvicorn"
fi

grep -q "WEB_CONCURRENCY" /tmp/nexusdesk-runtime-compose-config.yml || fail "WEB_CONCURRENCY is missing from app environment"
grep -q "WEB_TIMEOUT" /tmp/nexusdesk-runtime-compose-config.yml || fail "WEB_TIMEOUT is missing from app environment"

info "checking nginx cache/gzip/keepalive policy"
grep -q "gzip on" "$NGINX_CONF" || fail "gzip is not enabled"
grep -q "keepalive 32" "$NGINX_CONF" || fail "upstream keepalive missing"
grep -q "public, max-age=31536000, immutable" "$NGINX_CONF" || fail "immutable assets cache policy missing"
grep -q "no-store" "$NGINX_CONF" || fail "api no-store cache policy missing"
grep -q "no-cache" "$NGINX_CONF" || fail "html/spa no-cache policy missing"

if [[ -n "$APP_URL" ]]; then
  info "probing readyz at $APP_URL/readyz"
  curl -fsS --max-time 5 "$APP_URL/readyz" >/tmp/nexusdesk-readyz.json || fail "readyz probe failed"
fi

info "runtime performance baseline smoke passed"
