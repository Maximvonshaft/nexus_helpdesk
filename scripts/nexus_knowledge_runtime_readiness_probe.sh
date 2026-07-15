#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-${ROOT_DIR}/backend}"

exec python "${ROOT_DIR}/backend/scripts/probe_knowledge_readiness.py" "$@"
