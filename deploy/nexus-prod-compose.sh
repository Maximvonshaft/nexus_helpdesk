#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

exec docker compose \
  -f deploy/docker-compose.server.yml \
  "$@"
