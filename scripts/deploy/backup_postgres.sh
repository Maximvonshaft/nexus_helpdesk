#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/helpdesk_${STAMP}.sql.gz"

pg_dump "$DATABASE_URL" | gzip > "$OUT_FILE"
echo "Backup written to $OUT_FILE"
