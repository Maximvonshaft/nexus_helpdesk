#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELEASE_METADATA_ENV="${1:-${RELEASE_METADATA_ENV:-}}"
PROD_ENV="${PROD_ENV:-$ROOT_DIR/deploy/.env.prod}"
OUTPUT_ENV="${OUTPUT_ENV:-$ROOT_DIR/deploy/.env.prod.next}"
APP_HOST_PORT_OVERRIDE="${APP_HOST_PORT_OVERRIDE:-}"

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy/prepare_production_release_env.sh /path/to/release-metadata.env

Environment:
  PROD_ENV=/opt/nexus_helpdesk/deploy/.env.prod
  OUTPUT_ENV=/opt/nexus_helpdesk/deploy/.env.prod.next
  APP_HOST_PORT_OVERRIDE=18086

This script copies the current production env file and upserts non-secret
release identity fields from the GitHub Actions release-metadata artifact. It
does not run docker compose, reload nginx, or change public traffic.
EOF
}

if [ -z "$RELEASE_METADATA_ENV" ] || [ "${RELEASE_METADATA_ENV:-}" = "-h" ] || [ "${RELEASE_METADATA_ENV:-}" = "--help" ]; then
  usage
  exit 2
fi
if [ ! -r "$RELEASE_METADATA_ENV" ]; then
  echo "release metadata file is not readable: $RELEASE_METADATA_ENV" >&2
  exit 2
fi
if [ ! -r "$PROD_ENV" ]; then
  echo "production env file is not readable: $PROD_ENV" >&2
  exit 2
fi
if [ "$OUTPUT_ENV" = "$PROD_ENV" ] && [ "${ALLOW_IN_PLACE:-false}" != "true" ]; then
  echo "refusing in-place update; set OUTPUT_ENV to a separate file or ALLOW_IN_PLACE=true" >&2
  exit 2
fi

metadata_value() {
  local key="$1"
  grep -E "^${key}=" "$RELEASE_METADATA_ENV" | tail -n 1 | sed "s/^${key}=//"
}

required_keys=(GIT_SHA BUILD_TIME IMAGE_TAG APP_VERSION FRONTEND_BUILD_SHA)
for key in "${required_keys[@]}"; do
  value="$(metadata_value "$key")"
  if [ -z "$value" ]; then
    echo "release metadata missing $key" >&2
    exit 3
  fi
done

if grep -E '(^|_)(SECRET|PASSWORD|TOKEN|API_KEY)=' "$RELEASE_METADATA_ENV" >/dev/null; then
  echo "release metadata must not contain secret-like keys" >&2
  exit 4
fi

install -m 0600 "$PROD_ENV" "$OUTPUT_ENV"

upsert_env() {
  local key="$1"
  local value="$2"
  local file="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print key "=" value
      }
    }
  ' "$file" > "$tmp"
  install -m 0600 "$tmp" "$file"
  rm -f "$tmp"
}

for key in "${required_keys[@]}"; do
  upsert_env "$key" "$(metadata_value "$key")" "$OUTPUT_ENV"
done

if [ -n "$APP_HOST_PORT_OVERRIDE" ]; then
  upsert_env APP_HOST_PORT "$APP_HOST_PORT_OVERRIDE" "$OUTPUT_ENV"
fi

echo "PRODUCTION_RELEASE_ENV_PREPARED=true"
echo "output_env=$OUTPUT_ENV"
echo "git_sha=$(metadata_value GIT_SHA)"
echo "image_tag=$(metadata_value IMAGE_TAG)"
if [ -n "$APP_HOST_PORT_OVERRIDE" ]; then
  echo "app_host_port=$APP_HOST_PORT_OVERRIDE"
fi
