# Knowledge Studio Conflict / Golden Test Evidence - 2026-05-29

## Scope

- Added `POST /api/knowledge-items/conflict-check` for real KnowledgeItem scope/question/alias conflict scanning.
- Added `POST /api/knowledge-items/golden-test` for published KnowledgeChunk retrieval assertions:
  - top hit minimum score
  - expected source item
  - expected answer evidence
  - forbidden answer guard
- Updated `/api/lite/knowledge-studio` so conflict scan and golden test blocks are `implemented`.
- Updated `/knowledge-studio` to run both commands through the unified API client, not raw fetch.

## Local Validation

- `python -m py_compile backend\app\schemas_control_plane.py backend\app\services\knowledge_service.py backend\app\services\knowledge_studio_service.py backend\app\api\knowledge_items.py backend\tests\test_knowledge_studio_contract.py`: pass.
- `python -m pytest -q backend\tests\test_knowledge_studio_contract.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-knowledge-conflict-golden`: 2 passed, 4 existing warnings.
- `python -m pytest -q backend\tests\test_knowledge_studio_contract.py backend\tests\test_knowledge_items.py backend\tests\test_knowledge_runtime_context.py backend\tests\test_knowledge_rag_runtime.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-knowledge-suite`: 41 passed, 30 existing warnings.
- `cd webapp; node --test tests\operator-console-contract.test.mjs`: 27 passed.
- `cd webapp; node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `cd webapp; npm test`: 79 passed.
- `cd webapp; npm run build`: pass; existing LiveKit chunk-size warning remains.
- `cd webapp; npm run lint`: pass with existing 5 hook warnings.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/knowledge-studio` redirects to `/login` when unauthenticated; login screen renders, no Vite error overlay.
