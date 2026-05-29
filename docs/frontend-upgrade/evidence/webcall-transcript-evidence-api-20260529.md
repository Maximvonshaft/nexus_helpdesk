# WebCall Transcript Evidence API

Date: 2026-05-29
Branch: `codex/webcall-transcript-evidence-api`
Base: stacked on `codex/webcall-call-notes-template-api` / PR #328

## Scope

This PR closes the next v1.7.8 WebCall workbench evidence gap without claiming a full telephony adapter.

- Adds `GET /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/evidence`.
- Requires `webcall.voice.read` and normal ticket visibility checks.
- Returns only redacted transcript segment text from `webchat_voice_transcript_segments`.
- Returns voice AI turns and AI action decisions from `webchat_voice_ai_turns` and `webchat_voice_ai_actions`.
- Wires `AgentWebCallPanel` to the unified API client and renders `Live Transcript / AI Evidence`, AI turn evidence, and AI action decisions.

## Local Validation

Actions are disabled for this stack, so validation is local.

```powershell
python -m py_compile backend\app\voice_schemas.py backend\app\api\webchat_voice.py backend\app\services\webchat_voice_service.py backend\tests\test_webchat_voice_api.py
python -m pytest -q backend\tests\test_webchat_voice_api.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-webcall-evidence-suite-local
node --test tests\webcall-operator-workbench-contract.test.mjs tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs
npm test
npm run build
npm run lint
git diff --check
```

Results:

- Python compile: passed.
- Backend pytest: 26 passed, 11 existing warnings.
- Focused WebCall frontend contracts: 41 passed.
- Webapp test suite: 81 passed.
- Production build: passed; existing LiveKit vendor chunk size warning remains.
- ESLint: 0 errors, 5 existing react-hooks warnings.
- Whitespace check: passed.
- Browser smoke: `/webcall` redirects to `/login` for an unauthenticated user, renders the login form, and has no blocking fixed overlay.

## Remaining Risk

This PR is a real evidence read path, not live STT streaming or a provider-side call-control adapter. Remaining WebCall parity work is live-console polish and future telephony actions such as hold, transfer, keypad, and add participant when the backend adapter exists.
