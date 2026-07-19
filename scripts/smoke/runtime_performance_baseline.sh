#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLED_ENV_FILE="${NEXUS_CONTROLLED_ENV_FILE:-$ROOT_DIR/deploy/.env.controlled.example}"
CONTROLLED_COMPOSE="$ROOT_DIR/deploy/docker-compose.controlled.yml"
CONTROLLED_POSTGRES_COMPOSE="$ROOT_DIR/deploy/docker-compose.controlled-postgres.yml"
DATABASE_TOPOLOGY="${NEXUS_DATABASE_TOPOLOGY:-external}"
NGINX_CONF="${NGINX_CONF:-$ROOT_DIR/deploy/nginx/default.conf}"
APP_URL="${APP_URL:-}"

fail() {
  echo "[runtime-smoke][fail] $*" >&2
  exit 1
}

info() {
  echo "[runtime-smoke] $*"
}

args=" ${*:-} "
case "$args" in
  *" down "*|*" restart "*|*" rm "*|*" kill "*|*" prune "*)
    fail "destructive docker action detected; this smoke is read-only only"
    ;;
esac

[[ -f "$CONTROLLED_ENV_FILE" ]] || fail "controlled environment file not found: $CONTROLLED_ENV_FILE"
[[ -f "$CONTROLLED_COMPOSE" ]] || fail "controlled compose file not found: $CONTROLLED_COMPOSE"
[[ -f "$NGINX_CONF" ]] || fail "nginx conf not found: $NGINX_CONF"

compose_files=(-f "$CONTROLLED_COMPOSE")
case "$DATABASE_TOPOLOGY" in
  external) ;;
  local)
    [[ -f "$CONTROLLED_POSTGRES_COMPOSE" ]] || fail "controlled PostgreSQL overlay not found"
    compose_files+=(-f "$CONTROLLED_POSTGRES_COMPOSE")
    ;;
  *) fail "NEXUS_DATABASE_TOPOLOGY must be external or local" ;;
esac

info "validating controlled docker compose config"
CONTROLLED_IMAGE="ghcr.io/maximvonshaft/nexus_helpdesk@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
IMAGE_TAG="ghcr.io/maximvonshaft/nexus_helpdesk@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
GIT_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
FRONTEND_BUILD_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
BUILD_TIME="2026-01-01T00:00:00Z" \
APP_VERSION="smoke" \
docker compose --env-file "$CONTROLLED_ENV_FILE" "${compose_files[@]}" config \
  >/tmp/nexusdesk-runtime-compose-config.yml

grep -q "gunicorn app.main:app" /tmp/nexusdesk-runtime-compose-config.yml || fail "app command does not contain gunicorn app.main:app"
grep -q "uvicorn.workers.UvicornWorker" /tmp/nexusdesk-runtime-compose-config.yml || fail "app command does not contain UvicornWorker"
if grep -q "command: uvicorn app.main:app" /tmp/nexusdesk-runtime-compose-config.yml; then
  fail "app command still appears to use single-process uvicorn"
fi

for service in \
  app-controlled \
  worker-outbound-controlled \
  worker-background-controlled \
  worker-webchat-ai-controlled \
  worker-handoff-snapshot-controlled; do
  grep -q "^  ${service}:" /tmp/nexusdesk-runtime-compose-config.yml || fail "controlled service missing: $service"
done

grep -q "WEB_CONCURRENCY" /tmp/nexusdesk-runtime-compose-config.yml || fail "WEB_CONCURRENCY is missing from app environment"
grep -q "WEB_TIMEOUT" /tmp/nexusdesk-runtime-compose-config.yml || fail "WEB_TIMEOUT is missing from app environment"
grep -q "check_worker_progress.py" /tmp/nexusdesk-runtime-compose-config.yml || fail "durable worker progress healthcheck missing"

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
