# WebCall AI Infrastructure Skeleton Runbook

## Scope

This runbook covers the repository-side infrastructure skeleton for the future WebCall AI path at `/webcall-ai`.
It is not a production-ready release. It keeps `/webcall-ai-demo` as an internal sandbox, does not store raw audio by default, and does not enable real customer traffic by default.

## Runtime Flags

Required staging validation flags, applied only after CI is green and runtime secrets are present:

```text
WEBCALL_AI_PRODUCTION_ENABLED=true
WEBCALL_AI_AGENT_ENABLED=true
WEBCALL_AI_PROVIDER_PROFILE=fake
WEBCALL_AI_RECORD_RAW_AUDIO=false
WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ENABLED=true
LIVEKIT_URL=wss://voice.leakle.com
LIVEKIT_API_KEY=<runtime secret>
LIVEKIT_API_SECRET=<runtime secret>
STT_PROVIDER=fake
LLM_PROVIDER=fake
TTS_PROVIDER=fake
```

Keep these disabled for the initial rollout:

```text
WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER=false
WEBCALL_AI_ALLOW_CANCEL=false
WEBCALL_AI_ALLOW_ADDRESS_UPDATE=false
```

## Deploy Boundary

1. Keep `WEBCALL_AI_PRODUCTION_ENABLED=false` for merge.
2. Apply secrets only in the server runtime environment when staging validation starts.
2. Run migrations as usual. This change uses existing voice/session/event tables and adds no migration.
3. Start the API.
4. Start the agent worker profile only in staging validation:

```bash
docker compose -f deploy/docker-compose.server.yml --profile webcall-ai up -d webcall-ai-agent
```

## Smoke

1. `GET /api/webcall-ai/runtime-config` returns `enabled=true`, `status=ready`, and does not expose LiveKit secrets.
2. Open `/webcall-ai`.
3. Start a call and grant microphone permission.
4. Confirm the browser joins the LiveKit room.
5. Confirm the agent worker joins as AI participant.
6. Speak a tracking question and verify a redacted event appears at `/api/webcall-ai/sessions/{id}/events`.
7. Request human handoff and verify `webcall_ai.handoff.requested` is persisted.
8. End the call and verify final voice evidence is present in the ticket timeline.

## Rollback

Set:

```text
WEBCALL_AI_PRODUCTION_ENABLED=false
WEBCALL_AI_AGENT_ENABLED=false
```

Then restart the app and stop the `webcall-ai-agent` service. Human WebCall and the internal demo sandbox remain separate.

## Current Limitation

The checked-in provider profile is deterministic `fake` for CI and safe staging. Real STT, LLM, and TTS providers are not enabled by this PR. This PR must be merged only as an infrastructure skeleton and must not be described as production-ready.
