# WebCall AI Agent Rollout Runbook

## Scope

PR-0/PR-6 does not make WebCall AI functional yet. It only adds the guarded architecture, schema, config, tests, no-op claim lifecycle, deterministic mock turn persistence, deterministic mock STT/TTS boundaries, a real STT/TTS provider contract skeleton, and the first Deepgram STT adapter behind feature flags. PR-6 does not implement functional AI voice. It does not join LiveKit, does not read/publish WebRTC audio, does not change frontend, does not call LLM/provider runtime, does not call OpenClaw, does not call Speedaf, and does not enable real STT by default. Keep all real AI voice execution disabled until a later worker PR explicitly adds and validates runtime behavior.

## Feature Flags

Foundation defaults are fail-closed:

```env
WEBCALL_AI_AGENT_ENABLED=false
WEBCALL_AI_AGENT_MODE=ai_first_human_fallback
WEBCALL_AI_AGENT_MAX_TURNS=6
WEBCALL_AI_AGENT_MAX_CALL_SECONDS=180
WEBCALL_STT_PROVIDER=mock
WEBCALL_TTS_PROVIDER=mock
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
```

## Rollout Stages

1. Foundation: merge docs, models, migration, config, schema, and safety tests with the agent disabled.
2. Worker skeleton: add a backend worker that can claim AI session lifecycle state without joining LiveKit media. PR-2 is no-op claim lifecycle only: claim, heartbeat, release, fail, and lease recovery metadata, with no media, STT, TTS, LLM, or Speedaf execution.
3. Deterministic mock turn: write one safe mock AI turn and one safe NexusDesk action decision for a claimed session, with no audio, STT, TTS, LLM, OpenClaw, or Speedaf calls.
4. Mock media: PR-4 adds deterministic mock STT/TTS boundaries so tests can validate turn lifecycle and handoff without external calls. It uses no audio, STT, TTS, LLM, OpenClaw, or Speedaf calls to real providers.
5. Provider contracts: PR-5 adds the real STT/TTS provider contract skeleton, fail-closed provider router, token-file config, timeout bounds, and canary config. It does not connect real provider SDKs or networks.
6. Deepgram STT: PR-6 adds a Deepgram pre-recorded STT adapter behind `WEBCALL_STT_PROVIDER=deepgram`, `WEBCALL_STT_DEEPGRAM_ENABLED=true`, token-file rules, HTTPS remote audio reference controls, and canary config. It does not enable real STT by default.
7. Real media: later PRs connect LiveKit/WebRTC capture and TTS providers behind feature flags and canaries.
8. Tracking facts: allow backend-governed tracking lookup after redaction and caller confirmation.
9. Handoff: route cancel, address change, compensation/refund, complaint, driver/DSP responsibility, customs/payment disputes, legal/privacy questions, low confidence, and unsupported-language cases to a human agent.
10. Evidence: add transcript summaries, evidence cards, callback tasks, and operational dashboards.

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
```

If code rollback is required, run the deterministic Alembic downgrade only as part of a planned database rollback window, not as a first response to a runtime incident.

## Next PR

The next PR should wire a controlled audio-reference source into the worker or add the first TTS adapter behind feature flags/canary, without joining LiveKit media from the browser or changing Speedaf behavior.
