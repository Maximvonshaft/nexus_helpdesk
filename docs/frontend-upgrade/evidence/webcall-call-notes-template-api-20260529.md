# WebCall Call Notes Template API Evidence

Date: 2026-05-29
Branch: `codex/webcall-call-notes-template-api`
Base: stacked on `codex/control-tower-governance-actions` / PR #327

## Scope

This PR closes the v1.7.8 WebCall workbench call-note gap without expanding into unrelated telephony features.

- Adds `POST /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/notes`.
- Requires both `webcall.voice.read` and `note.write.internal`, then applies normal ticket visibility checks.
- Persists the operator note as `TicketInternalNote`.
- Writes `TicketEvent(internal_note_added)`, `WebchatEvent(voice.session.note_saved)` and `AdminAuditLog(webcall.voice.note_saved)`.
- Wires `/webcall` `AgentWebCallPanel` through the unified `api.webchatVoiceSaveNote` client.

## Local Validation

Actions are disabled for this stack, so validation is local.

```powershell
python -m py_compile backend\app\voice_schemas.py backend\app\api\webchat_voice.py backend\app\services\webchat_voice_service.py backend\tests\test_webchat_voice_api.py
python -m pytest -q backend\tests\test_webchat_voice_api.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-webcall-notes
node --test tests\webcall-operator-workbench-contract.test.mjs tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs
python -m pytest -q backend\tests\test_webchat_voice_api.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-webcall-notes-suite
npm test
npm run build
npm run lint
git diff --check
```

Result: all commands passed locally. `npm run build` retains the existing LiveKit chunk-size warning, and `npm run lint` retains the existing 5 React hook warnings.

Browser smoke: `/webcall` redirects unauthenticated users to `/login`, the login screen renders, there is no Vite/Next/Webpack overlay, console error/warn count is 0, and the account input accepts focus.

## Remaining Risk

This PR does not implement live transcript streaming or post-call summary generation. It makes operator notes a real backend write path and keeps the remaining WebCall visual/runtime parity work isolated.
