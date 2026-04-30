#!/usr/bin/env bash
set -Eeuo pipefail

PASS=0
WARN=0
FAIL=0
ROOT_DIR="${1:-$(pwd)}"

say_pass(){ echo "[PASS] $*"; PASS=$((PASS+1)); }
say_warn(){ echo "[WARN] $*"; WARN=$((WARN+1)); }
say_fail(){ echo "[FAIL] $*"; FAIL=$((FAIL+1)); }

cd "$ROOT_DIR" 2>/dev/null || { echo "[FAIL] Cannot enter repo dir: $ROOT_DIR"; exit 2; }

echo "===== NexusDesk server drift audit ====="
echo "Repo dir: $(pwd)"
echo

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say_pass "Git repository detected"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
  echo "Branch: ${BRANCH:-unknown}"
  echo "Commit: ${COMMIT:-unknown}"
  [[ "$BRANCH" == "main" ]] && say_pass "Current branch is main" || say_warn "Current branch is not main: ${BRANCH:-unknown}"
  if [[ -n "$(git status --short 2>/dev/null || true)" ]]; then
    say_warn "Working tree is dirty; review server-only files before deploy"
    git status --short || true
  else
    say_pass "Working tree is clean"
  fi
else
  say_fail "Current directory is not a Git repository"
fi

if [[ -f deploy/.env.prod ]]; then
  say_pass "deploy/.env.prod exists"
  if git ls-files --error-unmatch deploy/.env.prod >/dev/null 2>&1; then
    say_fail "deploy/.env.prod is tracked by Git; secrets must not be committed"
  else
    say_pass "deploy/.env.prod is not tracked by Git"
  fi
else
  say_fail "deploy/.env.prod missing"
fi

[[ -d data ]] && say_pass "data/ directory exists" || say_warn "data/ directory not found"
if [[ -d backend/uploads || -d data/uploads ]]; then
  say_pass "uploads storage directory exists"
else
  say_warn "uploads storage directory not found"
fi

if [[ -f deploy/docker-compose.server.example.yml ]]; then
  say_pass "deploy/docker-compose.server.example.yml exists"
  grep -q 'worker:' deploy/docker-compose.server.example.yml && say_pass "compose contains worker service" || say_fail "compose missing worker service"
  grep -q 'sync-daemon:' deploy/docker-compose.server.example.yml && say_pass "compose contains sync-daemon service" || say_fail "compose missing sync-daemon service"
  grep -q 'event-daemon:' deploy/docker-compose.server.example.yml && say_pass "compose contains event-daemon service" || say_fail "compose missing event-daemon service"
else
  say_fail "deploy/docker-compose.server.example.yml missing"
fi

if command -v docker >/dev/null 2>&1; then
  say_pass "docker command available"
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | sed -n '1,20p' || say_warn "docker ps failed"
else
  say_warn "docker command not available"
fi

if command -v alembic >/dev/null 2>&1 && [[ -d backend ]]; then
  (cd backend && alembic heads 2>/dev/null) && say_pass "Alembic heads command succeeded" || say_warn "Alembic heads command failed"
else
  say_warn "Alembic command not available in current shell"
fi

if [[ -d backups || -d /root/nexusdesk_deploy_backup || -d /root/helpdesk_deploy_backup ]]; then
  say_pass "Backup directory candidate exists"
else
  say_warn "No known backup directory found"
fi

SERVER_ONLY_COUNT="$(find . -maxdepth 3 \( -name '*.local' -o -name '*.override.yml' -o -name '.env.prod' \) 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$SERVER_ONLY_COUNT" -gt 0 ]]; then
  say_warn "Server-only override/env files detected: $SERVER_ONLY_COUNT"
  find . -maxdepth 3 \( -name '*.local' -o -name '*.override.yml' -o -name '.env.prod' \) 2>/dev/null | sort
else
  say_pass "No server-only override/env files detected within maxdepth 3"
fi

overall="ready"
if [[ "$FAIL" -gt 0 ]]; then overall="not_ready"; elif [[ "$WARN" -gt 0 ]]; then overall="review_required"; fi

echo
echo "===== Summary ====="
printf '{"pass":%s,"warn":%s,"fail":%s,"overall":"%s"}\n' "$PASS" "$WARN" "$FAIL" "$overall"

[[ "$FAIL" -eq 0 ]]
