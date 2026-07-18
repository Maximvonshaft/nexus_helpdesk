#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Deployment structure is part of the single repository authority. Do not grow
# a second deploy-only verifier here; the canonical verifier owns aliases,
# service isolation, database roles, supply-chain inputs and retired paths.
exec python3 scripts/verify_repository.py --static-only
