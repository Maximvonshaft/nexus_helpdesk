#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${RC_ENV_FILE:-${ROOT_DIR}/deploy/.env.rc-test}"
COMPOSE_FILE="${RC_COMPOSE_FILE:-${ROOT_DIR}/deploy/docker-compose.rc-test.yml}"
EVIDENCE_DIR="${RC_EVIDENCE_DIR:-${ROOT_DIR}/artifacts/rc-test}"
BASE_URL="${RC_BASE_URL:-http://127.0.0.1:18083}"
PUBLIC_ORIGIN="${RC_PUBLIC_ORIGIN:-${BASE_URL}}"
STATUS_FILE="${EVIDENCE_DIR}/status.json"
CURRENT_STAGE="initializing"

ALL_SERVICES=(
  postgres-rc
  migrate-rc
  seed-rc
  app-rc
  nginx-rc
  worker-outbound-rc
  worker-background-rc
  worker-webchat-ai-rc
)

for command in docker python3 curl git; do
  command -v "${command}" >/dev/null 2>&1 || {
    echo "missing required command: ${command}" >&2
    exit 2
  }
done

if [[ ! -f "${ENV_FILE}" || -L "${ENV_FILE}" ]]; then
  echo "RC environment file missing or unsafe: ${ENV_FILE}" >&2
  exit 2
fi
if [[ ! -f "${COMPOSE_FILE}" || -L "${COMPOSE_FILE}" ]]; then
  echo "RC Compose file missing or unsafe: ${COMPOSE_FILE}" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

RC_SOURCE_SHA_VALUE="${RC_SOURCE_SHA:?RC_SOURCE_SHA required}"
SOURCE_SHA="${GIT_SHA:?GIT_SHA required}"
IMAGE_TAG_VALUE="${RC_IMAGE_TAG:?RC_IMAGE_TAG required}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME required}"
RC_POSTGRES_IMAGE="${RC_POSTGRES_IMAGE:?RC_POSTGRES_IMAGE required}"
RC_NGINX_IMAGE="${RC_NGINX_IMAGE:?RC_NGINX_IMAGE required}"
BUILD_TIME="${BUILD_TIME:?BUILD_TIME required}"
APP_VERSION="${APP_VERSION:?APP_VERSION required}"
RC_TEST_ADMIN_USERNAME="${RC_TEST_ADMIN_USERNAME:?RC_TEST_ADMIN_USERNAME required}"
RC_TEST_ADMIN_PASSWORD="${RC_TEST_ADMIN_PASSWORD:?RC_TEST_ADMIN_PASSWORD required}"

