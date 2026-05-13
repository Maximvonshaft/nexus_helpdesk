# Fast Lane OpenClaw Production Note

Production app operations must use:

cd /opt/nexus_helpdesk
./deploy/nexus-prod-compose.sh up -d app

Do not use docker-compose.server.yml alone.

Reason:

The app requires this read-only token mount:

./deploy/runtime_secrets/openclaw_responses_token:/run/openclaw_responses_token:ro

Without it, /api/webchat/fast-reply can return:

ai_unavailable

Main runbook:

docs/ops/NEXUSDESK_FASTLANE_OPENCLAW_RUNBOOK.md

Next chat context:

docs/ops/NEXT_CHAT_CONTEXT.md
