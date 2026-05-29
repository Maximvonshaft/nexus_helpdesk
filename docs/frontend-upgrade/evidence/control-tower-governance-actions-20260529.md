# Control Tower Governance Actions Evidence - 2026-05-29

## Scope

- Added `POST /api/lite/control-tower/actions`.
- Persists manager commands as active `operator_tasks` with `task_type=control_tower_action`.
- Writes governance audit evidence with `AdminAuditLog.action=control_tower.action.submitted`.
- Reflects active action task id/status back into `/api/lite/control-tower` manager actions.
- Updates `/control-tower` so managers can create governance tasks from the action queue through the unified API client.
- Marks Provider / Channel Ops and Speedaf Wizard template blocks as implemented through the governance action contract plus existing capability-gated execution pages.

## Local Validation

- `python -m py_compile backend\app\schemas.py backend\app\api\lite.py backend\app\services\control_tower_service.py backend\tests\test_control_tower_contract.py`: pass.
- `python -m pytest -q backend\tests\test_control_tower_contract.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-control-actions`: 2 passed, 4 existing warnings.
- `python -m pytest -q backend\tests\test_control_tower_contract.py backend\tests\test_operator_queue.py backend\tests\test_operator_queue_api.py backend\tests\test_operator_queue_terminal_state.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-control-actions-suite`: 17 passed, 17 existing warnings.
- `cd webapp; node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `cd webapp; npm test`: 79 passed.
- `cd webapp; npm run build`: pass; existing LiveKit chunk-size warning remains.
- `cd webapp; npm run lint`: pass with existing 5 hook warnings.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/control-tower` redirects to `/login` when unauthenticated; login screen renders, no framework overlay, console error/warn count is 0, and account input focus interaction passed.