if [[ ! "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GIT_SHA must be an exact lowercase 40-character Git SHA" >&2
  exit 2
fi
if [[ "${RC_SOURCE_SHA_VALUE}" != "${SOURCE_SHA}" ]]; then
  echo "RC_SOURCE_SHA does not match GIT_SHA" >&2
  exit 2
fi
if [[ "$(git -C "${ROOT_DIR}" rev-parse HEAD)" != "${SOURCE_SHA}" ]]; then
  echo "RC source SHA does not match checkout" >&2
  exit 2
fi
if ! git -C "${ROOT_DIR}" diff --quiet || ! git -C "${ROOT_DIR}" diff --cached --quiet; then
  echo "RC checkout must be clean" >&2
  exit 2
fi

python3 - "${ENV_FILE}" <<'PY'
import sys
from pathlib import Path

expected = {
    "APP_ENV": "production",
    "TENANT_RUNTIME_AUTHORITY_MODE": "enforce",
    "AUTO_INIT_DB": "false",
    "SEED_DEMO_DATA": "false",
    "ALLOW_DEV_AUTH": "false",
    "PROVIDER_RUNTIME_ENABLED": "false",
    "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
    "PROVIDER_RUNTIME_KILL_SWITCH": "true",
    "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
    "PRIVATE_AI_RUNTIME_ENABLED": "false",
    "WEBCHAT_AI_ENABLED": "false",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_HUMAN_CALL_ENABLED": "false",
    "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
    "ENABLE_OUTBOUND_DISPATCH": "false",
    "OUTBOUND_PROVIDER": "disabled",
    "WHATSAPP_ENABLED": "false",
    "WHATSAPP_EMBEDDED_SIGNUP_ENABLED": "false",
    "WHATSAPP_MEDIA_ENABLED": "false",
    "WHATSAPP_MEDIA_SCANNER": "disabled",
    "EMAIL_MAILBOX_SYNC_ENABLED": "false",
    "SPEEDAF_MCP_ENABLED": "false",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
    "SPEEDAF_CANCEL_ENABLED": "false",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
}
values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, sep, value = raw.partition("=")
    if not sep:
        raise SystemExit(f"invalid RC env line: {raw!r}")
    values[key.strip()] = value.strip()
for key, required in expected.items():
    if values.get(key, "").lower() != required:
        raise SystemExit(f"unsafe RC control {key}")
for retired in (
    "WHATSAPP_NATIVE_ENABLED",
    "WHATSAPP_DISPATCH_MODE",
    "DATABASE_URL_HANDOFF",
):
    if retired in values:
        raise SystemExit(f"retired RC control present: {retired}")
PY

mkdir -p "${EVIDENCE_DIR}"
chmod 700 "${EVIDENCE_DIR}"

compose() {
  docker compose \
    --project-name "${PROJECT_NAME}" \
    --env-file "${ENV_FILE}" \
    --file "${COMPOSE_FILE}" \
    "$@"
}

set_stage() {
  CURRENT_STAGE="$1"
  python3 - "${STATUS_FILE}" "${CURRENT_STAGE}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schema": "nexus.osr.rc-test-status.v1",
    "stage": sys.argv[2],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

redact() {
  sed -E \
    -e 's#(postgresql(\+psycopg)?://[^:]+:)[^@]+@#\1[REDACTED]@#g' \
    -e 's#(Authorization: Bearer )[A-Za-z0-9._~+/-]+#\1[REDACTED]#g' \
    -e 's#(SECRET|PASSWORD|TOKEN|API_KEY)=([^[:space:]]+)#\1=[REDACTED]#g'
}

cleanup() {
  set +e
  compose ps -a 2>&1 | redact > "${EVIDENCE_DIR}/compose-ps-final.txt"
  for service in "${ALL_SERVICES[@]}"; do
    compose logs --no-color --tail 200 "${service}" 2>&1 \
      | redact > "${EVIDENCE_DIR}/${service}.log"
  done
  compose down --volumes --remove-orphans 2>&1 \
    | redact > "${EVIDENCE_DIR}/cleanup.txt"
}

on_exit() {
  rc=$?
  if [[ ${rc} -ne 0 ]]; then
    printf 'RC0_TEST_DEPLOYABLE=false\nFAILED_STAGE=%s\n' "${CURRENT_STAGE}" >&2
  fi
  cleanup
  exit "${rc}"
}
trap on_exit EXIT

wait_for_health() {
  local service="$1"
  local attempts="${2:-90}"
  local container status health
  for _ in $(seq 1 "${attempts}"); do
    container="$(compose ps -q "${service}")"
    if [[ -n "${container}" ]]; then
      status="$(docker inspect --format '{{.State.Status}}' "${container}" 2>/dev/null || true)"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${container}" 2>/dev/null || true)"
      if [[ "${status}" == "running" && ("${health}" == "healthy" || -z "${health}") ]]; then
        return 0
      fi
      if [[ "${status}" =~ ^(exited|dead)$ || "${health}" == "unhealthy" ]]; then
        compose logs --no-color --tail 200 "${service}" >&2 || true
        return 1
      fi
    fi
    sleep 2
  done
  echo "Timed out waiting for ${service}" >&2
  return 1
}

set_stage base-images
for image in "${RC_POSTGRES_IMAGE}" "${RC_NGINX_IMAGE}"; do
  docker pull "${image}" >/dev/null
  docker image inspect "${image}" --format '{{json .RepoDigests}}'
done > "${EVIDENCE_DIR}/base-image-digests.jsonl"
docker image inspect "${RC_POSTGRES_IMAGE}" >/dev/null
docker image inspect "${RC_NGINX_IMAGE}" >/dev/null
printf '%s\n' "${RC_POSTGRES_IMAGE}" > "${EVIDENCE_DIR}/postgres-image-digest.txt"
printf '%s\n' "${RC_NGINX_IMAGE}" > "${EVIDENCE_DIR}/nginx-image-digest.txt"

set_stage build
DOCKER_BUILDKIT=1 docker build --pull=false \
  --build-arg "GIT_SHA=${SOURCE_SHA}" \
  --build-arg "BUILD_TIME=${BUILD_TIME}" \
  --build-arg "IMAGE_TAG=${IMAGE_TAG_VALUE}" \
  --build-arg "APP_VERSION=${APP_VERSION}" \
  --build-arg "FRONTEND_BUILD_SHA=${SOURCE_SHA}" \
  --tag "${IMAGE_TAG_VALUE}" \
  "${ROOT_DIR}"
docker image inspect "${IMAGE_TAG_VALUE}" --format '{{.Id}}' > "${EVIDENCE_DIR}/image-id.txt"

set_stage compose-validation
compose config --quiet
compose config --services > "${EVIDENCE_DIR}/compose-services.txt"
compose config --images > "${EVIDENCE_DIR}/compose-images.txt"
python3 - "${COMPOSE_FILE}" "${EVIDENCE_DIR}/safe-config.json" "${PUBLIC_ORIGIN}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
compose_path = Path(sys.argv[1])
text = compose_path.read_text(encoding="utf-8")
for forbidden in (
    "production_runtime",
    "/opt/nexus_helpdesk/data",
    "/run/nexus/ai_runtime_token",
    "external: true",
    "worker-handoff-snapshot",
):
    if forbidden in text:
        raise SystemExit(f"forbidden production coupling in RC compose: {forbidden}")
Path(sys.argv[2]).write_text(json.dumps({
    "schema": "nexus.osr.rc-test-safe-config.v3",
    "profile": "rc-test-isolated-v1",
    "compose_sha256": hashlib.sha256(compose_path.read_bytes()).hexdigest(),
    "browser_origin": sys.argv[3],
    "database_service": "postgres-rc",
    "network": "project_local_internal_rc",
    "storage": "project_named_volumes",
    "workers": ["outbound", "background", "webchat-ai"],
    "provider_candidate_enabled": False,
    "real_outbound_enabled": False,
    "whatsapp_enabled": False,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

set_stage start-postgres
compose up -d postgres-rc
wait_for_health postgres-rc 60

set_stage resolve-migration-head
heads_output="$(compose run --rm --no-deps -T migrate-rc python -m alembic heads)"
printf '%s\n' "${heads_output}" > "${EVIDENCE_DIR}/migration-head.txt"
mapfile -t migration_heads < <(printf '%s\n' "${heads_output}" | awk 'NF {print $1}')
if [[ "${#migration_heads[@]}" -ne 1 ]]; then
  echo "RC requires exactly one Alembic head" >&2
  exit 2
fi
MIGRATION_HEAD="${migration_heads[0]}"
if [[ ! "${MIGRATION_HEAD}" =~ ^[A-Za-z0-9_.-]{1,80}$ ]]; then
  echo "Unexpected Alembic head format: ${MIGRATION_HEAD}" >&2
  exit 2
fi

set_stage migrate-database
compose run --rm --no-deps -T migrate-rc python -m alembic upgrade head \
  | tee "${EVIDENCE_DIR}/migration.txt"
current_output="$(compose run --rm --no-deps -T migrate-rc python -m alembic current)"
printf '%s\n' "${current_output}" > "${EVIDENCE_DIR}/migration-current.txt"
MIGRATION_CURRENT="$(printf '%s\n' "${current_output}" | awk 'NF {print $1; exit}')"
[[ "${MIGRATION_CURRENT}" == "${MIGRATION_HEAD}" ]] || {
  echo "Alembic current ${MIGRATION_CURRENT} does not match head ${MIGRATION_HEAD}" >&2
  exit 2
}

set_stage seed
compose run --rm --no-deps -T seed-rc | tee "${EVIDENCE_DIR}/seed-first.txt"
compose run --rm --no-deps -T seed-rc | tee "${EVIDENCE_DIR}/seed-second.txt"
compose run --rm --no-deps -T app-rc python - > "${EVIDENCE_DIR}/seed-verification.json" <<'PY'
import json
import os
from app.db import SessionLocal
from app.model_registry import register_all_models
from app.models import Tenant
from app.models_webchat_binding import WebchatPublicOriginBinding
from app.services.webchat_tenant_binding import normalize_public_origin
register_all_models()
tenant_key = os.environ["RC_TEST_TENANT_KEY"].strip().lower()
origin = normalize_public_origin(os.environ["RC_PUBLIC_ORIGIN"])
db = SessionLocal()
try:
    tenants = db.query(Tenant).filter(Tenant.tenant_key == tenant_key).all()
    rows = db.query(WebchatPublicOriginBinding).filter(WebchatPublicOriginBinding.normalized_origin == origin).all()
    if len(tenants) != 1 or not tenants[0].is_active or len(rows) != 1:
        raise SystemExit("RC seed authority invalid")
    row = rows[0]
    if row.tenant_key != tenant_key:
        raise SystemExit("RC origin binding Tenant mismatch")
    print(json.dumps({
        "schema": "nexus.osr.rc-test-seed-verification.v1",
        "status": "pass",
        "origin": origin,
        "tenant_key": row.tenant_key,
        "channel_key": row.channel_key,
        "is_active": bool(row.is_active),
        "row_count": 1,
    }, indent=2, sort_keys=True))
finally:
    db.close()
PY

set_stage seed-operator
compose run --rm --no-deps -T app-rc python - <<'PY'
import os
from sqlalchemy import func
from app.auth_service import hash_password, verify_password
from app.db import SessionLocal
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Tenant, User
from app.services.tenant_authority import RUNTIME_TENANT_ASSIGNMENT_SOURCE, RUNTIME_TENANT_ASSIGNMENT_VERSION
register_all_models()
tenant_key = os.environ["RC_TEST_TENANT_KEY"].strip().lower()
db = SessionLocal()
try:
    tenant = db.query(Tenant).filter(Tenant.tenant_key == tenant_key).first()
    if tenant is None or not tenant.is_active:
        raise SystemExit("RC operator Tenant missing")
    username = os.environ["RC_TEST_ADMIN_USERNAME"].strip()
    password = os.environ["RC_TEST_ADMIN_PASSWORD"]
    user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if user is None:
        user = User(
            username=username,
            display_name="RC Test Administrator",
            email=None,
            password_hash=hash_password(password),
            role=UserRole.admin,
            is_active=True,
        )
        db.add(user)
    elif not verify_password(password, user.password_hash):
        user.password_hash = hash_password(password)
    user.role = UserRole.admin
    user.is_active = True
    user.tenant_id = tenant.id
    user.tenant_assignment_source = RUNTIME_TENANT_ASSIGNMENT_SOURCE
    user.tenant_assignment_version = RUNTIME_TENANT_ASSIGNMENT_VERSION
    db.commit()
finally:
    db.close()
print("RC_TEST_OPERATOR_READY=true")
PY

set_stage start-runtime
compose up -d app-rc nginx-rc worker-outbound-rc worker-background-rc worker-webchat-ai-rc
for service in app-rc nginx-rc worker-outbound-rc worker-background-rc worker-webchat-ai-rc; do
  wait_for_health "${service}"
done
compose ps > "${EVIDENCE_DIR}/compose-ps-healthy.txt"

set_stage http-smoke
python3 "${ROOT_DIR}/scripts/release/rc_test_http_smoke.py" \
  --base-url "${BASE_URL}" \
  --origin "${PUBLIC_ORIGIN}" \
  --source-sha "${SOURCE_SHA}" \
  --image-tag "${IMAGE_TAG_VALUE}" \
  --migration-head "${MIGRATION_HEAD}" \
  --evidence-dir "${EVIDENCE_DIR}"

set_stage side-effect-proof
compose exec -T app-rc python /app/scripts/release/rc_test_side_effects.py \
  > "${EVIDENCE_DIR}/side-effect-safety.json"

set_stage network-proof
app_container="$(compose ps -q app-rc)"
nginx_container="$(compose ps -q nginx-rc)"
python3 - "${app_container}" "${nginx_container}" "${PROJECT_NAME}_rc" "${PROJECT_NAME}_edge" "${EVIDENCE_DIR}/network-safety.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path
app_id, nginx_id, rc_network, edge_network, output = sys.argv[1:]
def inspect_json(kind, identity):
    return json.loads(subprocess.check_output(["docker", kind, "inspect", identity], text=True))[0]
app = inspect_json("container", app_id)
nginx = inspect_json("container", nginx_id)
rc = inspect_json("network", rc_network)
edge = inspect_json("network", edge_network)
app_networks = sorted(app["NetworkSettings"]["Networks"])
nginx_networks = sorted(nginx["NetworkSettings"]["Networks"])
if app_networks != [rc_network]:
    raise SystemExit("App must attach only to the internal RC network")
if nginx_networks != sorted([rc_network, edge_network]):
    raise SystemExit("Nginx network attachment mismatch")
if rc.get("Internal") is not True or edge.get("Internal") is not False:
    raise SystemExit("RC network isolation flags mismatch")
Path(output).write_text(json.dumps({
    "schema": "nexus.osr.rc-test-network-safety.v1",
    "status": "pass",
    "app_networks": app_networks,
    "nginx_networks": nginx_networks,
    "internal_network": rc_network,
    "loopback_gateway_network": edge_network,
    "production_network_joined": False,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

set_stage browser-smoke
if [[ "${RC_RUN_BROWSER_SMOKE:-false}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  (
    cd "${ROOT_DIR}/webapp"
    PLAYWRIGHT_BASE_URL="${BASE_URL}" \
    RC_TEST_ADMIN_USERNAME="${RC_TEST_ADMIN_USERNAME}" \
    RC_TEST_ADMIN_PASSWORD="${RC_TEST_ADMIN_PASSWORD}" \
    RC_SOURCE_SHA="${SOURCE_SHA}" \
      npm run e2e -- e2e/rc-live.spec.ts --workers=1 --reporter=line
  ) | tee "${EVIDENCE_DIR}/browser-smoke.txt"
else
  echo "RC_RUN_BROWSER_SMOKE must be true for a deployable candidate" >&2
  exit 2
fi

set_stage teardown
compose down --volumes --remove-orphans | tee "${EVIDENCE_DIR}/teardown.txt"
remaining_containers="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
remaining_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
remaining_networks="$(docker network ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
[[ -z "${remaining_containers}${remaining_volumes}${remaining_networks}" ]] || {
  echo "RC resources remain after teardown" >&2
  exit 2
}
python3 - "${EVIDENCE_DIR}/rollback-verification.json" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schema": "nexus.osr.rc-test-rollback-verification.v1",
    "status": "pass",
    "remaining_containers": 0,
    "remaining_volumes": 0,
    "remaining_networks": 0,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
trap - EXIT

set_stage manifest
python3 "${ROOT_DIR}/scripts/release/build_rc_test_manifest.py" \
  --evidence-dir "${EVIDENCE_DIR}" \
  --source-sha "${SOURCE_SHA}" \
  --image-tag "${IMAGE_TAG_VALUE}" \
  --migration-head "${MIGRATION_HEAD}"
python3 "${ROOT_DIR}/scripts/release/validate_rc_test_manifest.py" \
  "${EVIDENCE_DIR}/candidate-manifest.json"

set_stage completed
echo "RC0_TEST_DEPLOYABLE=true"
echo "PRODUCTION_READY=false"
echo "FULL_OSR_AUTOMATION=NO_GO"
echo "evidence_dir=${EVIDENCE_DIR}"
