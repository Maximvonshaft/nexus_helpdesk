#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/nexus_canonical_source_release.zip}"
TMPDIR="$(mktemp -d)"
PKGROOT="$TMPDIR/nexus"
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
      --exclude 'artifacts' \
      --exclude 'tsconfig.tsbuildinfo' \
      "$src/" "$dst/"
  fi
}

cp "$ROOT/README.md" "$PKGROOT/README.md"
cp "$ROOT/Dockerfile" "$PKGROOT/Dockerfile"
copy_tree "$ROOT/backend" "$PKGROOT/backend"
copy_tree "$ROOT/webapp" "$PKGROOT/webapp"
copy_tree "$ROOT/deploy" "$PKGROOT/deploy"
copy_tree "$ROOT/scripts" "$PKGROOT/scripts"
copy_tree "$ROOT/config" "$PKGROOT/config"
copy_tree "$ROOT/docs" "$PKGROOT/docs"

for optional in .dockerignore .gitmodules; do
  if [[ -f "$ROOT/$optional" ]]; then
    cp "$ROOT/$optional" "$PKGROOT/$optional"
  fi
done

rm -f "$PKGROOT/backend/helpdesk.db"
find "$PKGROOT" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name 'node_modules' \) -prune -exec rm -rf {} +
find "$PKGROOT" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.db' -o -name '*.sqlite' -o -name 'tsconfig.tsbuildinfo' \) -delete

mkdir -p "$(dirname "$OUT")"
cd "$TMPDIR"
zip -qr "$OUT" nexus
printf 'Built %s\n' "$OUT"
