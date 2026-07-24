#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${CANDIDATE_IMAGE:?CANDIDATE_IMAGE required}"

if [[ ! "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "controlled assurance runtime: SOURCE_SHA must be an exact lowercase Git SHA" >&2
  exit 2
fi

suffix="${SOURCE_SHA:0:12}"
network_name="nexus-assurance-${suffix}"
database_name="nexus-assurance-postgres-${suffix}"
candidate_name="nexus-assurance-candidate-${suffix}"
database_user="nexus_image"
database_password="nexus_image_only"
database_name_value="nexus_image_acceptance"
database_url="postgresql+psycopg://${database_user}:${database_password}@${database_name}:5432/${database_name_value}"

cleanup() {
  docker rm -f "${candidate_name}" >/dev/null 2>&1 || true
  docker rm -f "${database_name}" >/dev/null 2>&1 || true
  docker network rm "${network_name}" >/dev/null 2>&1 || true
}

start() {
  : "${RC_POSTGRES_IMAGE_PIN:?RC_POSTGRES_IMAGE_PIN required when an assurance runtime must be created}"
  if [[ ! "${RC_POSTGRES_IMAGE_PIN}" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "controlled assurance runtime: PostgreSQL image must be digest pinned" >&2
    exit 2
  fi

  cleanup
  docker network create "${network_name}" >/dev/null
  docker run -d --name "${database_name}" --network "${network_name}" \
    -e POSTGRES_USER="${database_user}" \
    -e POSTGRES_PASSWORD="${database_password}" \
    -e POSTGRES_DB="${database_name_value}" \
    "${RC_POSTGRES_IMAGE_PIN}" >/dev/null

  ready=false
  for _attempt in $(seq 1 60); do
    if docker exec "${database_name}" pg_isready -U "${database_user}" -d "${database_name_value}" >/dev/null 2>&1; then
      ready=true
      break
    fi
    if [[ "$(docker inspect --format '{{.State.Status}}' "${database_name}" 2>/dev/null || true)" =~ ^(exited|dead)$ ]]; then
      docker logs "${database_name}" >&2 || true
      exit 1
    fi
    sleep 1
  done
  if [[ "${ready}" != "true" ]]; then
    echo "controlled assurance runtime: PostgreSQL did not become ready" >&2
    exit 1
  fi

  docker run --rm --network "${network_name}" \
    -e PYTHONPATH=/app/backend \
    -e APP_ENV=test \
    -e DATABASE_URL="${database_url}" \
    -e JWT_SECRET_KEY=ci-only-not-a-production-secret-0123456789abcdef \
    -e AI_REPLY_CONTRACT_SECRET=ci-only-contract-secret-0123456789abcdef \
    -e PROVIDER_RUNTIME_ENABLED=false \
    -e PROVIDER_RUNTIME_KILL_SWITCH=true \
    -e ENABLE_OUTBOUND_DISPATCH=false \
    -e WHATSAPP_NATIVE_ENABLED=false \
    "${CANDIDATE_IMAGE}" python -m alembic upgrade head

  docker run -d --name "${candidate_name}" --network "${network_name}" \
    -e PYTHONPATH=/app/backend \
    -e APP_ENV=test \
    -e DATABASE_URL="${database_url}" \
    -e JWT_SECRET_KEY=ci-only-not-a-production-secret-0123456789abcdef \
    -e AI_REPLY_CONTRACT_SECRET=ci-only-contract-secret-0123456789abcdef \
    -e PROVIDER_RUNTIME_ENABLED=false \
    -e PROVIDER_RUNTIME_TRAFFIC_MODE=control \
    -e PROVIDER_RUNTIME_KILL_SWITCH=true \
    -e PROVIDER_RUNTIME_CANARY_PERCENT=0 \
    -e PRIVATE_AI_RUNTIME_ENABLED=false \
    -e WEBCHAT_AI_ENABLED=false \
    -e WEBCHAT_HUMAN_CALL_ENABLED=false \
    -e WEBCHAT_LIVE_AI_VOICE_ENABLED=false \
    -e ENABLE_OUTBOUND_DISPATCH=false \
    -e OUTBOUND_PROVIDER=disabled \
    -e WHATSAPP_NATIVE_ENABLED=false \
    -e WHATSAPP_DISPATCH_MODE=disabled \
    -e SPEEDAF_MCP_ENABLED=false \
    -e OPERATIONS_DISPATCH_MODE=disabled \
    "${CANDIDATE_IMAGE}" >/dev/null

  running=false
  for _attempt in $(seq 1 60); do
    status="$(docker inspect --format '{{.State.Status}}' "${candidate_name}" 2>/dev/null || true)"
    if [[ "${status}" == "running" ]] && docker exec "${candidate_name}" python -c 'import app.main' >/dev/null 2>&1; then
      running=true
      break
    fi
    if [[ "${status}" =~ ^(exited|dead)$ ]]; then
      docker logs "${candidate_name}" >&2 || true
      exit 1
    fi
    sleep 1
  done
  if [[ "${running}" != "true" ]]; then
    echo "controlled assurance runtime: candidate did not become executable" >&2
    exit 1
  fi

  printf '%s\n' "${candidate_name}"
}

case "${1:-}" in
  start)
    trap cleanup ERR
    start
    trap - ERR
    ;;
  cleanup)
    cleanup
    ;;
  *)
    echo "usage: $0 start|cleanup" >&2
    exit 2
    ;;
esac
