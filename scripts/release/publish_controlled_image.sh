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

bash scripts/release/require_exact_current_main.sh

mkdir -p "${CONTROLLED_DIR}"
registry_image="ghcr.io/$(printf '%s/%s' "${GITHUB_REPOSITORY_OWNER}" "${GITHUB_REPOSITORY#*/}" | tr '[:upper:]' '[:lower:]')"
tag="controlled-${SOURCE_SHA}"
local_image_id="$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{.Id}}')"
test "${local_image_id}" = "$(jq -r '.candidate.image_id' artifacts/rc-test/candidate-manifest.json)"
test "${local_image_id}" = "$(jq -r '.image_id' "${RELEASE_IMAGE_DIR}/release-image-manifest.json")"
local_image_env_json="$(docker image inspect "${CANDIDATE_IMAGE}" --format '{{json .Config.Env}}')"

logged_in=false
cleanup_registry_login() {
  if [[ "${logged_in}" == "true" ]]; then
    docker logout ghcr.io >/dev/null 2>&1 || true
  fi
}
trap cleanup_registry_login EXIT

printf '%s' "${GHCR_TOKEN}" | docker login ghcr.io --username "${GITHUB_ACTOR}" --password-stdin
logged_in=true
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
pulled_image_env_json="$(docker image inspect "${registry_image}@${registry_digest}" --format '{{json .Config.Env}}')"

REGISTRY_IMAGE="${registry_image}" \
REGISTRY_DIGEST="${registry_digest}" \
LOCAL_IMAGE_ID="${local_image_id}" \
PULLED_IMAGE_ID="${pulled_image_id}" \
LOCAL_IMAGE_ENV_JSON="${local_image_env_json}" \
PULLED_IMAGE_ENV_JSON="${pulled_image_env_json}" \
python - "${CONTROLLED_DIR}/registry-publish-receipt.json" <<'PY'
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sha40 = re.compile(r"^[0-9a-f]{40}$")
build_time_re = re.compile(r"^\d{8}T\d{6}Z$")
app_version_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
local_tag_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def image_env(raw: str) -> dict[str, str]:
    values = json.loads(raw)
    if not isinstance(values, list):
        raise SystemExit("image environment is not a list")
    result: dict[str, str] = {}
    for item in values:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key in result:
            raise SystemExit(f"duplicate image environment key: {key}")
        result[key] = value
    return result


local_env = image_env(os.environ["LOCAL_IMAGE_ENV_JSON"])
pulled_env = image_env(os.environ["PULLED_IMAGE_ENV_JSON"])
identity_keys = ("GIT_SHA", "FRONTEND_BUILD_SHA", "BUILD_TIME", "APP_VERSION", "IMAGE_TAG")
for key in identity_keys:
    if not local_env.get(key) or local_env.get(key) != pulled_env.get(key):
        raise SystemExit(f"image identity metadata mismatch: {key}")

source = os.environ["SOURCE_SHA"]
if not sha40.fullmatch(source):
    raise SystemExit("source SHA invalid")
if local_env["GIT_SHA"] != source or local_env["FRONTEND_BUILD_SHA"] != source:
    raise SystemExit("embedded source identity mismatch")
if not build_time_re.fullmatch(local_env["BUILD_TIME"]):
    raise SystemExit("embedded build time invalid")
datetime.strptime(local_env["BUILD_TIME"], "%Y%m%dT%H%M%SZ")
if not app_version_re.fullmatch(local_env["APP_VERSION"]):
    raise SystemExit("embedded app version invalid")
if not local_tag_re.fullmatch(local_env["IMAGE_TAG"]):
    raise SystemExit("embedded image tag invalid")

image = os.environ["REGISTRY_IMAGE"]
digest = os.environ["REGISTRY_DIGEST"]
payload = {
    "schema": "nexus.osr.registry-publish-receipt.v1",
    "status": "pass",
    "source_sha": source,
    "frontend_build_sha": local_env["FRONTEND_BUILD_SHA"],
    "build_time": local_env["BUILD_TIME"],
    "app_version": local_env["APP_VERSION"],
    "embedded_image_tag": local_env["IMAGE_TAG"],
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

docker logout ghcr.io >/dev/null
logged_in=false
trap - EXIT
