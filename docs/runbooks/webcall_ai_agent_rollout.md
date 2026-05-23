# WebCall AI Agent Rollout Runbook

## Scope

PR-0/PR-9 and Acceleration Pack A do not make WebCall AI functional yet. They only add the guarded architecture, schema, config, tests, no-op worker claim lifecycle, deterministic mock turn persistence, deterministic mock STT/TTS boundaries, a real STT/TTS provider contract skeleton, the first Deepgram STT adapter behind feature flags, a controlled static HTTPS audio reference source for STT input, a fake LiveKit AI participant ownership skeleton, a server-side LiveKit AI participant token issuer wrapper, and a backend no-media AI presence runtime. Acceleration Pack A does not implement functional AI voice. It does not subscribe to audio, publish audio, read WebRTC tracks, change frontend, call LLM/provider runtime/OpenClaw/OpenAI/Codex, call Speedaf, execute Speedaf writes, persist participant tokens, log participant tokens, expose AI participant tokens to browsers, or enable Deepgram by default. Keep all real AI voice execution disabled until a later worker PR explicitly adds and validates runtime behavior.

## Feature Flags

Foundation defaults are fail-closed:

```env
WEBCALL_AI_AGENT_ENABLED=false
WEBCALL_AI_AGENT_MODE=ai_first_human_fallback
WEBCALL_AI_AGENT_MAX_TURNS=6
WEBCALL_AI_AGENT_MAX_CALL_SECONDS=180
WEBCALL_STT_PROVIDER=mock
WEBCALL_TTS_PROVIDER=mock
WEBCALL_AI_AUDIO_REFERENCE_SOURCE=disabled
WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED=false
WEBCALL_AI_PARTICIPANT_ENABLED=false
WEBCALL_AI_PARTICIPANT_MODE=fake_room_client
WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS=300
WEBCALL_AI_PARTICIPANT_ID_PREFIX=ai_webcall
WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED=false
WEBCALL_AI_ROOM_PRESENCE_ENABLED=false
WEBCALL_AI_ROOM_PRESENCE_MODE=fake_no_media
WEBCALL_AI_ROOM_PRESENCE_JOIN_TIMEOUT_MS=5000
WEBCALL_AI_ROOM_PRESENCE_SMOKE_ENABLED=false
WEBCALL_AI_PROVIDER=provider_runtime
WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER=false
WEBCALL_AI_ALLOW_CANCEL=false
WEBCALL_AI_ALLOW_ADDRESS_UPDATE=false
WEBCALL_AI_TRANSCRIPT_ENABLED=true
WEBCALL_AI_SUMMARY_ENABLED=false
WEBCALL_AI_RECORD_RAW_AUDIO=false
```

Production must reject these values in this foundation PR:

```env
WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER=true
WEBCALL_AI_ALLOW_CANCEL=true
WEBCALL_AI_ALLOW_ADDRESS_UPDATE=true
WEBCALL_AI_RECORD_RAW_AUDIO=true
WEBCALL_AI_AUDIO_REFERENCE_SOURCE=static_fixture
WEBCALL_AI_PARTICIPANT_ENABLED=true
WEBCALL_AI_PARTICIPANT_MODE=livekit_token_issuer
WEBCALL_AI_ROOM_PRESENCE_ENABLED=true
```

## Rollout Stages

