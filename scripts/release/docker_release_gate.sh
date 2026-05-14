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

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAMP_SAFE="$(printf '%s' "$STAMP" | tr '[:upper:]' '[:lower:]')"
OUT="${OUT:-$REPO_ROOT/release_evidence_${STAMP}}"
mkdir -p "$OUT/command_outputs"
RUN_STACK="${DOCKER_GATE_RUN_STACK:-0}"
CONFIRM="${DOCKER_GATE_CONFIRM:-}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nexusdesk_release_gate_${STAMP_SAFE}}"
APP_PORT="${DOCKER_GATE_APP_PORT:-18082}"
NGINX_PORT="${DOCKER_GATE_NGINX_PORT:-18080}"
POSTGRES_USER_GATE="${DOCKER_GATE_POSTGRES_USER:-nexusdesk_gate}"
POSTGRES_PASSWORD_GATE="${DOCKER_GATE_POSTGRES_PASSWORD:-nexusdesk_gate_password}"
POSTGRES_DB_GATE="${DOCKER_GATE_POSTGRES_DB:-nexusdesk_release_gate}"
DATABASE_URL_GATE="postgresql+psycopg://${POSTGRES_USER_GATE}:${POSTGRES_PASSWORD_GATE}@postgres:5432/${POSTGRES_DB_GATE}"
TMP_DIR="$(mktemp -d)"
GATE_COMPOSE_FILE="$TMP_DIR/docker-compose.server.gate.yml"
OVERRIDE_FILE="$TMP_DIR/docker-release-gate.override.yml"
RAW_CONFIG="$TMP_DIR/docker-resolved-config.raw.yml"
RESOLVED_CONFIG="$OUT/command_outputs/11_docker_resolved_config.redacted.yml"
SANITIZE='s/(token|secret|password|authorization|cookie|api[_-]?key|database_url)([=: ]+)[^ ]+/\1\2[REDACTED]/Ig'

cleanup() {
  if [ "${CLEANUP:-1}" = "1" ] && [ "$RUN_STACK" = "1" ]; then
    COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" --profile edge-nginx down --remove-orphans || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

write_gate_compose() {
  sed \
    -e "s#127\.0\.0\.1:18081:8080#127.0.0.1:${APP_PORT}:8080#g" \
    -e "s#\"80:80\"#\"127.0.0.1:${NGINX_PORT}:80\"#g" \
    "$COMPOSE_FILE" > "$GATE_COMPOSE_FILE"
}

write_override() {
  cat > "$OVERRIDE_FILE" <<YAML
services:
  postgres:
    environment:
      POSTGRES_USER: ${POSTGRES_USER_GATE}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD_GATE}
      POSTGRES_DB: ${POSTGRES_DB_GATE}
  app:
    environment:
      APP_ENV: staging
      AUTO_INIT_DB: "false"
      SEED_DEMO_DATA: "false"
      DATABASE_URL: ${DATABASE_URL_GATE}
  worker-outbound:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  worker-background:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  worker-handoff-snapshot:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  worker-webchat-ai:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  worker-openclaw-inbound:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  legacy-worker:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  sync-daemon:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
  event-daemon:
    environment:
      APP_ENV: staging
      DATABASE_URL: ${DATABASE_URL_GATE}
YAML
}

write_gate_compose
write_override

{
  echo "UTC: $(date -u)"
  git rev-parse HEAD || true
  git status --short || true
  docker --version
  docker compose version
  echo "COMPOSE_FILE=$COMPOSE_FILE"
  echo "GATE_COMPOSE_FILE=$GATE_COMPOSE_FILE"
  echo "COMPOSE_PROJECT_NAME=$PROJECT_NAME"
  echo "DOCKER_GATE_RUN_STACK=$RUN_STACK"
  echo "DOCKER_GATE_APP_PORT=$APP_PORT"
  echo "DOCKER_GATE_NGINX_PORT=$NGINX_PORT"
  echo "Docker gate DATABASE_URL is forced to the isolated compose postgres service and intentionally not printed."
} 2>&1 | tee "$OUT/command_outputs/11_docker_environment.log"

