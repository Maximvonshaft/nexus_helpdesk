# WebCall AI Real Voice Smoke Runbook

This runbook is for an internal smoke of `/webcall-ai`. Do not call the release production-ready until a real browser voice smoke passes on `https://www.leakle.com/webcall-ai`.

## Required Environment

```dotenv
WEBCALL_AI_PRODUCTION_ENABLED=true
WEBCALL_AI_AGENT_ENABLED=true
WEBCALL_AI_KILL_SWITCH=false
WEBCALL_AI_PUBLIC_ROLLOUT_MODE=internal
WEBCALL_AI_ALLOWED_ORIGINS=https://www.leakle.com
WEBCALL_AI_AGENT_LEASE_SECONDS=45
WEBCALL_AI_MIN_UTTERANCE_AUDIO_MS=4000
WEBCALL_AI_MAX_UTTERANCE_AUDIO_MS=12000
WEBCALL_AI_SILENCE_END_MS=1500
WEBCALL_AI_POST_TTS_LISTEN_GRACE_MS=800
WEBCALL_AI_AUDIO_SAMPLE_RATE=48000
WEBCALL_AI_RECORD_RAW_AUDIO=false
WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER=false
WEBCALL_AI_ALLOW_CANCEL=false
WEBCALL_AI_ALLOW_ADDRESS_UPDATE=false

WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ENABLED=true
WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES=/webcall-ai,/webcall,/webchat/voice
WEBCHAT_VOICE_CONNECT_SRC=self,wss://voice.leakle.com
WEBCHAT_VOICE_RECORDING_ENABLED=false
WEBCHAT_VOICE_TRANSCRIPTION_ENABLED=false

LIVEKIT_URL=wss://voice.leakle.com
LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key
LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret

WEBCALL_AI_PROVIDER_PROFILE=external
STT_PROVIDER=external
LLM_PROVIDER=external
TTS_PROVIDER=external
STT_ENDPOINT=https://stt-bridge.example/v1/transcribe
LLM_ENDPOINT=https://llm-bridge.example/v1/respond
TTS_ENDPOINT=https://tts-bridge.example/v1/speech
STT_API_KEY_FILE=/run/secrets/webcall_stt_api_key
LLM_API_KEY_FILE=/run/secrets/webcall_llm_api_key
TTS_API_KEY_FILE=/run/secrets/webcall_tts_api_key

TRACKING_LOOKUP_ENDPOINT=https://tracking-bridge.example/v1/lookup
TRACKING_LOOKUP_API_KEY_FILE=/run/secrets/webcall_tracking_api_key
```

## Secret Handling

- Source the LiveKit API key from `/opt/livekit_nexus/secrets.env` into `/run/secrets/livekit_api_key`.
- Mount `LIVEKIT_API_SECRET_FILE` from a root-owned secret file. Do not use inline `LIVEKIT_API_SECRET` in production.
- Provider tokens must be mounted as `*_API_KEY_FILE` under `/run/secrets` or an equivalent read-only secret path.
- Never paste secret values into logs, PRs, chat, docs, or browser responses.

## Provider Contracts

- STT: multipart `POST STT_ENDPOINT`, `Authorization: Bearer $(cat STT_API_KEY_FILE)`, field `audio` as WAV. LiveKit PCM is wrapped as WAV in memory; raw audio is not persisted. Form fields: `language`, `sample_rate`, `channels`. Response JSON: `text`, `language`, `confidence`.
- LLM: JSON `POST LLM_ENDPOINT`, `Authorization: Bearer $(cat LLM_API_KEY_FILE)`. Body includes `system`, `input`, `language`, `response_format=json`. Response JSON: `response_text`, `intent`, `handoff_required`, `handoff_reason`.
- TTS: JSON `POST TTS_ENDPOINT`, `Authorization: Bearer $(cat TTS_API_KEY_FILE)`. Body includes `text`, `language`, `voice`, `format=wav`. Response body is WAV or PCM16 with content type `audio/wav`, `audio/pcm`, `audio/l16`, or `application/octet-stream`.
- Tracking: `TRACKING_LOOKUP_ENDPOINT` must be read-only. It must never expose cancellation, address update, work order, payment, refund, or driver phone actions.

