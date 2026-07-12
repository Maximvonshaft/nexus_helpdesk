#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/deploy/docker-compose.rc-test.yml"
ENV_FILE="${RC_ENV_FILE:-${ROOT_DIR}/deploy/.env.rc-test}"
EVIDENCE_DIR="${RC_EVIDENCE_DIR:-${ROOT_DIR}/artifacts/rc-test}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nexus_rc_test}"
BASE_URL="${RC_BASE_URL:-http://127.0.0.1:${RC_APP_PORT:-18083}}"
KEEP_STACK="${KEEP_RC_STACK:-false}"

export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
mkdir -p "${EVIDENCE_DIR}"
rm -f "${EVIDENCE_DIR}"/*.json "${EVIDENCE_DIR}"/*.txt 2>/dev/null || true

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

collect_failure_evidence() {
  set +e
  compose ps --all > "${EVIDENCE_DIR}/compose-ps-failure.txt" 2>&1
  compose logs --no-color --tail=200 \
    app-rc worker-outbound-rc worker-background-rc worker-webchat-ai-rc worker-handoff-snapshot-rc \
    > "${EVIDENCE_DIR}/bounded-failure-logs.txt" 2>&1
  if command -v python3 >/dev/null 2>&1 && [[ -f "${ROOT_DIR}/scripts/security/scan_artifacts.py" ]]; then
    python3 "${ROOT_DIR}/scripts/security/scan_artifacts.py" \
      --root "${ROOT_DIR}" \
      --output "${EVIDENCE_DIR}/failure-evidence-scan.json" \
      "${EVIDENCE_DIR}" >/dev/null 2>&1 || true
  fi
  set -e
}

cleanup_stack() {
  local exit_code=$?
  if [[ "${exit_code}" -ne 0 ]]; then
    collect_failure_evidence
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

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}; copy deploy/.env.rc-test.example and replace placeholders." >&2
  exit 2
fi

set -a
# The RC env file is repository-owner controlled and generated locally/inside CI.
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

python3 - "${ENV_FILE}" <<'PY'
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

path = Path(sys.argv[1])
values = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"invalid env line: {line[:80]}")
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

required = [
    "RC_IMAGE_TAG", "IMAGE_TAG", "GIT_SHA", "FRONTEND_BUILD_SHA",
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
    "SECRET_KEY", "RUNTIME_CONTRACT_SIGNING_SECRET",
    "RC_TEST_ADMIN_USERNAME", "RC_TEST_ADMIN_PASSWORD",
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

db = urlparse(values["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
if db.hostname != "postgres-rc":
    raise SystemExit("DATABASE_URL must target isolated postgres-rc")
if values.get("APP_ENV") != "production":
    raise SystemExit("APP_ENV must be production")
if values.get("ALLOWED_ORIGINS") != "https://rc-test.invalid":
    raise SystemExit("ALLOWED_ORIGINS must use the reserved RC test origin")
if values.get("WEBCHAT_ALLOWED_ORIGINS") != "https://rc-test.invalid":
    raise SystemExit("WEBCHAT_ALLOWED_ORIGINS must use the reserved RC test origin")

expected = {
    "AUTO_INIT_DB": "false",
    "SEED_DEMO_DATA": "false",
    "ALLOW_DEV_AUTH": "false",
    "KNOWLEDGE_RUNTIME_VERSION": "legacy",
    "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
    "WEBCHAT_AI_ENABLED": "false",
    "PROVIDER_RUNTIME_ENABLED": "false",
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
bad = [f"{key}={values.get(key)!r}" for key, expected_value in expected.items() if values.get(key, "").lower() != expected_value]
if bad:
    raise SystemExit("unsafe RC configuration: " + ", ".join(bad))
print("RC_ENV_VALID=true")
PY

SOURCE_SHA="${GIT_SHA}"
IMAGE_TAG="${RC_IMAGE_TAG}"
BUILD_TIME_VALUE="${BUILD_TIME}"
APP_VERSION_VALUE="${APP_VERSION}"

if [[ -n "${RC_SOURCE_SHA:-}" && "${RC_SOURCE_SHA}" != "${SOURCE_SHA}" ]]; then
  echo "RC_SOURCE_SHA does not match GIT_SHA" >&2
  exit 2
fi

printf '%s\n' "${SOURCE_SHA}" > "${EVIDENCE_DIR}/source-sha.txt"
printf '%s\n' "${IMAGE_TAG}" > "${EVIDENCE_DIR}/image-tag.txt"

docker build \
  --file "${ROOT_DIR}/Dockerfile" \
  --build-arg "GIT_SHA=${SOURCE_SHA}" \
  --build-arg "BUILD_TIME=${BUILD_TIME_VALUE}" \
  --build-arg "IMAGE_TAG=${IMAGE_TAG}" \
  --build-arg "APP_VERSION=${APP_VERSION_VALUE}" \
  --build-arg "FRONTEND_BUILD_SHA=${SOURCE_SHA}" \
  --tag "${IMAGE_TAG}" \
  "${ROOT_DIR}"

docker image inspect "${IMAGE_TAG}" --format '{{.Id}}' > "${EVIDENCE_DIR}/image-id.txt"

compose config --quiet
compose config --services > "${EVIDENCE_DIR}/compose-services.txt"
compose config --images > "${EVIDENCE_DIR}/compose-images.txt"

python3 - "${COMPOSE_FILE}" "${EVIDENCE_DIR}/safe-config.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

compose_path = Path(sys.argv[1])
output = Path(sys.argv[2])
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

profile = {
    "schema": "nexus.osr.rc-test-safe-config.v1",
    "profile": "rc-test-isolated-v1",
    "compose_sha256": hashlib.sha256(compose_path.read_bytes()).hexdigest(),
    "database_service": "postgres-rc",
    "network": "project_local_internal_rc",
    "storage": "project_named_volumes",
    "provider_candidate_enabled": False,
    "real_outbound_enabled": False,
    "whatsapp_enabled": False,
    "speedaf_writes_enabled": False,
}
output.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

compose up -d postgres-rc
compose run --rm -T migrate-rc | tee "${EVIDENCE_DIR}/migration.txt"

compose run --rm -T app-rc python - <<'PY'
import os
from sqlalchemy import func
from app.auth_service import hash_password
from app.db import SessionLocal
from app.enums import UserRole
from app.models import User

username = os.environ["RC_TEST_ADMIN_USERNAME"].strip()
password = os.environ["RC_TEST_ADMIN_PASSWORD"]
db = SessionLocal()
try:
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
    db.commit()
finally:
    db.close()
print("RC_TEST_OPERATOR_READY=true")
PY

compose up -d \
  app-rc \
  worker-outbound-rc \
  worker-background-rc \
  worker-webchat-ai-rc \
  worker-handoff-snapshot-rc

wait_for_url() {
  local url="$1"
  local attempts="${2:-90}"
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 5 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "${i}" -eq "${attempts}" ]]; then
      echo "Timed out waiting for ${url}" >&2
      return 1
    fi
    sleep 2
  done
}
wait_for_url "${BASE_URL%/}/readyz"

wait_for_health() {
  local service="$1"
  local attempts="${2:-60}"
  local container_id status i
  container_id="$(compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "Missing container for ${service}" >&2
    return 1
  fi
  for i in $(seq 1 "${attempts}"); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
    if [[ "${status}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]]; then
      echo "${service} entered ${status}" >&2
      return 1
    fi
    sleep 2
  done
  echo "Timed out waiting for healthy ${service}" >&2
  return 1
}

for service in \
  app-rc \
  worker-outbound-rc \
  worker-background-rc \
  worker-webchat-ai-rc \
  worker-handoff-snapshot-rc
do
  wait_for_health "${service}"
done
compose ps > "${EVIDENCE_DIR}/compose-ps-healthy.txt"

python3 - "${BASE_URL}" "${EVIDENCE_DIR}" "${SOURCE_SHA}" "${IMAGE_TAG}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

base = sys.argv[1].rstrip("/")
out = Path(sys.argv[2])
source_sha = sys.argv[3]
image_tag = sys.argv[4]
origin = "https://rc-test.invalid"
username = os.environ["RC_TEST_ADMIN_USERNAME"]
password = os.environ["RC_TEST_ADMIN_PASSWORD"]


def request(path, *, method="GET", payload=None, headers=None, expected=(200,)):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        merged["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=merged, method=method)
    response_headers = {}
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status = response.status
            response_headers = dict(response.headers.items())
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        body = exc.read()
    if status not in expected:
        raise SystemExit(f"{method} {path} returned {status}: {body[:400]!r}")
    content_type = response_headers.get("Content-Type", "")
    if "application/json" in content_type or (body and body[:1] in (b"{", b"[")):
        return status, json.loads(body.decode("utf-8"))
    return status, body.decode("utf-8", errors="replace")


_, health = request("/healthz")
_, ready = request("/readyz")
if health.get("status") != "ok" or ready.get("status") != "ready":
    raise SystemExit("health/readiness not ready")
for payload in (health, ready):
    if payload.get("git_sha") != source_sha:
        raise SystemExit("runtime git_sha mismatch")
    if payload.get("frontend_build_sha") != source_sha:
        raise SystemExit("frontend build SHA mismatch")
    if payload.get("image_tag") != image_tag:
        raise SystemExit("image tag mismatch")
if not ready.get("migration_revision"):
    raise SystemExit("migration revision missing")

out.joinpath("healthz.json").write_text(json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8")
out.joinpath("readyz.json").write_text(json.dumps(ready, indent=2, sort_keys=True) + "\n", encoding="utf-8")

status, login_html = request("/login", headers={"Accept": "text/html"})
if status != 200 or "<html" not in login_html.lower():
    raise SystemExit("login SPA route did not render")

request(
    "/api/auth/login",
    method="POST",
    payload={"username": username, "password": "intentionally-wrong-password"},
    expected=(401,),
)
_, login = request(
    "/api/auth/login",
    method="POST",
    payload={"username": username, "password": password},
)
admin_token = login.get("access_token")
if not isinstance(admin_token, str) or len(admin_token) < 20:
    raise SystemExit("admin login did not return a token")

web_headers = {"Origin": origin}
_, init = request(
    "/api/webchat/init",
    method="POST",
    headers=web_headers,
    payload={
        "tenant_key": "rc-test",
        "channel_key": "website",
        "visitor_name": "RC Synthetic Visitor",
        "origin": origin,
        "page_url": origin + "/help",
    },
)
conversation_id = init.get("conversation_id")
visitor_token = init.get("visitor_token")
if not isinstance(conversation_id, str) or not conversation_id.startswith("wc_"):
    raise SystemExit("invalid webchat conversation id")
if not isinstance(visitor_token, str) or len(visitor_token) < 20:
    raise SystemExit("invalid visitor token")

visitor_headers = {"Origin": origin, "X-Webchat-Visitor-Token": visitor_token}
_, sent = request(
    f"/api/webchat/conversations/{conversation_id}/messages",
    method="POST",
    headers=visitor_headers,
    payload={"body": "RC synthetic delivery-status test message", "client_message_id": "rc0-smoke-1"},
)
if not isinstance(sent, dict):
    raise SystemExit("webchat send response invalid")
_, polled = request(
    f"/api/webchat/conversations/{conversation_id}/messages",
    headers=visitor_headers,
)
messages = polled.get("messages") if isinstance(polled, dict) else None
if not isinstance(messages, list) or not any(
    item.get("direction") == "visitor" and "RC synthetic" in str(item.get("body"))
    for item in messages if isinstance(item, dict)
):
    raise SystemExit("visitor message was not persisted")

_, admin_conversations = request(
    "/api/webchat/admin/conversations?limit=20",
    headers={"Authorization": f"Bearer {admin_token}"},
)
if not isinstance(admin_conversations, list) or not any(
    item.get("conversation_id") == conversation_id
    for item in admin_conversations if isinstance(item, dict)
):
    raise SystemExit("operator API cannot read the synthetic conversation")

summary = {
    "schema": "nexus.osr.rc-test-http-smoke.v1",
    "health": "pass",
    "readiness": "pass",
    "login_route": "pass",
    "invalid_login_rejected": "pass",
    "operator_login": "pass",
    "webchat_init_send_poll": "pass",
    "operator_conversation_read": "pass",
}
out.joinpath("http-core-smoke.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print("RC_HTTP_CORE_SMOKE=true")
PY

compose exec -T app-rc python - > "${EVIDENCE_DIR}/side-effect-safety.json" <<'PY'
import json
import os

expected = {
    "PROVIDER_RUNTIME_ENABLED": "false",
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
bad = {
    key: {"actual": os.getenv(key), "expected": value}
    for key, value in expected.items()
    if (os.getenv(key) or "").strip().lower() != value
}
if bad:
    raise SystemExit("unsafe side-effect configuration")
print(json.dumps({
    "schema": "nexus.osr.rc-test-side-effect-safety.v1",
    "status": "pass",
    "provider_candidate_enabled": False,
    "real_outbound_enabled": False,
    "whatsapp_enabled": False,
    "speedaf_write_enabled": False,
    "operations_dispatch_enabled": False,
}, indent=2, sort_keys=True))
PY

browser_smoke_flag="${RC_RUN_BROWSER_SMOKE:-false}"
if [[ "${browser_smoke_flag,,}" =~ ^(1|true|yes|on)$ ]]; then
  (
    cd "${ROOT_DIR}/webapp"
    PLAYWRIGHT_BASE_URL="${BASE_URL}" \
      npm run e2e -- --grep "login page renders|unauthenticated protected route redirects back to login"
  ) | tee "${EVIDENCE_DIR}/browser-smoke.txt"
else
  echo "RC_RUN_BROWSER_SMOKE must be true for a deployable candidate" >&2
  exit 2
fi

# Prove teardown while preserving evidence outside Docker volumes.
compose down --volumes --remove-orphans | tee "${EVIDENCE_DIR}/teardown.txt"
remaining="$(compose ps -q --all)"
if [[ -n "${remaining}" ]]; then
  echo "RC containers remain after teardown" >&2
  exit 2
fi
trap - EXIT

python3 - "${EVIDENCE_DIR}" "${SOURCE_SHA}" "${IMAGE_TAG}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
source_sha = sys.argv[2]
image_tag = sys.argv[3]
ready = json.loads(root.joinpath("readyz.json").read_text(encoding="utf-8"))
safe_config = json.loads(root.joinpath("safe-config.json").read_text(encoding="utf-8"))
image_id = root.joinpath("image-id.txt").read_text(encoding="utf-8").strip()
safe_digest = "sha256:" + hashlib.sha256(
    json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

manifest = {
    "schema": "nexus.osr.rc-test-candidate.v1",
    "release_class": "controlled_test_deployment",
    "decision": "RC0_TEST_DEPLOYABLE",
    "candidate": {
        "source_sha": source_sha,
        "frontend_build_sha": source_sha,
        "image_tag": image_tag,
        "image_id": image_id,
        "migration_revision": ready["migration_revision"],
        "config_profile": "rc-test-isolated-v1",
        "config_digest": safe_digest,
    },
    "checks": {
        "image_build": "pass",
        "compose_validation": "pass",
        "migration": "pass",
        "application_ready": "pass",
        "workers_healthy": "pass",
        "http_core_smoke": "pass",
        "browser_smoke": "pass",
        "side_effect_safety": "pass",
        "teardown": "pass",
    },
    "safety": {
        "production_data_used": False,
        "production_network_joined": False,
        "provider_candidate_enabled": False,
        "real_outbound_enabled": False,
        "whatsapp_enabled": False,
        "speedaf_write_enabled": False,
        "production_ready": False,
        "full_osr_automation": "NO_GO",
        "test_environment_isolated": True,
    },
    "evidence": {
        "health": "healthz.json",
        "readiness": "readyz.json",
        "http_core_smoke": "http-core-smoke.json",
        "browser_smoke": "browser-smoke.txt",
        "workers": "compose-ps-healthy.txt",
        "migration": "migration.txt",
        "side_effect_safety": "side-effect-safety.json",
        "safe_config": "safe-config.json",
        "teardown": "teardown.txt",
    },
}
root.joinpath("candidate-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

python3 "${ROOT_DIR}/scripts/release/validate_rc_test_manifest.py" \
  "${EVIDENCE_DIR}/candidate-manifest.json"

echo "RC0_TEST_DEPLOYABLE=true"
echo "PRODUCTION_READY=false"
echo "FULL_OSR_AUTOMATION=NO_GO"
echo "evidence_dir=${EVIDENCE_DIR}"