{
  echo "===== docker compose config validation ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" config --quiet
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" config > "$RAW_CONFIG"
  sed -E "$SANITIZE" "$RAW_CONFIG" > "$RESOLVED_CONFIG"
  echo "Redacted resolved config stored at $RESOLVED_CONFIG. Raw config is kept only in a temporary directory and removed on exit."
  echo "===== docker compose resolved service list ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" config --services
  echo "===== safety assertion: app must use isolated app host port and must not bind production app host port 18081 ====="
  grep -Eq "published:[[:space:]]*\"?${APP_PORT}\"?|127\.0\.0\.1:${APP_PORT}:8080" "$RAW_CONFIG"
  if grep -Eq "published:[[:space:]]*\"?18081\"?|127\.0\.0\.1:18081:8080|0\.0\.0\.0:18081:8080" "$RAW_CONFIG"; then
    echo "Refusing config that still binds production app host port 18081" >&2
    exit 2
  fi
  echo "===== safety assertion: resolved config must point app/workers to isolated compose postgres ====="
  grep -q "postgresql+psycopg://" "$RAW_CONFIG"
  grep -q "@postgres:5432/${POSTGRES_DB_GATE}" "$RAW_CONFIG"
  echo "===== safety assertion: redacted evidence must not contain obvious secret values ====="
  if grep -Ei "(token|secret|password|authorization|cookie|api[_-]?key|database_url):[[:space:]]*[^[]" "$RESOLVED_CONFIG" >/dev/null; then
    echo "Refusing to keep unresolved secret-like values in redacted config evidence" >&2
    exit 2
  fi
} 2>&1 | sed -E "$SANITIZE" | tee "$OUT/command_outputs/11_docker_config_validation.log"

if [ "$RUN_STACK" != "1" ]; then
  {
    echo "Docker stack execution skipped by default."
    echo "Reason: server compose file is production-oriented and must not be started from a release gate by accident."
    echo "To run isolated staging stack evidence, set:"
    echo "  DOCKER_GATE_RUN_STACK=1 DOCKER_GATE_CONFIRM=non_production"
    echo "Runtime gate uses a temporary gate compose file with isolated host ports and forced DATABASE_URL."
    echo "Optional isolated ports:"
    echo "  DOCKER_GATE_APP_PORT=$APP_PORT DOCKER_GATE_NGINX_PORT=$NGINX_PORT"
  } | tee "$OUT/command_outputs/11_docker_stack_skipped.log"
  echo "Docker config-only evidence written to: $OUT"
  exit 0
fi

if [ "$CONFIRM" != "non_production" ]; then
  echo "Refusing to start docker stack without DOCKER_GATE_CONFIRM=non_production" >&2
  exit 2
fi
if [ "${APP_ENV:-staging}" = "production" ]; then
  echo "Refusing shell APP_ENV=production" >&2
  exit 2
fi
case "$PROJECT_NAME" in
  *prod*|*production*) echo "Refusing production-like COMPOSE_PROJECT_NAME=$PROJECT_NAME" >&2; exit 2 ;;
esac

{
  echo "===== docker compose build ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" build
  echo "===== start isolated postgres ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" up -d postgres
  echo "===== alembic upgrade head against isolated postgres ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" run --rm app alembic upgrade head
  echo "===== up app and workers on isolated project/ports ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" up -d app worker-background worker-handoff-snapshot worker-webchat-ai
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" ps
  echo "===== healthz ====="
  curl -fsS "http://127.0.0.1:${APP_PORT}/healthz"
  echo
  echo "===== readyz ====="
  curl -fsS "http://127.0.0.1:${APP_PORT}/readyz"
  echo
  echo "===== logs app tail ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" logs --tail=200 app | sed -E "$SANITIZE"
  echo "===== logs handoff worker tail ====="
  COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" logs --tail=200 worker-handoff-snapshot | sed -E "$SANITIZE"
} 2>&1 | sed -E "$SANITIZE" | tee "$OUT/command_outputs/11_docker_compose_health.log"

if [ "${RUN_NGINX_SMOKE:-0}" = "1" ]; then
  {
    echo "===== start nginx edge profile on isolated port ====="
    COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" --profile edge-nginx up -d nginx
    COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose -f "$GATE_COMPOSE_FILE" -f "$OVERRIDE_FILE" ps nginx
    echo "===== nginx widget smoke ====="
    curl -fsS "http://127.0.0.1:${NGINX_PORT}/webchat/widget.js" >/tmp/nexusdesk_widget_smoke.js
    wc -c /tmp/nexusdesk_widget_smoke.js
    grep -q "fast-reply" /tmp/nexusdesk_widget_smoke.js
    echo "===== nginx API OPTIONS smoke ====="
    curl -fsS -X OPTIONS "http://127.0.0.1:${NGINX_PORT}/api/webchat/fast-reply" -H 'Origin: http://localhost' -i | head -40
  } 2>&1 | sed -E "$SANITIZE" | tee "$OUT/command_outputs/12_nginx_smoke.log"
fi

echo "Docker release evidence written to: $OUT"
