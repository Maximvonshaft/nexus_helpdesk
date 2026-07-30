#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/nexus_helpdesk}"
NEXUS_DATABASE_TOPOLOGY="${NEXUS_DATABASE_TOPOLOGY:-external}"
NEXUS_CONTROLLED_ENV_FILE="${NEXUS_CONTROLLED_ENV_FILE:-deploy/.env.controlled}"
APP_URL="${APP_URL:-http://127.0.0.1:18095}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
section() { printf '\n===== %s =====\n' "$*"; }

status=0
warn_status=0
check_ok() { green "OK: $*"; }
check_warn() { yellow "WARN: $*"; warn_status=1; }
check_fail() { red "FAIL: $*"; status=1; }

compose() {
  NEXUS_DATABASE_TOPOLOGY="$NEXUS_DATABASE_TOPOLOGY" \
  NEXUS_CONTROLLED_ENV_FILE="$NEXUS_CONTROLLED_ENV_FILE" \
    deploy/nexus-prod-compose.sh "$@"
}

section "0. Enter app dir"
cd "$APP_DIR"
pwd

if [[ ! -x deploy/nexus-prod-compose.sh ]]; then
  check_fail "canonical compose wrapper is unavailable"
fi
if [[ ! -f "$NEXUS_CONTROLLED_ENV_FILE" || -L "$NEXUS_CONTROLLED_ENV_FILE" ]]; then
  check_fail "controlled environment file is unavailable or unsafe: $NEXUS_CONTROLLED_ENV_FILE"
fi

section "1. Git identity"
git status --short || check_warn "git status unavailable"
git branch --show-current || true
CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
printf 'git_sha=%s\n' "${CURRENT_SHA:-unknown}"
git log -1 --oneline || true

section "2. Controlled topology"
compose config --services || check_fail "controlled compose config failed"
compose ps || check_warn "controlled compose ps failed"

section "3. Listening ports"
ss -lntp | grep -E ':80|:18095|:8080|:5432' || check_warn "expected ports not visible"

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
if compose exec -T app-controlled python - <<'PY'
from app.settings import get_settings
s = get_settings()
print('app_env=', s.app_env)
print('is_postgres=', s.is_postgres)
print('storage_backend=', s.storage_backend)
print('upload_root=', s.upload_root)
print('enable_outbound_dispatch=', s.enable_outbound_dispatch)
print('outbound_provider=', s.outbound_provider)
print('webchat_rate_limit_backend=', s.webchat_rate_limit_backend)
print('webchat_ai_auto_reply_mode=', s.webchat_ai_auto_reply_mode)
print('metrics_enabled=', s.metrics_enabled)
PY
then
  check_ok "container settings readable"
else
  check_fail "container settings unreadable"
fi

section "6. Alembic revision"
compose exec -T app-controlled alembic current || check_fail "alembic current failed"

section "7. Upload persistence"
compose exec -T app-controlled python - <<'PY' || check_fail "upload root write probe failed"
from app.settings import get_settings
from pathlib import Path
s = get_settings()
p = Path(s.upload_root)
p.mkdir(parents=True, exist_ok=True)
probe = p / '.runtime-probe'
probe.write_text('ok', encoding='utf-8')
print('upload_root=', p)
print('probe=', probe.exists())
probe.unlink(missing_ok=True)
PY

section "8. Queue semantics"
if compose exec -T app-controlled python - <<'PY'
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
  if [[ "$rc" = "2" ]]; then
    check_warn "external pending outbound backlog exists; inspect dispatch gate and worker"
  else
    check_fail "queue semantics probe failed"
  fi
fi

section "9. Durable Worker progress"
for service in \
  worker-outbound-controlled \
  worker-background-controlled \
  worker-webchat-ai-controlled; do
  if compose exec -T "$service" python scripts/check_worker_progress.py; then
    check_ok "$service progress is fresh"
  else
    check_fail "$service progress is stale or unavailable"
  fi
done

section "10. Metrics authentication"
unauthenticated_code="$(
  curl --silent --show-error --max-time 5 \
    --output /tmp/nexus_metrics_unauthenticated.out \
    --write-out '%{http_code}' \
    "$APP_URL/metrics" || true
)"
if [[ "$unauthenticated_code" = "401" ]]; then
  check_ok "metrics endpoint rejects unauthenticated requests"
else
  check_fail "metrics endpoint accepted an unauthenticated request or returned an unexpected status: $unauthenticated_code"
fi

if compose exec -T app-controlled python - <<'PY'
import urllib.request
from app.settings import get_settings

settings = get_settings()
if not settings.metrics_enabled or not settings.metrics_token:
    raise SystemExit('metrics_not_configured')
request = urllib.request.Request(
    'http://127.0.0.1:8080/metrics',
    headers={'X-Metrics-Token': settings.metrics_token},
)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = response.read(256).decode('utf-8', errors='replace')
    if response.status != 200 or '# HELP' not in payload:
        raise SystemExit('metrics_response_invalid')
print('metrics_authenticated=true')
PY
then
  check_ok "metrics authenticated probe passed"
else
  check_fail "metrics authenticated probe failed"
fi

section "11. Recent logs"
compose logs --tail=120 app-controlled || check_warn "app logs unavailable"
compose logs --tail=120 worker-outbound-controlled || check_warn "outbound Worker logs unavailable"
compose logs --tail=120 worker-background-controlled || check_warn "background Worker logs unavailable"
compose logs --tail=120 worker-webchat-ai-controlled || check_warn "WebChat AI Worker logs unavailable"

section "12. Summary"
if [[ "$status" -ne 0 ]]; then
  check_fail "runtime probe failed"
  exit 1
fi
if [[ "$warn_status" -ne 0 ]]; then
  check_warn "runtime probe completed with warnings"
  exit 2
fi
check_ok "runtime probe passed"
