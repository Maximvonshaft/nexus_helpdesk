#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TOPOLOGY="${NEXUS_DATABASE_TOPOLOGY:-}"
case "$TOPOLOGY" in
  external)
    ENV_FILE="${NEXUS_CONTROLLED_ENV_FILE:-deploy/.env.controlled}"
    COMPOSE_FILES=(
      -f deploy/docker-compose.controlled.yml
    )
    ;;
  local)
    ENV_FILE="${NEXUS_CONTROLLED_ENV_FILE:-deploy/.env.controlled.local-postgres}"
    COMPOSE_FILES=(
      -f deploy/docker-compose.controlled.yml
      -f deploy/docker-compose.controlled-postgres.yml
    )
    ;;
  *)
    echo "Set NEXUS_DATABASE_TOPOLOGY=external or local." >&2
    exit 2
    ;;
esac

if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
  echo "Controlled environment file must be a regular non-symlink file: $ENV_FILE" >&2
  exit 3
fi

exec docker compose \
  --env-file "$ENV_FILE" \
  "${COMPOSE_FILES[@]}" \
  "$@"
