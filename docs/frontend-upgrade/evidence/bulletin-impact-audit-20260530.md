# Bulletin Impact / Audit Evidence

Date: 2026-05-30
Branch: `codex/bulletin-impact-audit`

## Scope

- Promote the v1.7.8 Bulletin Center from read/edit UI into a contract-backed operations page.
- Keep `/bulletins` readable for authenticated operators while preserving `bulletin.manage` for writes.
- Add audited create/update writes for `market_bulletins`.
- Add `POST /api/admin/bulletins/impact-preview` so managers can preview affected open tickets, channel distribution, sample tickets and AI context injection before saving a bulletin.
- Keep AppShell and CommandPalette entrypoints aligned with unified `routeAccess`.

## Contract Boundaries

- `/api/lookups/bulletins` remains the authenticated read path for operator-facing bulletin visibility.
- `/api/admin/bulletins` create/update remains `bulletin.manage` gated and now writes `AdminAuditLog` rows with normalized country/channel scope.
- `/api/admin/bulletins/impact-preview` is `bulletin.manage` gated and performs a read-only query over non-terminal tickets.
- The preview intentionally does not publish or mutate bulletin state.

## Local Validation

Actions are disabled for this stack; validation is local.

- `python -m compileall backend\app\api\admin.py backend\app\schemas.py backend\app\services\bulletin_service.py`: passed.
- `python -m pytest backend\tests\test_bulletin_center_contract.py -q`: 1 passed.
- `python -m pytest backend\tests\test_bulletin_center_contract.py backend\tests\test_round20a_rectification.py backend\tests\test_control_tower_contract.py -q`: 7 passed.
- `node --test tests\bulletin-center-contract.test.mjs tests\route-nav-consistency.test.mjs`: 12 passed.
- `npm test`: 88 passed.
- `npm run build`: passed with the existing `vendor-livekit` chunk-size warning.
- `npm run lint`: passed with 5 existing warnings and 0 errors.
- `git diff --check`: passed.
- Browser smoke against local FastAPI + `frontend_dist`: logged in as `admin/demo123`, opened `/bulletins`, clicked `预览影响工单`, observed `匹配工单`, `data-testid="bulletin-impact-preview"`, no no-access state, `POST /api/admin/bulletins/impact-preview` 200, and browser console errors/warnings `[]`. Browser screenshot capture timed out in the in-app Browser CDP path, so DOM, API, and console evidence are used for this smoke.
