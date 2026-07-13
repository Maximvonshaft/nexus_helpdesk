#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/docker-compose.rc-test.yml"
ENV_FILE="${RC_ENV_FILE:-${ROOT_DIR}/deploy/.env.rc-test}"
EVIDENCE_DIR="${RC_EVIDENCE_DIR:-${ROOT_DIR}/artifacts/rc-test}"
KEEP_STACK="${KEEP_RC_STACK:-false}"
CURRENT_STAGE="bootstrap"

set_stage() {
  CURRENT_STAGE="$1"
  echo "RC_STAGE=${CURRENT_STAGE}"
}

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}; copy deploy/.env.rc-test.example and replace placeholders." >&2
  exit 2
fi

set -a
# The RC env file is repository-owner controlled and generated locally/inside CI.
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nexus_rc_test}"
BASE_URL="${RC_BASE_URL:-http://127.0.0.1:${RC_APP_PORT:-18083}}"
PUBLIC_ORIGIN="${RC_PUBLIC_ORIGIN:-${BASE_URL%/}}"
export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
export RC_BASE_URL="${BASE_URL}"
export RC_PUBLIC_ORIGIN="${PUBLIC_ORIGIN}"

mkdir -p "${EVIDENCE_DIR}"
find "${EVIDENCE_DIR}" -mindepth 1 -maxdepth 1 -type f -delete
if find "${EVIDENCE_DIR}" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q .; then
  echo "RC evidence directory contains a non-regular entry" >&2
  exit 2
fi

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

ALL_SERVICES=(
  postgres-rc migrate-rc seed-rc app-rc nginx-rc
  worker-outbound-rc worker-background-rc worker-webchat-ai-rc worker-handoff-snapshot-rc
)

wait_for_health() {
  local service="$1"
  local attempts="${2:-60}"
  local container_id status i
  for i in $(seq 1 "${attempts}"); do
    container_id="$(compose ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${container_id}" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
      if [[ "${status}" == "healthy" ]]; then
        return 0
      fi
      if [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]]; then
        echo "${service} entered ${status}" >&2
        return 1
      fi
    fi
    sleep 2
  done
  echo "Timed out waiting for healthy ${service}" >&2
  return 1
}

collect_failure_evidence() {
  local exit_code="$1"
  local raw_dir status_file
  raw_dir="$(mktemp -d)"
  status_file="${raw_dir}/service-status.tsv"
  set +e
  compose ps --all > "${raw_dir}/compose-ps.txt" 2>&1
  compose logs --no-color --tail=250 "${ALL_SERVICES[@]}" > "${raw_dir}/logs.txt" 2>&1
  : > "${status_file}"
  for service in "${ALL_SERVICES[@]}"; do
    container_id="$(compose ps -q --all "${service}" 2>/dev/null || true)"
    if [[ -z "${container_id}" ]]; then
      printf '%s\t%s\n' "${service}" "missing" >> "${status_file}"
      continue
    fi
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || echo inspect_error)"
    printf '%s\t%s\n' "${service}" "${status}" >> "${status_file}"
  done
  python3 - "${ENV_FILE}" "${raw_dir}/compose-ps.txt" "${EVIDENCE_DIR}/compose-ps-failure.txt" \
    "${raw_dir}/logs.txt" "${EVIDENCE_DIR}/bounded-failure-logs.txt" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
pairs = []
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip()
    if len(value) >= 8:
        pairs.append((value, f"[REDACTED:{key.strip()}]"))
pairs.sort(key=lambda item: len(item[0]), reverse=True)
for source_name, target_name in ((sys.argv[2], sys.argv[3]), (sys.argv[4], sys.argv[5])):
    text = Path(source_name).read_text(encoding="utf-8", errors="replace")
    for value, replacement in pairs:
        text = text.replace(value, replacement)
    Path(target_name).write_text(text[-512 * 1024 :], encoding="utf-8")
PY
  python3 - "${CURRENT_STAGE}" "${exit_code}" "${status_file}" "${EVIDENCE_DIR}/failure-summary.json" <<'PY'
