# WebCall Session Action Commands Evidence

Date: 2026-05-29
Branch: `codex/webcall-session-action-commands`
Base: stacked on `codex/webcall-transcript-evidence-api` / PR #329

## Scope

This PR closes the next v1.7.8 WebCall cockpit gap for call-control actions without claiming a provider telephony adapter is already available.

- Adds `webchat_voice_session_actions` as the durable operator command ledger.
- Adds `webcall.voice.control` and uses it for hold, resume, mute, unmute, keypad, transfer and add-participant commands.
- Adds `POST /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/actions`.
- Adds `GET /api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/actions`.
- Writes TicketEvent, WebchatEvent and AdminAuditLog evidence for every command.
- Redacts keypad digits from response, action payload storage, ticket timeline payloads and audit payloads.
- Wires `AgentWebCallPanel` session actions through the unified `webchatVoiceApi` client and shows the recent action ledger.

## Local Validation

Actions are disabled for this stack, so validation is local.

```powershell
python -m py_compile backend\app\voice_models.py backend\app\voice_schemas.py backend\app\api\webchat_voice.py backend\app\services\permissions.py backend\app\services\webchat_voice_service.py backend\tests\test_webchat_voice_api.py backend\tests\test_rbac_capability_contracts.py
python -m py_compile backend\alembic\versions\20260529_0042_webcall_session_actions.py
python -m pytest -q backend\tests\test_webchat_voice_api.py backend\tests\test_rbac_capability_contracts.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-webcall-actions-local
python -m pytest -q backend\tests\test_migration_drift_gate.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-migration-gate-local
node --test tests\webcall-operator-workbench-contract.test.mjs tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs
npm test
npm run build
npm run lint
git diff --check
```

Results:

- Python compile: passed, including the new Alembic migration.
- Backend pytest: 30 passed, 11 existing warnings.
- Migration drift gate: 3 passed.
- Focused WebCall frontend contracts: 42 passed.
- Webapp test suite: 82 passed.
- Production build: passed; existing LiveKit vendor chunk size warning remains.
- ESLint: 0 errors, 5 existing react-hooks warnings.
- Whitespace check: passed.
- Browser smoke: `/webcall` redirects to `/login` for an unauthenticated user, renders the login form, and has no blocking fixed overlay.

## Remaining Risk

This is a real backend command/audit path, not a provider-side hold/transfer/keypad adapter. The response and UI keep that distinction explicit with `provider_adapter_pending` until a telephony adapter executes commands against the provider.
