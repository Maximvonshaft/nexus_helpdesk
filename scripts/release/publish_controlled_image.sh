#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${CANDIDATE_IMAGE:?CANDIDATE_IMAGE required}"
: "${RELEASE_IMAGE_DIR:?RELEASE_IMAGE_DIR required}"
: "${CONTROLLED_DIR:?CONTROLLED_DIR required}"
: "${GHCR_TOKEN:?GHCR_TOKEN required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY required}"
: "${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER required}"
: "${GITHUB_ACTOR:?GITHUB_ACTOR required}"

mkdir -p "${CONTROLLED_DIR}"
registry_image="ghcr.io/$(printf '%s/%s' "${GITHUB_REPOSITORY_OWNER}" "${GITHUB_REPOSITORY#*/}" | tr '[:upper:]' '[:lower:]')"
tag="controlled-${SOURCE_SHA}"
local_image_id="$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')"
test "${local_image_id}" = "$(jq -r '.candidate.image_id' artifacts/rc-test/candidate-manifest.json)"
test "${local_image_id}" = "$(jq -r '.image_id' "${RELEASE_IMAGE_DIR}/release-image-manifest.json")"
printf '%s' "${GHCR_TOKEN}" | docker login ghcr.io --username "${GITHUB_ACTOR}" --password-stdin
docker tag "${CANDIDATE_IMAGE}" "${registry_image}:${tag}"
docker push "${registry_image}:${tag}"
repo_digest="$(docker image inspect "${registry_image}:${tag}" --format '{{index .RepoDigests 0}}')"
registry_digest="${repo_digest#*@}"
test "${repo_digest}" = "${registry_image}@${registry_digest}"
[[ "${registry_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image rm "${registry_image}:${tag}"
docker pull "${registry_image}@${registry_digest}"
pulled_image_id="$(docker image inspect "${registry_image}@${registry_digest}" --format '{{.Id}}')"
test "${pulled_image_id}" = "${local_image_id}"
REGISTRY_IMAGE="${registry_image}" \
REGISTRY_DIGEST="${registry_digest}" \
LOCAL_IMAGE_ID="${local_image_id}" \
PULLED_IMAGE_ID="${pulled_image_id}" \
python - "${CONTROLLED_DIR}/registry-publish-receipt.json" <<'PY'
import json
import os
import sys
from pathlib import Path

image = os.environ["REGISTRY_IMAGE"]
digest = os.environ["REGISTRY_DIGEST"]
payload = {
    "schema": "nexus.osr.registry-publish-receipt.v1",
    "status": "pass",
    "source_sha": os.environ["SOURCE_SHA"],
    "registry_image": image,
    "registry_digest": digest,
    "registry_reference": f"{image}@{digest}",
    "local_image_id": os.environ["LOCAL_IMAGE_ID"],
    "pulled_image_id": os.environ["PULLED_IMAGE_ID"],
    "image_pushed": True,
    "deployment_performed": False,
}
Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
docker logout ghcr.io
