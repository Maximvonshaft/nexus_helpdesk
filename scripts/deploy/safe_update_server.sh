#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/deploy_backups/$STAMP}"
BACKUP_PARENT="$(dirname "$BACKUP_DIR")"

if [[ -e "$BACKUP_DIR" || -L "$BACKUP_DIR" ]]; then
  echo "refusing existing backup target: $BACKUP_DIR" >&2
  exit 2
fi
mkdir -p -- "$BACKUP_PARENT"

STAGING_DIR="$(mktemp -d "${BACKUP_DIR}.tmp.XXXXXX")"
chmod 700 "$STAGING_DIR"
cleanup() {
  rm -rf -- "$STAGING_DIR"
}
trap cleanup EXIT

# Preserve the single controlled deployment authority. This script only creates
# a verified backup; it never checks out, overwrites, deploys, restarts or
# changes containers.
files=(
  Dockerfile
  deploy/.env.controlled
  deploy/.env.controlled.local-postgres
  deploy/docker-compose.controlled.yml
  deploy/docker-compose.controlled-postgres.yml
  deploy/nexus-prod-compose.sh
  deploy/nginx/default.conf
  backend/.env
)

for relative_path in "${files[@]}"; do
  if [[ -L "$relative_path" ]]; then
    echo "refusing symlinked protected file: $relative_path" >&2
    exit 3
  fi
  if [[ -e "$relative_path" && ! -f "$relative_path" ]]; then
    echo "refusing non-regular protected file: $relative_path" >&2
    exit 3
  fi
done

manifest="$STAGING_DIR/SHA256SUMS"
: > "$manifest"
chmod 600 "$manifest"
copied=0

for relative_path in "${files[@]}"; do
  if [[ ! -f "$relative_path" ]]; then
    echo "Missing optional protected file: $relative_path"
    continue
  fi

  destination="$STAGING_DIR/$relative_path"
  mkdir -p -- "$(dirname "$destination")"
  chmod 700 "$(dirname "$destination")"
  install -m 600 -- "$relative_path" "$destination"
  (
    cd "$STAGING_DIR"
    sha256sum -- "$relative_path"
  ) >> "$manifest"
  copied=$((copied + 1))
done

if [[ "$copied" -eq 0 ]]; then
  echo "no protected files were available to back up" >&2
  exit 4
fi

find "$STAGING_DIR" -type d -exec chmod 700 {} +
find "$STAGING_DIR" -type f -exec chmod 600 {} +
(
  cd "$STAGING_DIR"
  sha256sum --check --strict SHA256SUMS >/dev/null
)

mv -T -n -- "$STAGING_DIR" "$BACKUP_DIR"
if [[ -e "$STAGING_DIR" || -L "$STAGING_DIR" ]]; then
  echo "backup target appeared during publication; verified bundle was not published" >&2
  exit 5
fi
trap - EXIT
chmod 700 "$BACKUP_DIR"

printf 'Configuration backup verified\n'
printf 'backup_dir=%s\n' "$BACKUP_DIR"
printf 'protected_files=%s\n' "$copied"
printf 'manifest=%s\n' "$BACKUP_DIR/SHA256SUMS"
printf '\nCurrent git status:\n'
if ! git status --short; then
  echo "warning: git status could not be read" >&2
fi
printf '\nNext steps:\n'
printf '%s\n' \
  '1. Keep the controlled configuration backup unchanged.' \
  '2. Prepare deploy/.env.controlled or deploy/.env.controlled.local-postgres.' \
  '3. Run the canonical static verifier and controlled preflight.' \
  '4. Back up PostgreSQL and uploads separately.' \
  '5. Only after explicit authorization, use deploy/nexus-prod-compose.sh with an explicit database topology.'
