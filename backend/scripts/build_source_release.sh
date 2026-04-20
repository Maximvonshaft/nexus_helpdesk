#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/helpdesk_suite_lite_round27_source_release.zip}"
TMPDIR="$(mktemp -d)"
PKGROOT="$TMPDIR/helpdesk_suite_lite"
trap 'rm -rf "$TMPDIR"' EXIT
mkdir -p "$PKGROOT"

copy_tree() {
  local src="$1"
  local dst="$2"
  if [[ -d "$src" ]]; then
    mkdir -p "$dst"
    rsync -a \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude '.pytest_cache' \
      --exclude '.venv' \
      --exclude 'venv' \
      --exclude 'node_modules' \
      --exclude 'dist' \
      --exclude 'coverage' \
      --exclude '*.db' \
      --exclude '*.sqlite' \
      --exclude 'uploads' \
      --exclude 'tsconfig.tsbuildinfo' \
      "$src/" "$dst/"
  fi
}

cp "$ROOT/README.md" "$PKGROOT/README.md"
cp "$ROOT/Dockerfile" "$PKGROOT/Dockerfile"
copy_tree "$ROOT/backend" "$PKGROOT/backend"
copy_tree "$ROOT/frontend" "$PKGROOT/frontend"
copy_tree "$ROOT/webapp" "$PKGROOT/webapp"
copy_tree "$ROOT/deploy" "$PKGROOT/deploy"
copy_tree "$ROOT/scripts" "$PKGROOT/scripts"
if [[ -f "$ROOT/.dockerignore" ]]; then
  cp "$ROOT/.dockerignore" "$PKGROOT/.dockerignore"
fi
for optional in LOCAL_OPENCLAW_READY_REPORT.md helpdesk_local_openclaw_ready_summary.md NEXT_PHASE_MAX_PUSH_REPORT.md ROUND20B_LEGACY_PRODUCTION_REPORT.md ROUND20A_RECTIFICATION_REPORT.md ROUND27_FRONTEND_OPERATOR_HARDENING_REPORT.md ROUND26_FRONTEND_HARDENING_REPORT.md ROUND25_HARDENING_REPORT.md ROUND24_HARDENING_REPORT.md ROUND23_HARDENING_REPORT.md; do
  if [[ -f "$ROOT/$optional" ]]; then
    cp "$ROOT/$optional" "$PKGROOT/$optional"
  fi
done

rm -f "$PKGROOT/backend/helpdesk.db"
find "$PKGROOT" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name 'node_modules' \) -prune -exec rm -rf {} +
find "$PKGROOT" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.db' -o -name '*.sqlite' -o -name 'tsconfig.tsbuildinfo' \) -delete

mkdir -p "$(dirname "$OUT")"
cd "$TMPDIR"
zip -qr "$OUT" helpdesk_suite_lite
printf 'Built %s\n' "$OUT"
