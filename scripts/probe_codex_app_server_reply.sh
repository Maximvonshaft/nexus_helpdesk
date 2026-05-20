#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${CODEX_REPLY_PROBE_ARTIFACT_DIR:-${ROOT_DIR}/artifacts/codex_reply_probe}"

mkdir -p "${ARTIFACT_DIR}"

export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}"
export CODEX_REPLY_PROBE_ARTIFACT_DIR="${ARTIFACT_DIR}"

python3 "${ROOT_DIR}/tools/codex-reply-bridge/probe_codex_app_server_reply.py" "$@"
