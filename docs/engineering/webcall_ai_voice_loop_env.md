# WebCall AI Voice Loop Environment

This is an internal canary configuration shape. Do not paste secret values into Git, PRs, logs, or chat.

```dotenv
WEBCALL_AI_PRODUCTION_ENABLED=true
WEBCALL_AI_AGENT_ENABLED=true
WEBCALL_AI_KILL_SWITCH=false
WEBCALL_AI_PUBLIC_ROLLOUT_MODE=internal
WEBCALL_AI_ALLOWED_ORIGINS=https://www.leakle.com
WEBCALL_AI_AGENT_LEASE_SECONDS=45
WEBCALL_AI_MIN_UTTERANCE_SECONDS=1
WEBCALL_AI_MAX_UTTERANCE_SECONDS=12
WEBCALL_AI_SILENCE_END_MS=700
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

`LIVEKIT_API_KEY` may be sourced by deployment automation from `/opt/livekit_nexus/secrets.env`, but the API secret must be mounted as a file for production. If a rollback is needed, set `WEBCALL_AI_KILL_SWITCH=true` and stop the `webcall-ai-agent` compose profile.
