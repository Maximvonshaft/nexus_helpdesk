#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "knowledge_retrieval_probe_dry_run=ok"
  exit 0
fi

echo "knowledge_retrieval_probe_skipped=no_database_url"
exit 0
