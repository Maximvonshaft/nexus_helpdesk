#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE_DIR="$TMP_DIR/repo"
mkdir -p "$ARCHIVE_DIR"

echo ">> Exporting tracked source from HEAD"
git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$ARCHIVE_DIR"

echo ">> Installing webapp dependencies from lockfile"
pushd "$ARCHIVE_DIR/webapp" >/dev/null
npm ci

echo ">> Running TypeScript + Vite production build in clean room"
npm run build
popd >/dev/null

echo "✅ Clean-room webapp build passed"
