# Persona Builder Template API Evidence - 2026-05-29

## Scope

- Added `/api/lite/persona-builder` as a real read-model over `PersonaProfile` and `PersonaProfileVersion`.
- Added top-level `/persona-builder` workbench with unified API client, routeAccess RBAC, AppShell navigation and CommandPalette entry.
- Persona edit/publish/rollback commands stay linked to existing `/ai-control`.
- Follow-up approval workflow adds `persona_profile_reviews` plus submit-review, approve/reject, and release-window publish commands. Dedicated runtime evidence query remains marked `not_implemented`.

## Local Validation

- `python -m py_compile backend\app\api\lite.py backend\app\services\persona_builder_service.py backend\tests\test_persona_builder_contract.py`: pass.
- `python -m pytest -q backend\tests\test_persona_builder_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_persona_builder_template_api`: 2 passed, 4 existing warnings.
- `node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs`: 36 passed.
- `npm test`: 79 passed.
- `npm run build`: pass; existing LiveKit chunk-size warning remains.
- `npm run lint`: pass with existing 5 hook warnings.
- `git diff --check`: pass.
- Browser smoke: `http://127.0.0.1:5174/persona-builder` redirected to `/login` when unauthenticated; console error/warning count was 0.