See `docs/engineering/voice_provider_bridge_contract.md` for the full bridge contract.

## Deploy Commands

From the server checkout:

```bash
docker compose -f deploy/docker-compose.server.yml pull app webcall-ai-agent
docker compose -f deploy/docker-compose.server.yml up -d app
docker compose -f deploy/docker-compose.server.yml --profile webcall-ai up -d webcall-ai-agent
```

## Curl Probes

Public runtime config must not expose secrets:

```bash
curl -fsS https://www.leakle.com/api/webcall-ai/runtime-config | jq .
```

Expected: `enabled=true`, `agent_enabled=true`, `status=ready`, `voice_provider=livekit`, `livekit_url=wss://voice.leakle.com`, no API key or secret fields.

Admin health requires auth with `runtime.manage`:

```bash
curl -fsS \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://www.leakle.com/api/admin/webcall-ai/health | jq .
```

Expected readiness block:

```json
{
  "livekit_configured": true,
  "stt_configured": true,
  "llm_configured": true,
  "tts_configured": true,
  "tracking_bridge_configured": true,
  "kill_switch": false,
  "rollout_mode": "internal",
  "fake_heartbeat_enabled": false,
  "recording_enabled": false,
  "raw_audio_persistence": false,
  "dangerous_write_actions_enabled": false,
  "final_status": "ready_for_internal_smoke"
}
```

## Browser Smoke

1. Open `https://www.leakle.com/webcall-ai`.
2. Click Start call.
3. Grant microphone access.
4. Confirm the browser joins the LiveKit room.
5. Confirm the AI participant joins and a voice greeting is heard.
6. Say a tracking question with a test tracking number.
7. Confirm the page moves through listening, thinking, speaking.
8. Confirm AI audio is heard in the browser.
9. Request human handoff and confirm the AI stops speaking.
10. End the call.

## Expected DB Evidence

Replace placeholders with the session ids from the smoke.

```sql
select public_id, status, ai_agent_status, ai_turn_count, ai_agent_error_code
from webchat_voice_sessions
where mode = 'livekit_ai_agent'
order by id desc
limit 5;

select turn_index, customer_text_redacted, ai_response_text_redacted, intent, handoff_required
from webchat_voice_ai_turns
where voice_session_id = :voice_session_id
order by turn_index;

select model_action, nexus_decision, speedaf_tool_name, result_status
from webchat_voice_ai_actions
where voice_session_id = :voice_session_id
order by id;

select event_type, payload_json, created_at
from webchat_events
where conversation_id = :conversation_id
  and event_type like 'webcall_ai.%'
order by id;
```

Expected event sequence includes `webcall_ai.session.created`, `webcall_ai.agent.joined`, `webcall_ai.agent.listening`, `webcall_ai.transcript.final`, `webcall_ai.tool.called`, `webcall_ai.response.generated`, `webcall_ai.tts.ready`, `webcall_ai.agent.speaking`, `webcall_ai.response.spoken`, and `webcall_ai.session.ended` or `webcall_ai.handoff.requested`.

## Rollback

Immediate kill switch:

```bash
perl -0pi -e 's/WEBCALL_AI_KILL_SWITCH=false/WEBCALL_AI_KILL_SWITCH=true/' .env.prod
docker compose -f deploy/docker-compose.server.yml up -d app
docker compose -f deploy/docker-compose.server.yml --profile webcall-ai stop webcall-ai-agent
```

Full disable:

```bash
perl -0pi -e 's/WEBCALL_AI_PRODUCTION_ENABLED=true/WEBCALL_AI_PRODUCTION_ENABLED=false/' .env.prod
perl -0pi -e 's/WEBCALL_AI_AGENT_ENABLED=true/WEBCALL_AI_AGENT_ENABLED=false/' .env.prod
docker compose -f deploy/docker-compose.server.yml up -d app
docker compose -f deploy/docker-compose.server.yml --profile webcall-ai down webcall-ai-agent
```

No DB rollback is required for this additive evidence path.