import json
import re
import sys
from pathlib import Path

stage, exit_code, status_path, output_path = sys.argv[1:]
if not re.fullmatch(r"[a-z0-9_-]{1,64}", stage):
    stage = "invalid_stage"
statuses = {}
for line in Path(status_path).read_text(encoding="utf-8").splitlines():
    if "\t" not in line:
        continue
    service, status = line.split("\t", 1)
    if re.fullmatch(r"[a-z0-9_-]{1,80}", service) and re.fullmatch(r"[a-z_]{1,40}", status):
        statuses[service] = status
Path(output_path).write_text(json.dumps({
    "schema": "nexus.osr.rc-test-failure-summary.v1",
    "status": "failed",
    "stage": stage,
    "exit_code": int(exit_code),
    "service_states": statuses,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  rm -rf "${raw_dir}"
  set -e
}

cleanup_stack() {
  local exit_code=$?
  if [[ "${exit_code}" -ne 0 ]]; then
    collect_failure_evidence "${exit_code}"
  fi
  if [[ "${KEEP_STACK,,}" =~ ^(1|true|yes|on)$ ]]; then
    echo "RC stack retained because KEEP_RC_STACK=${KEEP_STACK}"
    return "${exit_code}"
  fi
  set +e
  compose down --volumes --remove-orphans > "${EVIDENCE_DIR}/teardown.txt" 2>&1
  local down_code=$?
  set -e
  if [[ "${exit_code}" -eq 0 && "${down_code}" -ne 0 ]]; then
    return "${down_code}"
  fi
  return "${exit_code}"
}
trap cleanup_stack EXIT

set_stage validate-env
python3 - "${ENV_FILE}" "${BASE_URL}" "${PUBLIC_ORIGIN}" <<'PY'
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

path = Path(sys.argv[1])
base_url = sys.argv[2].rstrip("/")
public_origin = sys.argv[3].rstrip("/")
values = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"invalid env line: {line[:80]}")
    key, value = line.split("=", 1)
    key = key.strip()
    if key in values:
        raise SystemExit(f"duplicate RC env key: {key}")
    values[key] = value.strip()

required = [
    "RC_IMAGE_TAG", "IMAGE_TAG", "RC_POSTGRES_IMAGE", "RC_NGINX_IMAGE",
    "GIT_SHA", "FRONTEND_BUILD_SHA", "RC_BASE_URL", "RC_PUBLIC_ORIGIN",
    "RC_TEST_TENANT_KEY", "RC_TEST_CHANNEL_KEY", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL", "SECRET_KEY",
    "RUNTIME_CONTRACT_SIGNING_SECRET", "RC_TEST_ADMIN_USERNAME",
    "RC_TEST_ADMIN_PASSWORD",
]
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit("missing required RC values: " + ", ".join(missing))
placeholders = [key for key, value in values.items() if "<" in value or "replace-with" in value.lower()]
if placeholders:
    raise SystemExit("unresolved placeholders: " + ", ".join(sorted(placeholders)))
if not re.fullmatch(r"[0-9a-f]{40}", values["GIT_SHA"]):
    raise SystemExit("GIT_SHA must be an exact lowercase 40-character SHA")
if values["FRONTEND_BUILD_SHA"] != values["GIT_SHA"]:
    raise SystemExit("FRONTEND_BUILD_SHA must match GIT_SHA")
if values["RC_IMAGE_TAG"] != values["IMAGE_TAG"]:
    raise SystemExit("RC_IMAGE_TAG and IMAGE_TAG must match")
if len(values["SECRET_KEY"]) < 32 or len(values["RUNTIME_CONTRACT_SIGNING_SECRET"]) < 32:
    raise SystemExit("RC signing secrets must be at least 32 characters")
if len(values["RC_TEST_ADMIN_PASSWORD"]) < 16:
    raise SystemExit("RC_TEST_ADMIN_PASSWORD must be at least 16 characters")

for label, raw_url in (("RC_BASE_URL", base_url), ("RC_PUBLIC_ORIGIN", public_origin)):
    parsed = urlparse(raw_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit(f"{label} must not contain credentials, query or fragment")
    if parsed.path not in {"", "/"}:
        raise SystemExit(f"{label} must be an origin/root URL")
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"{label} may use HTTP only on loopback")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit(f"{label} must be HTTP(S)")
if base_url != public_origin:
    raise SystemExit("RC_BASE_URL and RC_PUBLIC_ORIGIN must be the same real browser origin")
if values["RC_BASE_URL"].rstrip("/") != base_url or values["RC_PUBLIC_ORIGIN"].rstrip("/") != public_origin:
    raise SystemExit("RC URL environment values do not match the executed origin")

parsed_db = urlparse(values["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
if parsed_db.hostname != "postgres-rc":
    raise SystemExit("DATABASE_URL must target isolated postgres-rc")
if values.get("APP_ENV") != "production":
    raise SystemExit("APP_ENV must be production")
if values.get("ALLOWED_ORIGINS", "").rstrip("/") != public_origin:
    raise SystemExit("ALLOWED_ORIGINS must match the real RC browser origin")
if values.get("WEBCHAT_ALLOWED_ORIGINS", "").rstrip("/") != public_origin:
    raise SystemExit("WEBCHAT_ALLOWED_ORIGINS must match the real RC browser origin")

expected = {
    "AUTO_INIT_DB": "false",
    "TENANT_RUNTIME_AUTHORITY_MODE": "enforce",
    "SEED_DEMO_DATA": "false",
    "ALLOW_DEV_AUTH": "false",
    "KNOWLEDGE_RUNTIME_VERSION": "legacy",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_AI_ENABLED": "false",
    "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
    "PROVIDER_RUNTIME_KILL_SWITCH": "true",
    "PRIVATE_AI_RUNTIME_ENABLED": "false",
    "ENABLE_OUTBOUND_DISPATCH": "false",
    "OUTBOUND_PROVIDER": "disabled",
    "WHATSAPP_NATIVE_ENABLED": "false",
    "WHATSAPP_DISPATCH_MODE": "disabled",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED": "false",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED": "false",
    "SPEEDAF_CANCEL_ENABLED": "false",
    "OPERATIONS_DISPATCH_MODE": "disabled",
    "OPERATIONS_DISPATCH_ADAPTER": "disabled",
}
bad = [
    f"{key}={values.get(key)!r}"
    for key, expected_value in expected.items()
    if values.get(key, "").lower() != expected_value
]
if bad:
    raise SystemExit("unsafe RC configuration: " + ", ".join(bad))
print("RC_ENV_VALID=true")
PY

SOURCE_SHA="${GIT_SHA}"
IMAGE_TAG_VALUE="${RC_IMAGE_TAG}"
if [[ -n "${RC_SOURCE_SHA:-}" && "${RC_SOURCE_SHA}" != "${SOURCE_SHA}" ]]; then
  echo "RC_SOURCE_SHA does not match GIT_SHA" >&2
  exit 2
fi
printf '%s\n' "${SOURCE_SHA}" > "${EVIDENCE_DIR}/source-sha.txt"
printf '%s\n' "${IMAGE_TAG_VALUE}" > "${EVIDENCE_DIR}/image-tag.txt"

set_stage pull-base-images
docker pull "${RC_POSTGRES_IMAGE}" >/dev/null
docker pull "${RC_NGINX_IMAGE}" >/dev/null
docker image inspect "${RC_POSTGRES_IMAGE}" --format '{{index .RepoDigests 0}}' > "${EVIDENCE_DIR}/postgres-image-digest.txt"
docker image inspect "${RC_NGINX_IMAGE}" --format '{{index .RepoDigests 0}}' > "${EVIDENCE_DIR}/nginx-image-digest.txt"
if ! grep -Eq '@sha256:[0-9a-f]{64}$' "${EVIDENCE_DIR}/postgres-image-digest.txt"; then
  echo "PostgreSQL image did not resolve to a RepoDigest" >&2
  exit 2
fi
if ! grep -Eq '@sha256:[0-9a-f]{64}$' "${EVIDENCE_DIR}/nginx-image-digest.txt"; then
  echo "Nginx image did not resolve to a RepoDigest" >&2
  exit 2
fi

set_stage build-app-image
docker build \
  --file "${ROOT_DIR}/Dockerfile" \
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
output = Path(sys.argv[2])
origin = sys.argv[3]
text = compose_path.read_text(encoding="utf-8")
for forbidden in (
    "production_runtime",
    "/opt/nexus_helpdesk/data",
    "/run/nexus/ai_runtime_token",
    "whatsapp-sidecar",
    "external: true",
):
    if forbidden in text:
        raise SystemExit(f"forbidden production coupling in RC compose: {forbidden}")
postgres_block = text.split("  postgres-rc:\n", 1)[1].split("\n  migrate-rc:\n", 1)[0]
if "env_file:" in postgres_block:
    raise SystemExit("postgres-rc must not receive the application env file")
profile = {
    "schema": "nexus.osr.rc-test-safe-config.v2",
    "profile": "rc-test-isolated-v1",
    "compose_sha256": hashlib.sha256(compose_path.read_bytes()).hexdigest(),
    "browser_origin": origin,
    "database_service": "postgres-rc",
    "database_environment": ["POSTGRES_DB", "POSTGRES_PASSWORD", "POSTGRES_USER"],
    "network": "project_local_internal_rc",
    "storage": "project_named_volumes",
    "knowledge_runtime_adaptation": "legacy_isolated_rc_only_not_production_v2_parity",
    "provider_candidate_enabled": False,
    "real_outbound_enabled": False,
    "whatsapp_enabled": False,
    "speedaf_writes_enabled": False,
}
output.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

set_stage start-postgres
compose up -d postgres-rc
wait_for_health postgres-rc 60

set_stage resolve-migration-head
heads_output="$(compose run --rm --no-deps -T migrate-rc sh -ec 'cd /app/backend && alembic heads')"
printf '%s\n' "${heads_output}" > "${EVIDENCE_DIR}/migration-head.txt"
mapfile -t migration_heads < <(printf '%s\n' "${heads_output}" | awk 'NF {print $1}')
if [[ "${#migration_heads[@]}" -ne 1 ]]; then
  echo "RC requires exactly one Alembic head" >&2
  exit 2
fi
MIGRATION_HEAD="${migration_heads[0]}"
if [[ ! "${MIGRATION_HEAD}" =~ ^[0-9]{8}_[0-9]{4}$ ]]; then
  echo "Unexpected Alembic head format: ${MIGRATION_HEAD}" >&2
  exit 2
fi

set_stage migrate-database
compose run --rm --no-deps -T migrate-rc sh -ec 'cd /app/backend && alembic upgrade head' | tee "${EVIDENCE_DIR}/migration.txt"
current_output="$(compose run --rm --no-deps -T migrate-rc sh -ec 'cd /app/backend && alembic current')"
printf '%s\n' "${current_output}" > "${EVIDENCE_DIR}/migration-current.txt"
MIGRATION_CURRENT="$(printf '%s\n' "${current_output}" | awk 'NF {print $1; exit}')"
if [[ "${MIGRATION_CURRENT}" != "${MIGRATION_HEAD}" ]]; then
  echo "Alembic current ${MIGRATION_CURRENT} does not match head ${MIGRATION_HEAD}" >&2
  exit 2
fi

set_stage seed-webchat-origin
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
origin = normalize_public_origin(os.environ["RC_PUBLIC_ORIGIN"])
db = SessionLocal()
try:
    tenant_key = os.environ["RC_TEST_TENANT_KEY"]
    tenants = db.query(Tenant).filter(Tenant.tenant_key == tenant_key).all()
    if len(tenants) != 1 or not tenants[0].is_active:
        raise SystemExit("RC Tenant seed is not authoritative and active")
    rows = db.query(WebchatPublicOriginBinding).filter(
        WebchatPublicOriginBinding.normalized_origin == origin
    ).all()
    if len(rows) != 1:
        raise SystemExit("RC Origin seed is not idempotent")
    row = rows[0]
    expected = {
        "tenant_key": os.environ["RC_TEST_TENANT_KEY"],
        "channel_key": os.environ["RC_TEST_CHANNEL_KEY"],
        "is_active": True,
    }
    actual = {
        "tenant_key": row.tenant_key,
        "channel_key": row.channel_key,
        "is_active": bool(row.is_active),
    }
    if actual != expected:
        raise SystemExit("RC Origin seed values mismatch")
    print(json.dumps({
        "schema": "nexus.osr.rc-test-seed-verification.v1",
        "status": "pass",
        "origin": origin,
        "row_count": 1,
        "tenant_principal_count": 1,
        "tenant_principal_active": True,
        **actual,
    }, indent=2, sort_keys=True))
finally:
    db.close()
PY

set_stage seed-operator
compose run --rm --no-deps -T app-rc python - <<'PY'
import os
from sqlalchemy import func
from app.auth_service import hash_password
from app.db import SessionLocal
from app.enums import UserRole
from app.model_registry import register_all_models
from app.models import Tenant, User
from app.services.tenant_authority import (
    RUNTIME_TENANT_ASSIGNMENT_SOURCE,
    RUNTIME_TENANT_ASSIGNMENT_VERSION,
)

register_all_models()
username = os.environ["RC_TEST_ADMIN_USERNAME"].strip()
password = os.environ["RC_TEST_ADMIN_PASSWORD"]
tenant_key = os.environ["RC_TEST_TENANT_KEY"].strip()
db = SessionLocal()
try:
    tenant = db.query(Tenant).filter(Tenant.tenant_key == tenant_key).first()
    if tenant is None or not tenant.is_active:
        raise SystemExit("RC_TEST_OPERATOR_FAILED reason=tenant_principal_missing")
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
    else:
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
compose up -d app-rc nginx-rc worker-outbound-rc worker-background-rc worker-webchat-ai-rc worker-handoff-snapshot-rc

wait_for_url() {
  local url="$1"
  local attempts="${2:-90}"
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 5 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for ${url}" >&2
  return 1
}
wait_for_url "${BASE_URL%/}/readyz"
for service in app-rc nginx-rc worker-outbound-rc worker-background-rc worker-webchat-ai-rc worker-handoff-snapshot-rc; do
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
compose exec -T app-rc python /app/scripts/release/rc_test_side_effects.py > "${EVIDENCE_DIR}/side-effect-safety.json"

set_stage network-proof
app_container="$(compose ps -q app-rc)"
nginx_container="$(compose ps -q nginx-rc)"
rc_network="${PROJECT_NAME}_rc"
edge_network="${PROJECT_NAME}_edge"
python3 - "${app_container}" "${nginx_container}" "${rc_network}" "${edge_network}" "${EVIDENCE_DIR}/network-safety.json" <<'PY'
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
    "app_external_network_attached": False,
    "production_network_joined": False,
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

set_stage browser-smoke
browser_smoke_flag="${RC_RUN_BROWSER_SMOKE:-false}"
if [[ "${browser_smoke_flag,,}" =~ ^(1|true|yes|on)$ ]]; then
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
if [[ -n "${remaining_containers}${remaining_volumes}${remaining_networks}" ]]; then
  echo "RC resources remain after teardown" >&2
  exit 2
fi
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
python3 "${ROOT_DIR}/scripts/release/validate_rc_test_manifest.py" "${EVIDENCE_DIR}/candidate-manifest.json"

set_stage completed
echo "RC0_TEST_DEPLOYABLE=true"
echo "PRODUCTION_READY=false"
echo "FULL_OSR_AUTOMATION=NO_GO"
echo "evidence_dir=${EVIDENCE_DIR}"
