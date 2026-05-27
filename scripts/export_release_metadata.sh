#!/usr/bin/env sh
set -eu

# Generate non-secret release metadata for docker compose builds.
# Usage:
#   scripts/export_release_metadata.sh deploy/.release.env
#   set -a; . deploy/.release.env; set +a
#   docker compose --env-file deploy/.env.prod --env-file deploy/.release.env -f deploy/docker-compose.server.yml build

OUT_FILE="${1:-}"

if ! command -v git >/dev/null 2>&1; then
  echo "git command is required" >&2
  exit 1
fi

GIT_SHA_VALUE="${GIT_SHA:-$(git rev-parse HEAD)}"
SHORT_SHA_VALUE="$(printf '%s' "$GIT_SHA_VALUE" | cut -c1-12)"
BUILD_TIME_VALUE="${BUILD_TIME:-$(date -u +%Y%m%dT%H%M%SZ)}"
APP_VERSION_VALUE="${APP_VERSION:-main-${SHORT_SHA_VALUE}}"
IMAGE_TAG_VALUE="${IMAGE_TAG:-nexusdesk/helpdesk:main-${SHORT_SHA_VALUE}-${BUILD_TIME_VALUE}}"
FRONTEND_BUILD_SHA_VALUE="${FRONTEND_BUILD_SHA:-$GIT_SHA_VALUE}"

write_metadata() {
  printf 'GIT_SHA=%s\n' "$GIT_SHA_VALUE"
  printf 'COMMIT_SHA=%s\n' "$GIT_SHA_VALUE"
  printf 'APP_GIT_SHA=%s\n' "$GIT_SHA_VALUE"
  printf 'FRONTEND_BUILD_SHA=%s\n' "$FRONTEND_BUILD_SHA_VALUE"
  printf 'BUILD_TIME=%s\n' "$BUILD_TIME_VALUE"
  printf 'APP_BUILD_TIME=%s\n' "$BUILD_TIME_VALUE"
  printf 'APP_VERSION=%s\n' "$APP_VERSION_VALUE"
  printf 'IMAGE_TAG=%s\n' "$IMAGE_TAG_VALUE"
}

if [ -n "$OUT_FILE" ]; then
  case "$OUT_FILE" in
    *secret*|*Secret*|*password*|*Password*|*token*|*Token*)
      echo "Refusing to write release metadata to a sensitive-looking path: $OUT_FILE" >&2
      exit 1
      ;;
  esac
  mkdir -p "$(dirname "$OUT_FILE")"
  umask 077
  write_metadata > "$OUT_FILE"
  echo "Wrote release metadata to $OUT_FILE" >&2
else
  write_metadata
fi
