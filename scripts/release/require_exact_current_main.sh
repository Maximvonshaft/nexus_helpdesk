#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"

if [[ ! "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "exact-main guard: SOURCE_SHA must be a lowercase 40-character Git SHA" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "exact-main guard: repository checkout required" >&2
  exit 1
fi

if [[ "$(git rev-parse HEAD)" != "${SOURCE_SHA}" ]]; then
  echo "exact-main guard: checkout HEAD does not match SOURCE_SHA" >&2
  exit 1
fi

# Fetch immediately before an irreversible release operation. This closes the
# window between the workflow's initial guard and registry/evidence publication.
git fetch --no-tags origin main
if [[ "$(git rev-parse origin/main)" != "${SOURCE_SHA}" ]]; then
  echo "exact-main guard: SOURCE_SHA is no longer the current origin/main" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "exact-main guard: tracked repository content changed during qualification" >&2
  exit 1
fi

printf 'EXACT_CURRENT_MAIN_VALID=true source_sha=%s\n' "${SOURCE_SHA}"
