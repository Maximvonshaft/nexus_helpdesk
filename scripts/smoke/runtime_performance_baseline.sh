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

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
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
docker compose --env-file "$CONTROLLED_ENV_FILE" "${compose_files[@]}" config --format json \
  >/tmp/nexusdesk-runtime-compose-config.json

python3 - /tmp/nexusdesk-runtime-compose-config.json <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
services = payload.get("services") or {}
required = {
    "app-controlled",
    "worker-outbound-controlled",
    "worker-background-controlled",
    "worker-webchat-ai-controlled",
}
missing = sorted(required - set(services))
if missing:
    raise SystemExit("controlled services missing: " + ",".join(missing))

expected_queues = {
    "worker-outbound-controlled": "outbound",
    "worker-background-controlled": "background",
    "worker-webchat-ai-controlled": "webchat-ai",
}
for service_name, queue in expected_queues.items():
    service = services[service_name]
    command = [str(token) for token in service.get("command") or []]
    if "scripts/run_worker_supervised.py" not in command:
        raise SystemExit(f"supervised worker command missing: {service_name}")
    if "--queue" not in command:
        raise SystemExit(f"worker queue missing: {service_name}")
    index = command.index("--queue")
    if index + 1 >= len(command) or command[index + 1] != queue:
        raise SystemExit(f"worker queue mismatch: {service_name}")
    healthcheck = json.dumps(service.get("healthcheck") or {}, sort_keys=True)
    if "scripts/check_worker_progress.py" not in healthcheck:
        raise SystemExit(f"durable progress healthcheck missing: {service_name}")

app_command = [str(token) for token in services["app-controlled"].get("command") or []]
for token in ("gunicorn", "app.main:app", "uvicorn.workers.UvicornWorker", "--workers", "--timeout"):
    if token not in app_command:
        raise SystemExit(f"app runtime token missing: {token}")
if "uvicorn" in app_command and "gunicorn" not in app_command:
    raise SystemExit("single-process uvicorn is not the controlled runtime")
PY

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
