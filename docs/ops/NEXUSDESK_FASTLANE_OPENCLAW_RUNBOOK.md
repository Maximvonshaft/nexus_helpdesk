# NexusDesk Webchat Fast Lane / OpenClaw Production Runbook

## Current known good state

NexusDesk Webchat Fast Lane is working.

Final verified result:

- OpenClaw health endpoint returns 200 OK.
- OpenClaw responses endpoint returns 200 OK.
- NexusDesk /api/webchat/fast-reply returns ok=true.
- ai_generated=true.
- reply_source=openclaw_responses.
- error_code=null.
- FINAL_ASSERT=PASSED.

Final evidence directory:

/opt/nexus_helpdesk/probe_reports/v222_fastlane_final_lock_20260513173136

## Root cause fixed

OpenClaw Responses Gateway became reachable through Tailscale, but NexusDesk still returned ai_unavailable.

The actual remaining root cause was:

OPENCLAW_RESPONSES_TOKEN_FILE=/run/openclaw_responses_token

The env variable existed, but the token file was not mounted into the deploy-app-1 container.

After mounting the token file read-only, Fast Lane recovered.

## Required production files

- deploy/docker-compose.server.yml
- deploy/docker-compose.openclaw-token.override.yml
- deploy/runtime_secrets/openclaw_responses_token
- deploy/nexus-prod-compose.sh
- deploy/FASTLANE_OPENCLAW_RUNBOOK.md
- docs/ops/NEXUSDESK_FASTLANE_OPENCLAW_RUNBOOK.md
- docs/ops/NEXT_CHAT_CONTEXT.md

## Critical production rule

Do not start NexusDesk app with docker-compose.server.yml alone.

Wrong command:

docker compose -f deploy/docker-compose.server.yml up -d app

Correct command:

cd /opt/nexus_helpdesk
./deploy/nexus-prod-compose.sh up -d app

Reason:

The app container needs this read-only token mount:

./deploy/runtime_secrets/openclaw_responses_token:/run/openclaw_responses_token:ro

Without this mount, /api/webchat/fast-reply can return:

error_code=ai_unavailable

## Current OpenClaw endpoint

OpenClaw Responses URL:

http://100.106.75.61:18792/responses

OpenClaw is exposed through Tailscale.

Recommended exposure model:

- OpenClaw service listens on 127.0.0.1.
- Tailscale TCP serve exposes 100.106.75.61:18792 to the tailnet.
- Do not bind OpenClaw directly to 0.0.0.0 unless there is a separate security review.

## Standard smoke tests

Run from NexusDesk cloud host.

1. NexusDesk health:

curl -i --max-time 10 http://127.0.0.1:18081/readyz

Expected:

HTTP 200, status=ready, database=ok.

2. OpenClaw health:

curl -i --max-time 10 http://100.106.75.61:18792/healthz

Expected:

HTTP 200, service=openclaw-bridge.

3. OpenClaw responses:

curl -i --max-time 95 \
  -H 'Content-Type: application/json' \
  -d '{"sessionKey":"cloud-smoke","input":"Reply with exactly: pong"}' \
  http://100.106.75.61:18792/responses

Expected:

HTTP 200, output_text=pong.

4. NexusDesk Fast Reply:

curl -sS -m 95 -X POST http://127.0.0.1:18081/api/webchat/fast-reply \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://1.94.148.194' \
  -d '{"session_id":"fastlane-smoke","client_message_id":"fastlane-smoke-001","body":"Hello. Reply briefly with a safe customer service greeting."}'

Expected:

ok=true
ai_generated=true
reply_source=openclaw_responses
error_code=null

## Token mount verification

docker exec deploy-app-1 sh -lc '
set -Eeuo pipefail
f="${OPENCLAW_RESPONSES_TOKEN_FILE:-}"
echo "OPENCLAW_RESPONSES_TOKEN_FILE=$f"
echo "TOKEN_FILE_SET=$([ -n "$f" ] && echo yes || echo no)"
echo "TOKEN_FILE_EXISTS=$([ -f "$f" ] && echo yes || echo no)"
echo "TOKEN_FILE_READABLE=$([ -r "$f" ] && echo yes || echo no)"
if [ -f "$f" ]; then
  wc -c "$f" | awk "{print \"TOKEN_FILE_BYTES=\" \$1}"
fi
'

Expected:

TOKEN_FILE_EXISTS=yes
TOKEN_FILE_READABLE=yes
TOKEN_FILE_BYTES=65

## If Fast Lane breaks again

Check in this order:

1. ./deploy/nexus-prod-compose.sh ps
2. curl http://127.0.0.1:18081/readyz
3. curl http://100.106.75.61:18792/healthz
4. curl http://100.106.75.61:18792/responses
5. Verify token mount inside deploy-app-1.
6. Check app logs for webchat_openclaw_responses_metric.
7. Check app logs for ai_unavailable.

Most likely regression:

The app was restarted without deploy/docker-compose.openclaw-token.override.yml.

Fix:

cd /opt/nexus_helpdesk
./deploy/nexus-prod-compose.sh up -d --force-recreate --no-deps app

## Non-urgent maintenance notes

These are not current Fast Lane blockers:

- System restart required.
- 7 zombie processes.
- Possible orphan container deploy-worker-1.

Do not reboot or clean these during business traffic unless there is a maintenance window.
