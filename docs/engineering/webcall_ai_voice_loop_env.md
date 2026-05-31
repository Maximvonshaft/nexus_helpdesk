# WebCall AI Voice Loop Environment

This is an internal canary configuration shape. Do not paste secret values into Git, PRs, logs, or chat.

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
STT_ENDPOINT=https://stt-provider.example/v1/transcribe
LLM_ENDPOINT=https://llm-provider.example/v1/respond
TTS_ENDPOINT=https://tts-provider.example/v1/speech
STT_API_KEY_FILE=/run/secrets/webcall_stt_api_key
LLM_API_KEY_FILE=/run/secrets/webcall_llm_api_key
TTS_API_KEY_FILE=/run/secrets/webcall_tts_api_key

TRACKING_LOOKUP_ENDPOINT=
TRACKING_LOOKUP_API_KEY_FILE=
```

For the ProviderRuntime/Codex LLM bridge canary, keep STT/TTS fake or externally configured and switch only the LLM leg:

```dotenv
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

For streaming STT, switch the STT leg to Deepgram WebSocket streaming:

```dotenv
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

The checked-in streaming STT path sends PCM16 frames and consumes partial/final transcript events. Keep barge-in enabled only for the controlled LiveKit canary below so interruption evidence can be verified before public rollout.

The LiveKit collector uses a tracking-safe utterance window by default: at least 4000ms of customer audio, up to 12000ms, with 1500ms of post-speech silence before finalizing the turn. This prevents short pauses inside phrases like "BANANA SPEEDAF TEST. My tracking number is ABC123456789. Where is my parcel?" from being finalized after roughly 1.5 seconds. `WEBCALL_AI_POST_TTS_LISTEN_GRACE_MS=800` adds a short grace period after AI playback before the next listening turn so echo and playback tail do not become a fragmented STT request.

For streaming TTS and chunk publish, switch the TTS leg to Cartesia SSE:

```dotenv
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

Cartesia SSE chunks are decoded into PCM audio chunks and streamed into the server-side LiveKit `publish_ai_audio_stream()` path as they arrive; the worker no longer waits for the full TTS response before starting LiveKit publication. During server-side publication, barge-in can stop remaining LiveKit audio and preserve visitor frames for the next listening turn.

For Deepgram Aura-2 streaming TTS using the same Deepgram key as STT:

```dotenv
WEBCALL_AI_PROVIDER_PROFILE=hybrid
STT_PROVIDER=deepgram_streaming
TTS_PROVIDER=deepgram_streaming
STT_API_KEY_FILE=/run/secrets/deepgram_api_key
TTS_API_KEY_FILE=/run/secrets/deepgram_api_key
TTS_MODEL=aura-2-thalia-en
TTS_ENCODING=linear16
TTS_SAMPLE_RATE=48000
LLM_PROVIDER=provider_runtime
```

Deepgram TTS uses `wss://api.deepgram.com/v1/speak`, streams binary linear16 audio chunks into LiveKit as each chunk arrives, and supports the shared WebCall cancel token so barge-in can stop later provider chunks. API keys stay server-side in `*_API_KEY_FILE` secrets and are not exposed through runtime config or probe artifacts.

For barge-in:

```dotenv
WEBCALL_AI_BARGE_IN_ENABLED=true
WEBCALL_AI_BARGE_IN_MIN_SPEECH_MS=900
WEBCALL_AI_BARGE_IN_ENERGY_THRESHOLD=350
WEBCALL_AI_STT_MIN_AUDIO_MS=300
WEBCALL_AI_STT_SILENCE_RMS_THRESHOLD=80
```

When customer speech is detected while AI audio is publishing, remaining AI audio publication is stopped, the streaming TTS cancel token is signaled, `webcall_ai.response.interrupted` is written, and the captured customer frames are reused by the next listening turn. The default 900ms threshold is intentionally above short noise, echo, and brief acknowledgements during AI playback.

`LIVEKIT_API_KEY` may be sourced by deployment automation from `/opt/livekit_nexus/secrets.env`, but the API secret must be mounted as a file for production. If a rollback is needed, set `WEBCALL_AI_KILL_SWITCH=true` and stop the `webcall-ai-agent` compose profile.
