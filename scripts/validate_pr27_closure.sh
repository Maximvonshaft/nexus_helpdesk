#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Historical compatibility entrypoint only. The repository-local canonical
# verifier owns frontend, backend, architecture, supply-chain and identity gates.
# Do not recreate a PR-specific test/build/deploy chain here.
exec python3 scripts/verify_repository.py "$@"
