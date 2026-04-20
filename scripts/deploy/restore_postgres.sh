#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${1:?Usage: restore_postgres.sh <backup.sql.gz>}"

gunzip -c "$1" | psql "$DATABASE_URL"
echo "Restore completed from $1"
