# Persona Builder Runtime Evidence - 2026-05-29

## Scope

- Implemented dedicated runtime evidence contract: `POST /api/persona-profiles/runtime-evidence`.
- The endpoint reuses the production `build_webchat_runtime_context` path and returns matched Persona, match rank, sanitized runtime context, persona identity context, and evidence counters.
- `/persona-builder` now calls the endpoint through the unified webapp API client and exposes a runtime evidence command in the Runtime Evidence card.
- `/api/lite/persona-builder` now marks the `runtime-evidence` lifecycle and template block as `implemented`.

## Local Validation

- `python -m py_compile backend\app\schemas_control_plane.py backend\app\api\persona_profiles.py backend\app\services\persona_builder_service.py backend\tests\test_persona_builder_contract.py`
- `python -m pytest -q backend\tests\test_persona_builder_contract.py backend\tests\test_persona_review_workflow.py backend\tests\test_knowledge_runtime_context.py backend\tests\test_knowledge_rag_runtime.py -p no:cacheprovider --tb=short --basetemp=C:\Users\Maxim\Documents\nexus\.pytest-tmp-persona-runtime`
  - Result: `30 passed, 20 warnings`.
  - Warnings are existing FastAPI `on_event` deprecation and SQLite foreign-key drop-order warnings.
- `node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`
  - Result: `36 passed`.
- `npm test`
  - Result: `79 passed`.
- `npm run build`
  - Result: passed; existing LiveKit chunk-size warning remains.
- `npm run lint`
  - Result: passed with the existing 5 hook dependency warnings.
- `git diff --check`
  - Result: passed.

## Browser Smoke

- Target flow: `http://127.0.0.1:5174/persona-builder` unauthenticated route -> `/login`.
- Result: redirected to `http://127.0.0.1:5174/login`, title `登录 · 客服工作台`.
- DOM contained the login heading, account/password controls, and login button.
- Framework overlay check: no Vite/React/Webpack overlay text in DOM snapshot.
- Console health: `0` error/warn logs.
- Interaction proof: account textbox resolved to exactly one element and focus state moved to the input.
- Screenshot capture was attempted twice through the in-app browser and failed with local CDP `Page.captureScreenshot` timeout; DOM and console checks completed.