1. Foundation: merge docs, models, migration, config, schema, and safety tests with the agent disabled.
2. Worker skeleton: add a backend worker that can claim AI session lifecycle state without joining LiveKit media. PR-2 is no-op claim lifecycle only: claim, heartbeat, release, fail, and lease recovery metadata, with no media, STT, TTS, LLM, or Speedaf execution.
3. Deterministic mock turn: write one safe mock AI turn and one safe NexusDesk action decision for a claimed session, with no audio, STT, TTS, LLM, OpenClaw, or Speedaf calls.
4. Mock media: PR-4 adds deterministic mock STT/TTS boundaries so tests can validate turn lifecycle and handoff without external calls. It uses no audio, STT, TTS, LLM, OpenClaw, or Speedaf calls to real providers.
5. Provider contracts: PR-5 adds the real STT/TTS provider contract skeleton, fail-closed provider router, token-file config, timeout bounds, and canary config. It does not connect real provider SDKs or networks.
6. Deepgram STT: PR-6 adds a Deepgram pre-recorded STT adapter behind `WEBCALL_STT_PROVIDER=deepgram`, `WEBCALL_STT_DEEPGRAM_ENABLED=true`, token-file rules, HTTPS remote audio reference controls, and canary config. It does not enable real STT by default.
7. Controlled audio reference: PR-7 wires an optional static HTTPS `audio_reference` into STT input behind `WEBCALL_AI_AUDIO_REFERENCE_SOURCE=static_fixture`, `WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED=true`, and exact-host allowlist controls. It remains disabled by default and rejected in production.
8. Fake AI participant ownership: PR-8 creates a deterministic AI participant identity and participant row, issues fake token metadata, and performs fake join/leave transitions. It does not join LiveKit media and does not expose AI participant tokens to browsers.
9. LiveKit token issuer wrapper: PR-9 can issue a server-side AI participant token through the existing backend voice provider boundary when `WEBCALL_AI_PARTICIPANT_MODE=livekit_token_issuer` and `WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED=true`. It still does not join LiveKit media, stores no token, logs no token, and exposes no token to browsers.
10. No-media AI presence: Acceleration Pack A can issue or hold the server-side AI participant token, join fake or LiveKit no-media presence, run the existing mock turn, leave no-media presence, and release. It remains disabled by default, rejected in production, and does not implement functional AI voice.
11. Real media: later PRs connect LiveKit/WebRTC capture and TTS providers behind feature flags and canaries.
12. Tracking facts: allow backend-governed tracking lookup after redaction and caller confirmation.
13. Handoff: route cancel, address change, compensation/refund, complaint, driver/DSP responsibility, customs/payment disputes, legal/privacy questions, low confidence, and unsupported-language cases to a human agent.
14. Evidence: add transcript summaries, evidence cards, callback tasks, and operational dashboards.

## Deployment Checks

Before enabling any later WebCall AI runtime flag, confirm:

```text
Browser secret scan passes.
LLM cannot directly execute Speedaf writes.
Action Gate blocks forbidden actions.
LiveKit AI participant identity is backend-issued only.
Speedaf appCode, secretKey, sign material, full phone, full address, and raw payloads stay out of browser, LLM prompts, and logs.
Future real providers must implement the provider interfaces and remain behind feature flags.
```

## Rollback

Emergency rollback should turn all WebCall AI flags off and leave the new tables dormant. PR-4 adds no migration, so no database rollback is required. Do not drop the AI tables during emergency rollback; keeping dormant audit/schema tables avoids destructive recovery risk and preserves forward migration state.

Recommended rollback posture:

```text
WEBCALL_AI_AGENT_ENABLED=false
WEBCALL_AI_ALLOW_SPEEDAF_WORK_ORDER=false
WEBCALL_AI_ALLOW_CANCEL=false
WEBCALL_AI_ALLOW_ADDRESS_UPDATE=false
WEBCALL_AI_RECORD_RAW_AUDIO=false
WEBCALL_AI_AUDIO_REFERENCE_SOURCE=disabled
WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED=false
WEBCALL_AI_PARTICIPANT_ENABLED=false
WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED=false
WEBCALL_AI_ROOM_PRESENCE_ENABLED=false
WEBCALL_AI_ROOM_PRESENCE_SMOKE_ENABLED=false
```

If code rollback is required, run the deterministic Alembic downgrade only as part of a planned database rollback window, not as a first response to a runtime incident.

## Next PR

The next PR should add the first TTS adapter or a guarded real media bridge behind feature flags/canary, without joining LiveKit media from the browser or changing Speedaf behavior.
