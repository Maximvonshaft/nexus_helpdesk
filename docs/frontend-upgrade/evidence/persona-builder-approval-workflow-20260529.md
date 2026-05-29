# Persona Builder Approval Workflow Evidence - 2026-05-29

## Scope

- Added `persona_profile_reviews` and Alembic revision `20260529_0041`.
- Added real Persona review commands:
  - `POST /api/persona-profiles/{id}/submit-review`
  - `GET /api/persona-profiles/reviews`
  - `POST /api/persona-profiles/reviews/{id}/approve`
  - `POST /api/persona-profiles/reviews/{id}/reject`
  - `POST /api/persona-profiles/reviews/{id}/publish`
- Updated `/api/lite/persona-builder` and `/persona-builder` so approval/release-window blocks are implemented instead of marked as missing.

## Local Validation

- `python -m py_compile backend\app\models_control_plane.py backend\app\schemas_control_plane.py backend\app\services\persona_service.py backend\app\api\persona_profiles.py backend\app\services\persona_builder_service.py backend\tests\test_persona_review_workflow.py backend\alembic\versions\20260529_0041_persona_profile_reviews.py`: pass.
- `python -m pytest -q backend\tests\test_persona_review_workflow.py backend\tests\test_persona_builder_contract.py backend\tests\test_control_plane_foundation.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_persona_approval_workflow`: 14 passed, 15 existing warnings.
- `node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `npm test`: 79 passed.
- `npm run build`: pass; existing LiveKit chunk-size warning remains.
- `npm run lint`: pass with existing 5 hook warnings.
- `cd backend; DATABASE_URL=sqlite:///./alembic_persona_approval_probe.db python -m alembic upgrade head`: pass.
- `cd backend; DATABASE_URL=sqlite:///./alembic_persona_approval_probe.db python -m alembic downgrade -1`: pass.
- `cd backend; DATABASE_URL=sqlite:///./alembic_persona_approval_probe.db python -m alembic upgrade head`: pass; temp DB removed after verification.
- `cd backend; python -m alembic heads`: `20260529_0041 (head)`.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/persona-builder` redirected to `/login` when unauthenticated; login screen rendered, no framework overlay, console error/warning count was 0.
