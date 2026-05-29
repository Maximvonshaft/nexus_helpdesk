# QA Training Agent Appeals Evidence - 2026-05-29

## Scope

- Added `POST /api/lite/qa-training/appeals`.
- Persists appeal submissions as active `operator_tasks` with `task_type=qa_appeal`.
- Writes ticket timeline evidence with `TicketEvent.field_name=qa_agent_appeal`.
- Writes manager/audit evidence with `AdminAuditLog.action=qa.agent_appeal.submitted`.
- Updates `/api/lite/qa-training` so the Agent Appeal block and `agent_appeal_write_endpoint` fact are `implemented`.
- Updates `/qa-training` so leads can submit an appeal from a QA sample through the unified API client.

## Local Validation

- `python -m py_compile backend\app\schemas.py backend\app\services\qa_training_service.py backend\app\api\lite.py backend\tests\test_qa_training_contract.py`: pass.
- `python -m pytest -q backend\tests\test_qa_training_contract.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-qa-appeals`: 2 passed, 4 existing warnings.
- `python -m pytest -q backend\tests\test_qa_training_contract.py backend\tests\test_operator_queue.py backend\tests\test_operator_queue_api.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-qa-appeals-suite`: 10 passed, 10 existing warnings.
- `cd webapp; node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `cd webapp; npm test`: 79 passed.
- `cd webapp; npm run build`: pass; existing LiveKit chunk-size warning remains.
- `cd webapp; npm run lint`: pass with existing 5 hook warnings.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/qa-training` redirects to `/login` when unauthenticated; login screen renders, no Vite error overlay.
