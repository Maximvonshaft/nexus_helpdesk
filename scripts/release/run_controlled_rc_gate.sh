#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${RC_ENV_FILE:?RC_ENV_FILE required}"
: "${RC_PUBLIC_ORIGIN:?RC_PUBLIC_ORIGIN required}"
: "${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME required}"
: "${RC_POSTGRES_IMAGE_PIN:?RC_POSTGRES_IMAGE_PIN required}"
: "${RC_NGINX_IMAGE_PIN:?RC_NGINX_IMAGE_PIN required}"
: "${CANDIDATE_IMAGE:?CANDIDATE_IMAGE required}"

python -m pip install --disable-pip-version-check -r backend/requirements.txt
python -m py_compile \
  scripts/release/build_controlled_candidate_manifest.py \
  scripts/deploy/validate_controlled_server_preflight.py
python -m unittest -v \
  scripts.release.tests.test_build_controlled_candidate_manifest \
  scripts.release.tests.test_controlled_candidate_workflow_contract \
  scripts.deploy.tests.test_validate_controlled_server_preflight
python -m unittest discover -s scripts/release/tests -p 'test_*.py'
bash -n \
  scripts/release/run_rc_test_candidate.sh \
  scripts/release/run_controlled_rc_gate.sh \
  scripts/release/run_controlled_image_assurance.sh \
  scripts/release/manage_controlled_assurance_runtime.sh \
  scripts/release/publish_controlled_image.sh \
  scripts/release/finalize_controlled_candidate.sh
python scripts/release/generate_rc_test_env.py \
  --source-sha "${SOURCE_SHA}" \
  --compose-project "${COMPOSE_PROJECT_NAME}" \
  --origin "${RC_PUBLIC_ORIGIN}" \
  --output "${RC_ENV_FILE}"
controlled_app_version="controlled-${SOURCE_SHA:0:12}"
python - "${RC_ENV_FILE}" "${RC_POSTGRES_IMAGE_PIN}" "${RC_NGINX_IMAGE_PIN}" "${controlled_app_version}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
replacements = {
    "RC_POSTGRES_IMAGE": sys.argv[2],
    "RC_NGINX_IMAGE": sys.argv[3],
    "APP_VERSION": sys.argv[4],
}
lines, seen = [], set()
for raw in path.read_text(encoding="utf-8").splitlines():
    key, value = raw.split("=", 1)
    if key in replacements:
        value = replacements[key]
        seen.add(key)
    lines.append(f"{key}={value}")
if seen != set(replacements):
    raise SystemExit("controlled RC replacement keys missing")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
test "$(stat -c '%a' "${RC_ENV_FILE}")" = "600"
bash scripts/release/run_rc_test_candidate.sh
list_file="${RUNNER_TEMP:-/tmp}/rc-evidence-inputs.txt"
python scripts/release/validate_rc_test_evidence.py artifacts/rc-test --list-output "${list_file}"
mapfile -t evidence_files < "${list_file}"
test "${#evidence_files[@]}" -gt 0
python scripts/security/scan_artifacts.py \
  --root . \
  --output artifacts/rc-test/artifact-scan.json \
  "${evidence_files[@]}"
test "$(jq -r '.decision' artifacts/rc-test/candidate-manifest.json)" = "RC0_TEST_DEPLOYABLE"
test "$(jq -r '.candidate.source_sha' artifacts/rc-test/candidate-manifest.json)" = "${SOURCE_SHA}"
local_image_id="$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')"
test "${local_image_id}" = "$(jq -r '.candidate.image_id' artifacts/rc-test/candidate-manifest.json)"
