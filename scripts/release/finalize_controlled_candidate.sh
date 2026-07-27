#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${FINAL_DIR:?FINAL_DIR required}"
: "${REGISTRY_IMAGE:?REGISTRY_IMAGE required}"
: "${REGISTRY_DIGEST:?REGISTRY_DIGEST required}"
: "${LOCAL_IMAGE_ID:?LOCAL_IMAGE_ID required}"
: "${PULLED_IMAGE_ID:?PULLED_IMAGE_ID required}"
: "${ATTESTATION_ID:?ATTESTATION_ID required}"
: "${ATTESTATION_URL:?ATTESTATION_URL required}"
: "${SIDECAR_REGISTRY_IMAGE:?SIDECAR_REGISTRY_IMAGE required}"
: "${SIDECAR_REGISTRY_DIGEST:?SIDECAR_REGISTRY_DIGEST required}"
: "${SIDECAR_LOCAL_IMAGE_ID:?SIDECAR_LOCAL_IMAGE_ID required}"
: "${SIDECAR_PULLED_IMAGE_ID:?SIDECAR_PULLED_IMAGE_ID required}"
: "${SIDECAR_ATTESTATION_ID:?SIDECAR_ATTESTATION_ID required}"
: "${SIDECAR_ATTESTATION_URL:?SIDECAR_ATTESTATION_URL required}"

bash scripts/release/require_exact_current_main.sh

mkdir -p "${FINAL_DIR}"
cp artifacts/build/rc-test/candidate-manifest.json "${FINAL_DIR}/"
cp artifacts/build/release-image/release-image-manifest.json "${FINAL_DIR}/"
cp artifacts/build/release-image/release-image-compliance-binding.json "${FINAL_DIR}/"
cp artifacts/build/sidecar-image/sidecar-image-manifest.json "${FINAL_DIR}/"
cp artifacts/build/controlled/registry-publish-receipt.json "${FINAL_DIR}/"
cp artifacts/build/controlled/whatsapp-sidecar-registry-publish-receipt.json "${FINAL_DIR}/"
cp artifacts/recovery/recovery-evidence.json "${FINAL_DIR}/"
migration="$(jq -r '.candidate.migration_revision' "${FINAL_DIR}/candidate-manifest.json")"
frontend="$(jq -r '.candidate.frontend_build_sha' "${FINAL_DIR}/candidate-manifest.json")"
python scripts/release/build_controlled_candidate_manifest.py \
  --source-sha "${SOURCE_SHA}" \
  --registry-image "${REGISTRY_IMAGE}" \
  --registry-digest "${REGISTRY_DIGEST}" \
  --local-image-id "${LOCAL_IMAGE_ID}" \
  --pulled-image-id "${PULLED_IMAGE_ID}" \
  --migration-head "${migration}" \
  --frontend-sha "${frontend}" \
  --attestation-id "${ATTESTATION_ID}" \
  --attestation-url "${ATTESTATION_URL}" \
  --sidecar-registry-image "${SIDECAR_REGISTRY_IMAGE}" \
  --sidecar-registry-digest "${SIDECAR_REGISTRY_DIGEST}" \
  --sidecar-local-image-id "${SIDECAR_LOCAL_IMAGE_ID}" \
  --sidecar-pulled-image-id "${SIDECAR_PULLED_IMAGE_ID}" \
  --sidecar-attestation-id "${SIDECAR_ATTESTATION_ID}" \
  --sidecar-attestation-url "${SIDECAR_ATTESTATION_URL}" \
  --rc-manifest "${FINAL_DIR}/candidate-manifest.json" \
  --release-image-manifest "${FINAL_DIR}/release-image-manifest.json" \
  --compliance-binding "${FINAL_DIR}/release-image-compliance-binding.json" \
  --sidecar-image-manifest "${FINAL_DIR}/sidecar-image-manifest.json" \
  --recovery-evidence "${FINAL_DIR}/recovery-evidence.json" \
  --publish-receipt "${FINAL_DIR}/registry-publish-receipt.json" \
  --sidecar-publish-receipt "${FINAL_DIR}/whatsapp-sidecar-registry-publish-receipt.json" \
  --output "${FINAL_DIR}/controlled-candidate-manifest.json"
python scripts/release/scan_controlled_candidate_artifacts.py \
  --root . \
  --output "${FINAL_DIR}/artifact-scan.json" \
  "${FINAL_DIR}"/*.json
test "$(jq -r '.status' "${FINAL_DIR}/controlled-candidate-manifest.json")" = "pass"
test "$(jq -r '.safety.production_ready' "${FINAL_DIR}/controlled-candidate-manifest.json")" = "false"
test "$(jq -r '.safety.issue_533_go' "${FINAL_DIR}/controlled-candidate-manifest.json")" = "false"
test "$(jq -r '.safety.external_effects_authorized' "${FINAL_DIR}/controlled-candidate-manifest.json")" = "false"
