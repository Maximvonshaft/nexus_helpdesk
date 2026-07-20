#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${CANDIDATE_IMAGE:?CANDIDATE_IMAGE required}"
: "${RELEASE_IMAGE_DIR:?RELEASE_IMAGE_DIR required}"

mkdir -p "${RELEASE_IMAGE_DIR}"

expected_migration_head="$(
  python - <<'PY'
from pathlib import Path

from scripts.release.generate_rc_test_env import discover_alembic_head

print(discover_alembic_head(Path("backend/alembic/versions")))
PY
)"
test -n "${expected_migration_head}"
migration_contract_raw="${RELEASE_IMAGE_DIR}/migration-readiness-contract.raw"
docker exec -i \
  -e EXPECTED_MIGRATION_HEAD="${expected_migration_head}" \
  -e NEXUS_ASSURANCE_SOURCE_SHA="${SOURCE_SHA}" \
  -e PYTHONPATH=/app/backend \
  nexus-ci-candidate \
  python - <<'PY' > "${migration_contract_raw}"
import json
import os

from starlette.responses import Response

from app.main import readyz

result = readyz()
if isinstance(result, Response):
    status_code = result.status_code
    payload = json.loads(bytes(result.body).decode("utf-8"))
else:
    status_code = 200
    payload = result
contract = {
    "schema": "nexus.migration-readiness-contract.v1",
    "source_sha": os.environ["NEXUS_ASSURANCE_SOURCE_SHA"],
    "expected_migration_head": os.environ["EXPECTED_MIGRATION_HEAD"],
    "http_status": status_code,
    "payload": payload,
}
print("NEXUS_MIGRATION_READINESS_CONTRACT=" + json.dumps(contract, sort_keys=True))
PY
sed -n 's/^NEXUS_MIGRATION_READINESS_CONTRACT=//p' "${migration_contract_raw}" \
  | tail -n 1 > "${RELEASE_IMAGE_DIR}/migration-readiness-contract.json"
rm -f "${migration_contract_raw}"
test -s "${RELEASE_IMAGE_DIR}/migration-readiness-contract.json"
jq -e \
  --arg source_sha "${SOURCE_SHA}" \
  --arg expected "${expected_migration_head}" \
  '.schema == "nexus.migration-readiness-contract.v1"
   and .source_sha == $source_sha
   and .expected_migration_head == $expected
   and .http_status == 200
   and .payload.status == "ready"
   and .payload.database == "ok"
   and .payload.migration.ok == true
   and .payload.migration.required == true
   and .payload.migration.expected == $expected
   and .payload.migration.observed == $expected
   and .payload.migration_revision == $expected
   and .payload.reason_codes == []' \
  "${RELEASE_IMAGE_DIR}/migration-readiness-contract.json" >/dev/null

python scripts/security/sanitize_image_sbom.py \
  --input "${RELEASE_IMAGE_DIR}/image.raw.cdx.json" \
  --frontend-input "${RELEASE_IMAGE_DIR}/frontend.raw.cdx.json" \
  --overrides config/security/container-license-metadata-overrides.json \
  --output "${RELEASE_IMAGE_DIR}/image.preliminary.cdx.json"
python scripts/security/finalize_image_sbom.py \
  --input "${RELEASE_IMAGE_DIR}/image.preliminary.cdx.json" \
  --overrides config/security/container-license-metadata-overrides.json \
  --output "${RELEASE_IMAGE_DIR}/image.safe.cdx.json"
policy_date="$(date -u +%F)"
printf '%s\n' "${policy_date}" > "${RELEASE_IMAGE_DIR}/policy-evaluated-on.txt"
python scripts/security/validate_release_image_policy_inputs.py \
  --trivy "${RELEASE_IMAGE_DIR}/trivy.raw.json" \
  --sbom "${RELEASE_IMAGE_DIR}/image.safe.cdx.json" \
  --vulnerability-exceptions config/security/container-vulnerability-exceptions.json \
  --license-policy config/security/container-license-policy.json \
  --license-compliance config/security/container-license-compliance.json \
  --output "${RELEASE_IMAGE_DIR}/policy-input-validation.json" \
  --today "${policy_date}"
docker run --rm --entrypoint python "${CANDIDATE_IMAGE}" \
  /app/scripts/security/extract_installed_license_evidence.py \
  --package psycopg \
  --package psycopg-binary \
  > "${RELEASE_IMAGE_DIR}/installed-license-evidence.json"
python scripts/security/release_image_assurance.py vulnerabilities \
  --report "${RELEASE_IMAGE_DIR}/trivy.raw.json" \
  --exceptions config/security/container-vulnerability-exceptions.json \
  --output "${RELEASE_IMAGE_DIR}/vulnerability-summary.json" \
  --today "${policy_date}"
python scripts/security/release_image_assurance.py licenses \
  --sbom "${RELEASE_IMAGE_DIR}/image.safe.cdx.json" \
  --policy config/security/container-license-policy.json \
  --output "${RELEASE_IMAGE_DIR}/license-summary.json" \
  --today "${policy_date}"
