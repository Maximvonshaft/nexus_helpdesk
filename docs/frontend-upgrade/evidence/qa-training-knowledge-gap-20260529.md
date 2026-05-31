# QA Training Knowledge Gap Evidence - 2026-05-29

## Scope

- Added `POST /api/lite/qa-training/knowledge-gaps`.
- Creates or updates a draft `AIConfigResource` with `config_type=knowledge` from a sampled QA knowledge gap.
- Persists AI Ops follow-up work as active `operator_tasks` with `task_type=knowledge_gap`.
- Writes ticket timeline evidence with `TicketEvent.field_name=qa_knowledge_gap`.
- Writes manager/audit evidence with `AdminAuditLog.action=qa.knowledge_gap.submitted`.
- Updates `/api/lite/qa-training` so the Knowledge Gap Loop block and `knowledge_gap_write_endpoint` fact are `implemented`.
- Updates `/qa-training` so leads can create knowledge drafts from QA gap rows through the unified API client.

## Local Validation

- `python -m py_compile backend\app\schemas.py backend\app\api\lite.py backend\app\services\qa_training_service.py backend\tests\test_qa_training_contract.py`: pass.
- `python -m pytest -q backend\tests\test_qa_training_contract.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-qa-gap`: 2 passed, 4 existing warnings.
- `python -m pytest -q backend\tests\test_qa_training_contract.py backend\tests\test_operator_queue.py backend\tests\test_operator_queue_api.py backend\tests\test_operator_queue_terminal_state.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-qa-gap-suite`: 17 passed, 17 existing warnings.
- `cd webapp; node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `cd webapp; npm test`: 79 passed.
- `cd webapp; npm run build`: pass; existing LiveKit chunk-size warning remains.
- `cd webapp; npm run lint`: pass with existing 5 hook warnings.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/qa-training` redirects to `/login` when unauthenticated; login screen renders, no framework overlay, console error/warn count is 0, and account input focus interaction passed.
