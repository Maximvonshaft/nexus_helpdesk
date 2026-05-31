# WebCall AI Infrastructure Skeleton Runbook

## Scope

This runbook covers the repository-side infrastructure skeleton for the future WebCall AI path at `/webcall-ai`.
It is not a production-ready release. It keeps `/webcall-ai-demo` as an internal sandbox, does not store raw audio by default, and does not enable real customer traffic by default.

## Runtime Flags

Required staging validation flags, applied only after CI is green and runtime secrets are present:

```text
WEBCALL_AI_PRODUCTION_ENABLED=true
WEBCALL_AI_AGENT_ENABLED=true
WEBCALL_AI_PROVIDER_PROFILE=external
WEBCALL_AI_KILL_SWITCH=false
WEBCALL_AI_PUBLIC_ROLLOUT_MODE=internal
WEBCALL_AI_RECORD_RAW_AUDIO=false
WEBCHAT_VOICE_PROVIDER=livekit
WEBCHAT_VOICE_ENABLED=true
LIVEKIT_URL=wss://voice.leakle.com
LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key
LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret
STT_PROVIDER=external
LLM_PROVIDER=external
TTS_PROVIDER=external
STT_API_KEY_FILE=/run/secrets/webcall_stt_api_key
LLM_API_KEY_FILE=/run/secrets/webcall_llm_api_key
TTS_API_KEY_FILE=/run/secrets/webcall_tts_api_key
```

Codex/ProviderRuntime LLM can be enabled independently from STT/TTS with the hybrid profile:

```text
WEBCALL_AI_PROVIDER_PROFILE=hybrid
STT_PROVIDER=fake
LLM_PROVIDER=provider_runtime
TTS_PROVIDER=fake
WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER=codex_app_server
WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID=default
WEBCALL_AI_PROVIDER_RUNTIME_CHANNEL_KEY=webcall_ai
WEBCALL_AI_PROVIDER_RUNTIME_SCENARIO=webcall_ai_decision
WEBCALL_AI_PROVIDER_RUNTIME_OUTPUT_CONTRACT=speedaf_webchat_fast_reply_v1
```

For real audio rollout, replace fake STT/TTS with approved external or streaming providers in a separate canary PR. The current ProviderRuntime LLM path proves strict text-in/text-out decisioning and persisted turn evidence; it does not by itself add streaming STT, streaming TTS audio chunks, or barge-in.

Deepgram streaming STT can be canaried on the STT leg:

```text
WEBCALL_AI_PROVIDER_PROFILE=hybrid
STT_PROVIDER=deepgram_streaming
STT_API_KEY_FILE=/run/secrets/deepgram_api_key
STT_MODEL=nova-3
STT_LANGUAGE=en
STT_INTERIM_RESULTS=true
STT_ENDPOINTING_MS=300
LLM_PROVIDER=provider_runtime
TTS_PROVIDER=fake
```

This streams PCM16 frames over Deepgram WebSocket and consumes interim/final transcript events. The current worker still uses a sequential utterance loop; duplex listening while speaking remains the barge-in PR.

Cartesia streaming TTS can be canaried on the TTS leg:

```text
WEBCALL_AI_PROVIDER_PROFILE=hybrid
STT_PROVIDER=deepgram_streaming
LLM_PROVIDER=provider_runtime
TTS_PROVIDER=cartesia_streaming
TTS_API_KEY_FILE=/run/secrets/cartesia_api_key
TTS_VOICE_ID=<server-only voice id>
TTS_MODEL=sonic-3.5
TTS_SAMPLE_RATE=24000
CARTESIA_VERSION=2026-03-01
```

This uses `POST /tts/sse`, decodes `chunk` event audio data, and publishes audio chunks through `publish_ai_audio_stream()`. The full audio bytes are still retained in the existing turn payload for fallback publication and evidence compatibility.

Duplex barge-in can be enabled with:

```text
WEBCALL_AI_BARGE_IN_ENABLED=true
WEBCALL_AI_BARGE_IN_MIN_SPEECH_MS=300
WEBCALL_AI_BARGE_IN_ENERGY_THRESHOLD=350
```

During AI audio publication the worker checks inbound LiveKit audio frames. If visitor speech crosses the threshold, the worker stops publishing the remaining AI audio, writes `webcall_ai.response.interrupted`, preserves the visitor frames for the next `collect_next_customer_utterance()`, and returns to listening. Provider-side TTS generation cancellation is still limited by each TTS adapter; this path cancels server-side LiveKit publication.

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
WEBCALL_AI_KILL_SWITCH=true
WEBCALL_AI_PRODUCTION_ENABLED=false
WEBCALL_AI_AGENT_ENABLED=false
```

Then restart the app and stop the `webcall-ai-agent` service. Human WebCall and the internal demo sandbox remain separate.

## Current Limitation

The checked-in runtime remains fail-closed until approved LiveKit, STT, LLM, TTS, and read-only tracking provider configuration is present and a real browser voice smoke test passes. ProviderRuntime LLM is supported for WebCall AI production turns, but streaming STT, streaming TTS chunk publish, duplex barge-in, and metrics dashboards remain separate rollout gaps.