image_id="$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')"
python scripts/security/release_image_assurance.py manifest \
  --source-sha "${SOURCE_SHA}" \
  --image-id "${image_id}" \
  --sbom "${RELEASE_IMAGE_DIR}/image.safe.cdx.json" \
  --vulnerabilities "${RELEASE_IMAGE_DIR}/vulnerability-summary.json" \
  --licenses "${RELEASE_IMAGE_DIR}/license-summary.json" \
  --output "${RELEASE_IMAGE_DIR}/release-image-manifest.json"
python scripts/security/verify_license_compliance.py \
  --compliance config/security/container-license-compliance.json \
  --policy config/security/container-license-policy.json \
  --sbom "${RELEASE_IMAGE_DIR}/image.safe.cdx.json" \
  --installed "${RELEASE_IMAGE_DIR}/installed-license-evidence.json" \
  --notice THIRD_PARTY_NOTICES.md \
  --output "${RELEASE_IMAGE_DIR}/license-compliance-evidence.json" \
  --today "${policy_date}"
python scripts/security/bind_release_image_compliance.py \
  --source-sha "${SOURCE_SHA}" \
  --image-id "${image_id}" \
  --manifest "${RELEASE_IMAGE_DIR}/release-image-manifest.json" \
  --policy-input-validation "${RELEASE_IMAGE_DIR}/policy-input-validation.json" \
  --compliance "${RELEASE_IMAGE_DIR}/license-compliance-evidence.json" \
  --installed "${RELEASE_IMAGE_DIR}/installed-license-evidence.json" \
  --output "${RELEASE_IMAGE_DIR}/release-image-compliance-binding.json"
python - "${RELEASE_IMAGE_DIR}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])

def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

payload = {
    "schema_version": "nexus_raw_release_evidence_digests_v2",
    "trivy_report_sha256": digest(root / "trivy.raw.json"),
    "raw_cyclonedx_sha256": digest(root / "image.raw.cdx.json"),
    "raw_frontend_cyclonedx_sha256": digest(root / "frontend.raw.cdx.json"),
}
(root / "raw-evidence-digests.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

test "$(jq -r '.status' "${RELEASE_IMAGE_DIR}/release-image-manifest.json")" = "pass"
test "$(jq -r '.status' "${RELEASE_IMAGE_DIR}/release-image-compliance-binding.json")" = "pass"
test "$(jq -r '.image_id' "${RELEASE_IMAGE_DIR}/release-image-manifest.json")" = "${image_id}"

cleanup_code=0
raw_files=(
  "${RELEASE_IMAGE_DIR}/trivy.raw.json"
  "${RELEASE_IMAGE_DIR}/image.raw.cdx.json"
  "${RELEASE_IMAGE_DIR}/frontend.raw.cdx.json"
  "${RELEASE_IMAGE_DIR}/image.preliminary.cdx.json"
  "${RELEASE_IMAGE_DIR}/image.preliminary.cdx.json.summary.json"
)
rm -f "${raw_files[@]}" || cleanup_code=1
for raw_path in "${raw_files[@]}"; do
  if [ -e "${raw_path}" ]; then
    cleanup_code=1
  fi
done
printf '%s\n' "${cleanup_code}" > "${RELEASE_IMAGE_DIR}/raw-cleanup-exit-code"
test "${cleanup_code}" = "0"

set +e
python scripts/security/scan_artifacts.py \
  --root . \
  --output "${RELEASE_IMAGE_DIR}/artifact-scan.json" \
  "${RELEASE_IMAGE_DIR}/runtime-smoke-summary.txt" \
  THIRD_PARTY_NOTICES.md
artifact_scan_code=$?
set -e
printf '%s\n' "${artifact_scan_code}" > "${RELEASE_IMAGE_DIR}/artifact-scan-exit-code"
test "${artifact_scan_code}" = "0"
test "$(jq -r '.status' "${RELEASE_IMAGE_DIR}/artifact-scan.json")" = "pass"

python scripts/security/validate_release_image_evidence.py \
  --sbom "${RELEASE_IMAGE_DIR}/image.safe.cdx.json" \
  --sbom-summary "${RELEASE_IMAGE_DIR}/image.safe.cdx.json.summary.json" \
  --raw-digests "${RELEASE_IMAGE_DIR}/raw-evidence-digests.json" \
  --vulnerabilities "${RELEASE_IMAGE_DIR}/vulnerability-summary.json" \
  --licenses "${RELEASE_IMAGE_DIR}/license-summary.json" \
  --manifest "${RELEASE_IMAGE_DIR}/release-image-manifest.json" \
  --output "${RELEASE_IMAGE_DIR}/structured-evidence-scan.json"
test "$(jq -r '.status' "${RELEASE_IMAGE_DIR}/structured-evidence-scan.json")" = "pass"
