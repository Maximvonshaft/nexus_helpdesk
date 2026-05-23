# WebCall AI Demo Sandbox Runbook

## Purpose

The WebCall AI Demo Sandbox is an internal-only, admin/runtime demo surface for showing the WebCall AI flow without enabling customer-facing AI voice, recording, raw audio storage, production transcription, or external write actions.

## Feature Flags

Default fail-closed values:

```env
WEBCALL_AI_DEMO_LAB_ENABLED=false
WEBCALL_AI_DEMO_LAB_KILL_SWITCH=true
WEBCALL_AI_DEMO_LAB_MODE=simulated_full_loop
WEBCALL_AI_DEMO_LAB_ALLOW_BROWSER_SPEECH=true
WEBCALL_AI_DEMO_LAB_ALLOW_REAL_MEDIA=false
WEBCALL_AI_DEMO_LAB_TENANT_ALLOWLIST=
WEBCALL_AI_DEMO_LAB_MAX_ACTIVE_SESSIONS=3
WEBCALL_AI_DEMO_LAB_MAX_TURNS_PER_SESSION=8
WEBCALL_AI_DEMO_LAB_MAX_INPUT_CHARS=1000
WEBCALL_AI_DEMO_LAB_EVENT_RETENTION_LIMIT=200
```

Staging/internal demo enablement:

```env
WEBCALL_AI_DEMO_LAB_ENABLED=true
WEBCALL_AI_DEMO_LAB_KILL_SWITCH=false
WEBCALL_AI_DEMO_LAB_MODE=simulated_full_loop
WEBCALL_AI_DEMO_LAB_ALLOW_BROWSER_SPEECH=true
WEBCALL_AI_DEMO_LAB_ALLOW_REAL_MEDIA=false
```

Do not enable this as a public customer WebCall AI entry.

## Status Check

Use the admin-only endpoint:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  /api/admin/webcall-ai-demo/status
```

Provider runtime status also includes `webcall_ai_demo_lab`.

## Browser Demo Steps

1. Sign in as an admin/runtime operator.
2. Open `/webcall-ai-demo`.
3. Confirm the status is `ready`.
4. Create a demo session.
5. Send a typed turn.
6. If the browser supports speech recognition, use the mic control to populate the typed input.
7. If the browser supports speech synthesis, the AI reply may play through `speechSynthesis`; otherwise read the text reply.
8. Refresh the evidence timeline.
9. End the session.

## DB Evidence Queries

```sql
SELECT public_id, mode, status, recording_status, transcript_status, ai_agent_status, ai_turn_count
FROM webchat_voice_sessions
WHERE mode = 'internal_ai_demo'
ORDER BY id DESC
LIMIT 20;

SELECT voice_session_id, provider, speaker_type, text_redacted, redaction_status, created_at
FROM webchat_voice_transcript_segments
WHERE provider = 'demo_lab'
ORDER BY id DESC
LIMIT 20;

SELECT voice_session_id, turn_index, intent, action, handoff_required, provider, created_at
FROM webchat_voice_ai_turns
WHERE provider = 'demo_lab'
ORDER BY id DESC
LIMIT 20;
```

## Smoke Tests

1. `/healthz`
2. `/readyz`
3. `/api/webchat/voice/runtime-config` unchanged
4. `/api/admin/webcall-ai-demo/status` returns `ready` when enabled and kill switch is off
5. create demo session
6. send typed turn
7. verify transcript and AI turn rows
8. end session
9. turn after end returns conflict

## Rollback

Feature rollback:

1. Set `WEBCALL_AI_DEMO_LAB_KILL_SWITCH=true`.
2. Restart the app.
3. Confirm status is `blocked`.
4. Confirm create/turn endpoints reject.
5. Optionally set `WEBCALL_AI_DEMO_LAB_ENABLED=false`.

Image rollback:

1. Redeploy previous image.
2. Confirm `/healthz` and `/readyz`.
3. Confirm public voice runtime config remains unchanged.

## Limitations

- Demo AI replies are deterministic and safe; they do not verify live parcel status.
- Browser speech recognition and TTS are optional browser APIs.
- No raw audio is persisted.
- No external STT/TTS provider is enabled by default.
- No Speedaf write actions are performed.
- This is not a customer-facing WebCall AI rollout.
