#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/nexus_helpdesk}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.server.yml}"
APP_URL="${APP_URL:-http://127.0.0.1:18081}"
METRICS_TOKEN_VALUE="${METRICS_TOKEN:-}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
section() { printf '\n===== %s =====\n' "$*"; }

status=0
warn_status=0
check_ok() { green "OK: $*"; }
check_warn() { yellow "WARN: $*"; warn_status=1; }
check_fail() { red "FAIL: $*"; status=1; }

section "0. Enter app dir"
cd "$APP_DIR"
pwd

section "1. Git identity"
git status --short || check_warn "git status unavailable"
git branch --show-current || true
CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
printf 'git_sha=%s\n' "${CURRENT_SHA:-unknown}"
git log -1 --oneline || true

section "2. Compose topology"
docker compose -f "$COMPOSE_FILE" config --services || check_fail "compose config failed"
docker compose -f "$COMPOSE_FILE" ps || check_warn "compose ps failed"

section "3. Listening ports"
ss -lntp | grep -E ':80|:18081|:8080|:5432' || check_warn "expected ports not visible"

section "4. HTTP health"
if curl -fsS "$APP_URL/healthz" | tee /tmp/nexus_healthz.json; then
  check_ok "healthz reachable at $APP_URL"
else
  check_fail "healthz failed at $APP_URL"
fi
if curl -fsS "$APP_URL/readyz" | tee /tmp/nexus_readyz.json; then
  check_ok "readyz reachable at $APP_URL"
else
  check_fail "readyz failed at $APP_URL"
fi

section "5. Runtime settings snapshot"
if docker compose -f "$COMPOSE_FILE" exec -T app python - <<'PY'
from app.settings import get_settings
s=get_settings()
print('app_env=', s.app_env)
print('is_postgres=', s.is_postgres)
print('storage_backend=', s.storage_backend)
print('upload_root=', s.upload_root)
print('openclaw_bridge_enabled=', s.openclaw_bridge_enabled)
print('openclaw_bridge_url=', s.openclaw_bridge_url)
print('openclaw_transport=', s.openclaw_transport)
print('openclaw_deployment_mode=', s.openclaw_deployment_mode)
print('enable_outbound_dispatch=', s.enable_outbound_dispatch)
print('outbound_provider=', s.outbound_provider)
print('webchat_rate_limit_backend=', s.webchat_rate_limit_backend)
print('webchat_ai_auto_reply_mode=', s.webchat_ai_auto_reply_mode)
PY
then
  check_ok "container settings readable"
else
  check_fail "container settings unreadable"
fi

section "6. Alembic revision"
docker compose -f "$COMPOSE_FILE" exec -T app alembic current || check_fail "alembic current failed"

section "7. Upload persistence"
docker compose -f "$COMPOSE_FILE" exec -T app python - <<'PY' || check_fail "upload root write probe failed"
from app.settings import get_settings
from pathlib import Path
s=get_settings()
p=Path(s.upload_root)
p.mkdir(parents=True, exist_ok=True)
probe=p/'.runtime-probe'
probe.write_text('ok', encoding='utf-8')
print('upload_root=', p)
print('probe=', probe.exists())
probe.unlink(missing_ok=True)
PY

section "8. Queue semantics"
if docker compose -f "$COMPOSE_FILE" exec -T app python - <<'PY'
from app.db import db_context
from app.services.outbound_semantics import count_outbound_semantics
with db_context() as db:
    counts = count_outbound_semantics(db)
for key in sorted(counts):
    print(f'{key}={counts[key]}')
if counts.get('external_pending_outbound', 0) > 0:
    raise SystemExit(2)
PY
then
  check_ok "no external pending outbound backlog"
else
  rc=$?
  if [ "$rc" = "2" ]; then
    check_warn "external pending outbound backlog exists; inspect dispatch gate and worker"
  else
    check_fail "queue semantics probe failed"
  fi
fi

section "9. Metrics endpoint"
if curl -fsS "$APP_URL/metrics" >/tmp/nexus_metrics.out 2>/tmp/nexus_metrics.err; then
  check_ok "metrics reachable without token"
else
  if grep -Eq '404|metrics disabled' /tmp/nexus_metrics.err /tmp/nexus_metrics.out 2>/dev/null; then
    check_warn "metrics disabled; acceptable for controlled pilot if documented"
  elif [ -n "$METRICS_TOKEN_VALUE" ] && curl -fsS -H "X-Metrics-Token: $METRICS_TOKEN_VALUE" "$APP_URL/metrics" >/tmp/nexus_metrics_token.out; then
    check_ok "metrics reachable with token"
  else
    check_warn "metrics unavailable or token not provided"
  fi
fi

section "10. Recent logs"
docker compose -f "$COMPOSE_FILE" logs --tail=120 app || check_warn "app logs unavailable"
docker compose -f "$COMPOSE_FILE" logs --tail=120 worker || check_warn "worker logs unavailable"
docker compose -f "$COMPOSE_FILE" logs --tail=120 sync-daemon || check_warn "sync-daemon logs unavailable"
docker compose -f "$COMPOSE_FILE" logs --tail=120 event-daemon || check_warn "event-daemon logs unavailable"

section "11. Summary"
if [ "$status" -ne 0 ]; then
  check_fail "runtime probe failed"
  exit 1
fi
if [ "$warn_status" -ne 0 ]; then
  check_warn "runtime probe completed with warnings"
  exit 2
fi
check_ok "runtime probe passed"
