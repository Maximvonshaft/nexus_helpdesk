# WebCall AI Final Production Report

## 1. Branch

`codex/webcall-ai-infra-skeleton`

## 2. Base SHA

`fc390eb690718cf7a9faa89950a9a26f34a052f1`

## 3. Final SHA

Pending next commit.

## 4. PR URL

https://github.com/Maximvonshaft/nexus_helpdesk/pull/232

## 5. Files changed

- Backend production config, worker, provider contracts, session guardrails, admin health, evidence persistence.
- LiveKit RTC I/O boundary, continuous bounded worker loop, tracking fallback API, and provider HTTP adapters.
- Frontend `/webcall-ai` timeline polling and persisted event display.
- GitHub Actions WebCall AI final quality gate.
- Environment example and voice path/CSP defaults.

## 6. What was implemented

- Kill switch, rollout mode, allowed origins, agent lease seconds, and external provider readiness checks.
- Production worker no longer runs fake heartbeat by default; fake heartbeat requires `WEBCALL_AI_TEST_FAKE_HEARTBEAT=true`.
- Claim/lease lifecycle for `livekit_ai_agent` sessions.
- Provider router plus fail-closed external STT/LLM/TTS adapter boundaries.
- External STT/LLM/TTS HTTP adapters with token-file secret loading, timeout/retry handling, and provider error classification.
- LiveKit SDK-backed media I/O path with AI participant join, visitor audio collection, and TTS audio publication support.
- LiveKit audio turns now carry PCM metadata (`sample_rate`, `channels`, `mime_type`) and raw PCM is wrapped in-memory as WAV for STT bridge uploads.
- Energy-based VAD/silence cut detects utterance end before the max utterance timeout.
- Evidence semantics now separate `response.generated`, `tts.ready`, successful `response.spoken`, and `response.publish_failed`.
- Session release clears AI quota for handoff and terminalizes visitor disconnect/max duration/session ended cases.
- Bounded multi-turn call loop with greeting, heartbeat/lease refresh, max-turn/max-duration/handoff/visitor-disconnect/kill-switch exits.
- Redacted transcript, AI turn, AI action, and timeline event persistence path.
- Admin health endpoint and customer timeline polling.
- CI gate for backend contracts, frontend typecheck, and secret scanning.

## 7. What remains out of scope

- Production spoken smoke on `https://www.leakle.com/webcall-ai` has not been executed from this local environment.
- Read-only tracking lookup remains fail-closed as `not_configured` until an approved Speedaf read-only endpoint and token-file secret are configured.

## 8. New env flags

- `WEBCALL_AI_KILL_SWITCH`
- `WEBCALL_AI_PUBLIC_ROLLOUT_MODE`
- `WEBCALL_AI_ALLOWED_ORIGINS`
- `WEBCALL_AI_AGENT_LEASE_SECONDS`
- `STT_ENDPOINT`, `STT_API_KEY_FILE`
- `LLM_ENDPOINT`, `LLM_API_KEY_FILE`
- `TTS_ENDPOINT`, `TTS_API_KEY_FILE`
- `LIVEKIT_API_KEY_FILE`, `LIVEKIT_API_SECRET_FILE`
- `TRACKING_LOOKUP_ENDPOINT`, `TRACKING_LOOKUP_API_KEY_FILE`
- `WEBCALL_AI_MIN_UTTERANCE_SECONDS`
- `WEBCALL_AI_MAX_UTTERANCE_SECONDS`
- `WEBCALL_AI_SILENCE_END_MS`
- `WEBCALL_AI_AUDIO_SAMPLE_RATE`

## 9. Tests run and results

- `npm --prefix webapp run typecheck` passed.
- `py -3.12 -m py_compile ...` passed for changed backend Python files.
- `git diff --check` passed.
- Secret scan command passed with only the CI grep line itself matching.
- `py -3.12 -m pytest -q backend/tests/test_webcall_ai_production.py backend/tests/test_webcall_ai_voice_loop.py` was not run locally because `pytest` is not installed in the local Python 3.12 environment. The GitHub Actions gate installs dependencies and runs both test files in CI.

## 10. Manual smoke steps

Not executed locally. Required smoke remains:

1. Enable LiveKit and provider secrets through file-mounted secrets.
2. Start API and `webcall-ai-agent` profile.
3. Open `https://www.leakle.com/webcall-ai`.
4. Start call, confirm AI joins, voice greeting is heard, tracking question is answered by voice, handoff/end work.

## 11. DB evidence queries

Use redacted evidence only:

```sql
select public_id, ai_agent_status, ai_turn_count, ai_agent_error_code
from webchat_voice_sessions
where mode = 'livekit_ai_agent'
order by id desc
limit 10;

select turn_index, customer_text_redacted, ai_response_text_redacted, intent, handoff_required
from webchat_voice_ai_turns
where voice_session_id = :voice_session_id
order by turn_index;

select event_type, payload_json, created_at
from webchat_events
where conversation_id = :conversation_id
order by id;
```

## 12. Security/privacy evidence

- Raw audio storage remains disabled.
- Dangerous write actions remain rejected by config validation.
- Production rejects inline STT/LLM/TTS API keys; use `*_FILE` secrets.
- Runtime config does not expose LiveKit API key/secret or provider secrets.

## 13. Deployment notes

Deploy as internal canary only. Keep `WEBCALL_AI_PUBLIC_ROLLOUT_MODE=internal` until real RTC/provider adapters and read-only tracking pass smoke. Use `WEBCALL_AI_KILL_SWITCH=true` for immediate disable.

## 14. Rollback plan

Set `WEBCALL_AI_KILL_SWITCH=true`, stop the `webcall-ai-agent` profile, and leave DB evidence tables intact. No DB rollback is required for this additive change.

## 15. Merge readiness verdict

READY_FOR_REVIEW
