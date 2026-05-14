#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../.." ] && [ -d "$SCRIPT_DIR/../../.git" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d "$SCRIPT_DIR/.." ] && [ -d "$SCRIPT_DIR/../.git" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -d ".git" ]; then
  REPO_ROOT="$(pwd)"
else
  echo "Cannot locate repository root. Run from repo root or install this script under scripts/release/." >&2
  exit 2
fi
cd "$REPO_ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT:-$REPO_ROOT/release_evidence_${STAMP}}"
mkdir -p "$OUT/command_outputs" "$OUT/summaries"
SUMMARY="$OUT/summaries/release_gate_summary.md"
FAILED_STEP=""

on_error() {
  local line="$1"
  {
    echo "# Release Gate Summary"
    echo
    echo "Verdict: FAILED"
    echo "Failed step: ${FAILED_STEP:-unknown}"
    echo "Line: $line"
    echo "UTC: $(date -u)"
  } > "$SUMMARY"
  echo "Release gate failed at ${FAILED_STEP:-unknown}. Evidence: $OUT" >&2
}
trap 'on_error $LINENO' ERR

run_step() {
  local name="$1"; shift
  FAILED_STEP="$name"
  local log="$OUT/command_outputs/${name}.log"
  {
    echo "===== $name ====="
    echo "UTC: $(date -u)"
    echo "PWD: $(pwd)"
    printf 'CMD:'
    printf ' %q' "$@"
    echo
    "$@"
  } 2>&1 | tee "$log"
}

run_in_dir() {
  local name="$1"; shift
  local dir="$1"; shift
  FAILED_STEP="$name"
  local log="$OUT/command_outputs/${name}.log"
  {
    echo "===== $name ====="
    echo "UTC: $(date -u)"
    echo "PWD: $REPO_ROOT/$dir"
    printf 'CMD:'
    printf ' %q' "$@"
    echo
    (cd "$REPO_ROOT/$dir" && "$@")
  } 2>&1 | tee "$log"
}

{
  echo "UTC: $(date -u)"
  git rev-parse HEAD || true
  git status --short || true
  python --version || true
  node --version || true
  npm --version || true
  docker --version || true
  docker compose version || true
} 2>&1 | tee "$OUT/command_outputs/00_environment.txt"

run_step 01_compileall python -m compileall backend/app backend/scripts scripts
run_in_dir 02_backend_pytest backend pytest -q
run_in_dir 03_webapp_npm_ci webapp npm ci
run_in_dir 04_webapp_typecheck webapp npm run typecheck
run_in_dir 05_webapp_lint webapp npm run lint
run_in_dir 06_webapp_test webapp npm test
run_in_dir 07_webapp_build webapp npm run build

if [ ! -f scripts/smoke/browser_bundle_secret_scan.py ]; then
  echo "MISSING scripts/smoke/browser_bundle_secret_scan.py" | tee "$OUT/command_outputs/08_browser_bundle_secret_scan.log"
  exit 1
fi
run_step 08_browser_bundle_secret_scan python scripts/smoke/browser_bundle_secret_scan.py --static backend/app/static/webchat --static frontend_dist

{
  echo "# Release Gate Summary"
  echo
  echo "Verdict: PASS_LOCAL_GATE"
  echo "UTC: $(date -u)"
  echo "Commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "Evidence: $OUT"
} > "$SUMMARY"

echo "Release evidence written to: $OUT"
