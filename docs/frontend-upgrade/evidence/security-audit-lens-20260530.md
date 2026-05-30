# Security & Audit Lens Evidence - 2026-05-30

## Scope

- Added `security.read` and `audit.read` capabilities.
- Auditor role receives read-only access to `/security`.
- Added `GET /api/admin/security-audit` for capability catalog, user capability lens, recent AdminAuditLog entries, redacted audit diffs, high-risk override counts and read-only mode.
- Added frontend `/security` route, AppShell navigation, CommandPalette action and centralized API client call.

## Local validation

- `python -m compileall backend\app\api\admin.py backend\app\schemas.py backend\app\services\permissions.py`: passed.
- `python -m pytest backend\tests\test_security_audit_contract.py -q`: passed, 1 test.
- `python -m pytest backend\tests\test_rbac_capability_contracts.py -q`: passed, 2 tests.
- `python -m pytest backend\tests\test_admin_users_pagination.py -q`: passed, 4 tests.
- `python -m pytest tests\test_production_hardening_permissions.py -q` from `backend/`: passed, 4 tests.
- `node --test tests\security-audit-contract.test.mjs tests\route-nav-consistency.test.mjs`: passed, 14 tests.
- `npm test`: passed, 93 tests.
- `npm run build`: passed.
- `npm run lint`: passed with 5 existing warnings in unrelated files.
- Browser smoke at `http://127.0.0.1:5187/security` against local backend `http://127.0.0.1:8015`: passed. The page rendered `security-audit-lens`, `security-capability-matrix`, and `security-recent-audit`; direct API verification returned 3 users, 1 recent audit entry, and `[redacted]` for password/token fields.
