# Next Chat Context for NexusDesk / OpenClaw

Before making changes, read these files:

- docs/ops/NEXUSDESK_FASTLANE_OPENCLAW_RUNBOOK.md
- deploy/FASTLANE_OPENCLAW_RUNBOOK.md
- deploy/nexus-prod-compose.sh
- deploy/docker-compose.openclaw-token.override.yml

Current known good state:

NexusDesk Webchat Fast Lane is working.
OpenClaw Responses Gateway is reachable through Tailscale.
Fast Reply returns ok=true, ai_generated=true, reply_source=openclaw_responses, error_code=null.

Critical rule:

Do not start NexusDesk app with docker-compose.server.yml alone.
Always use:

./deploy/nexus-prod-compose.sh up -d app

Root cause that was fixed:

OPENCLAW_RESPONSES_TOKEN_FILE pointed to /run/openclaw_responses_token, but the file was missing inside the app container.

Fix that is now required for production:

deploy/docker-compose.openclaw-token.override.yml mounts:

./deploy/runtime_secrets/openclaw_responses_token:/run/openclaw_responses_token:ro

Final evidence directory:

/opt/nexus_helpdesk/probe_reports/v222_fastlane_final_lock_20260513173136

Good final response shape:

ok=true
ai_generated=true
reply_source=openclaw_responses
error_code=null
