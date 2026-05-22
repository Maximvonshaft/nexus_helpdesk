# WebCall AI Agent Rollout Runbook

## Scope

PR-0/PR-1 does not make WebCall AI functional yet. It only adds the guarded architecture, schema, config, and tests. Keep all AI voice execution disabled until a later worker PR explicitly adds and validates runtime behavior.

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
2. Worker skeleton: add a backend worker that can claim AI session lifecycle state without joining LiveKit media.
3. Mock media: add deterministic mock STT/TTS so tests can validate turn lifecycle and handoff without external calls.
4. Real media: connect real STT/TTS providers behind feature flags and canaries.
5. Tracking facts: allow backend-governed tracking lookup after redaction and caller confirmation.
6. Handoff: route cancel, address change, compensation/refund, complaint, driver/DSP responsibility, customs/payment disputes, legal/privacy questions, low confidence, and unsupported-language cases to a human agent.
7. Evidence: add transcript summaries, evidence cards, callback tasks, and operational dashboards.

## Deployment Checks

Before enabling any later WebCall AI runtime flag, confirm:

```text
Browser secret scan passes.
LLM cannot directly execute Speedaf writes.
Action Gate blocks forbidden actions.
LiveKit AI participant identity is backend-issued only.
Speedaf appCode, secretKey, sign material, full phone, full address, and raw payloads stay out of browser, LLM prompts, and logs.
```

## Rollback

Emergency rollback should turn all WebCall AI flags off and leave the new tables dormant. Do not drop the AI tables during emergency rollback; keeping dormant audit/schema tables avoids destructive recovery risk and preserves forward migration state.

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

PR-2 should implement webcall-ai-worker skeleton and AI session claim lifecycle only. It should not connect real STT/TTS yet.
