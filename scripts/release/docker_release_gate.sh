#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../.." ] && [ -d "$SCRIPT_DIR/../../.git" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d ".git" ]; then
  REPO_ROOT="$(pwd)"
else
  echo "Cannot locate repository root" >&2
  exit 2
fi
cd "$REPO_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.server.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Compose file not found: $COMPOSE_FILE" >&2
  exit 2
fi
if [ "${APP_ENV:-staging}" = "production" ]; then
  echo "Refusing APP_ENV=production" >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT:-$REPO_ROOT/release_evidence_${STAMP}}"
mkdir -p "$OUT/command_outputs"

if [ "${CLEANUP:-0}" = "1" ]; then
  trap 'docker compose -f "$COMPOSE_FILE" --profile edge-nginx down || true' EXIT
fi

{
  echo "UTC: $(date -u)"
  git rev-parse HEAD || true
  git status --short || true
  docker --version
  docker compose version
} 2>&1 | tee "$OUT/command_outputs/11_docker_environment.log"

{
  echo "===== docker compose config validation ====="
  docker compose -f "$COMPOSE_FILE" config --quiet
  echo "===== docker compose build ====="
  docker compose -f "$COMPOSE_FILE" build
  echo "===== alembic upgrade head ====="
  docker compose -f "$COMPOSE_FILE" run --rm app alembic upgrade head
  echo "===== up app and workers ====="
  docker compose -f "$COMPOSE_FILE" up -d app worker-background worker-handoff-snapshot worker-webchat-ai
  docker compose -f "$COMPOSE_FILE" ps
  echo "===== healthz ====="
  curl -fsS http://127.0.0.1:18081/healthz
  echo
  echo "===== readyz ====="
  curl -fsS http://127.0.0.1:18081/readyz
  echo
  echo "===== logs app tail ====="
  docker compose -f "$COMPOSE_FILE" logs --tail=200 app | sed -E 's/(token|secret|password|authorization)([=: ]+)[^ ]+/\1\2[REDACTED]/Ig'
  echo "===== logs handoff worker tail ====="
  docker compose -f "$COMPOSE_FILE" logs --tail=200 worker-handoff-snapshot | sed -E 's/(token|secret|password|authorization)([=: ]+)[^ ]+/\1\2[REDACTED]/Ig'
} 2>&1 | tee "$OUT/command_outputs/11_docker_compose_health.log"

if [ "${RUN_NGINX_SMOKE:-0}" = "1" ]; then
  {
    echo "===== start nginx edge profile ====="
    docker compose -f "$COMPOSE_FILE" --profile edge-nginx up -d nginx
    docker compose -f "$COMPOSE_FILE" ps nginx
    echo "===== nginx widget smoke ====="
    curl -fsS http://127.0.0.1/webchat/widget.js >/tmp/nexusdesk_widget_smoke.js
    wc -c /tmp/nexusdesk_widget_smoke.js
    grep -q "fast-reply" /tmp/nexusdesk_widget_smoke.js
    echo "===== nginx API OPTIONS smoke ====="
    curl -fsS -X OPTIONS http://127.0.0.1/api/webchat/fast-reply -H 'Origin: http://localhost' -i | head -40
  } 2>&1 | tee "$OUT/command_outputs/12_nginx_smoke.log"
fi

echo "Docker release evidence written to: $OUT"
