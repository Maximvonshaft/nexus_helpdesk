# Canonical Administration Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one production-grade account and administration control plane inside the existing Nexus OSR application, with self-service password change, server-authoritative user/role/team/capability governance, security audit visibility, and immediate invalidation of stale sessions.

**Architecture:** Extend the current FastAPI authentication and `/api/admin` authorities; do not create a second backend or duplicate user-management implementation. Extend the canonical TanStack Router registry, existing AppShell, MUI theme, `apiClient.ts`, and `supportApi.ts`; do not add another shell, transport, theme, or CSS authority. JWT `iat` and canonical `User.updated_at` form the single session-revocation rule for both HTTP and agent WebSocket authentication.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, PyJWT, Argon2, React 18, TypeScript, TanStack Router/Query, Material UI, pytest, Node test runner, Playwright, canonical GitHub Actions acceptance.

## Global Constraints

- Base commit: `54f1ed8de13dc5ee347ff53f30c88c3d9d1ef38d`.
- Work only on `feat/admin-control-plane-20260720`.
- Preserve `webapp/src/routes/` as the only route registry.
- Preserve `webapp/src/lib/apiClient.ts` as the only generic HTTP transport.
- Preserve Material UI, one Nexus theme, one AppShell, and `OperatorPresentation` as the only generic visual authorities.
- Reuse existing `/api/admin/users`, capability, security-audit, lookup, and audit services; no parallel admin implementation.
- No database migration or second session store: session freshness is derived from JWT `iat` and `User.updated_at`.
- Passwords and tokens must never appear in audit payloads, errors, or UI logs.
- Every write remains backend-authorized and durably audited.

---

### Task 1: Canonical session freshness and self-service password change

**Files:**
- Modify: `backend/app/auth_service.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/auth.py`
- Modify: `backend/app/api/webchat_ws.py`
- Test: `backend/tests/test_identity_control_plane.py`

**Interfaces:**
- Produces: `AccessTokenClaims`, `decode_access_token_claims(token)`, `access_token_is_current(user, claims)`, `load_current_user_for_token(db, token)`.
- Produces: `POST /api/auth/change-password` with `{current_password,new_password}` and `{"ok":true,"reauthenticate":true}`.

- [ ] Add tests proving a valid token authenticates, an updated user invalidates the old token for HTTP and WebSocket user loading, wrong current password is rejected, password policy is enforced, and successful password change revokes the old token.
- [ ] Implement typed JWT claim decoding while retaining `decode_access_token(token) -> Optional[int]` compatibility.
- [ ] Centralize database-backed token freshness in `load_current_user_for_token`; consume it from HTTP and agent WebSocket authentication.
- [ ] Implement password change with current-password verification, the existing password policy, Argon2 hashing, `auth.password_changed` audit, and no secret-bearing audit data.
- [ ] Run `cd backend && pytest -q tests/test_identity_control_plane.py` and require zero failures.

### Task 2: Server-authoritative identity governance projection

**Files:**
- Create: `backend/app/api/admin_identity.py`
- Modify: `backend/app/bootstrap/routers.py`
- Test: `backend/tests/test_identity_control_plane.py`

**Interfaces:**
- Produces: `GET /api/admin/identity/roles`, returning every `UserRole` and its canonical default capabilities from `ROLE_CAPABILITIES`.
- Produces: `GET /api/admin/identity/teams`, `POST /api/admin/identity/teams`, `PATCH /api/admin/identity/teams/{team_id}`.
- Produces: `DELETE /api/admin/identity/users/{user_id}/team`, the explicit inverse of existing team assignment.

- [ ] Add failing tests for admin-only role projection, team create/update/deactivate, unique team names, valid active market binding, protected deactivation of teams with active users, and explicit user-team clearing.
- [ ] Implement the router by reusing `ensure_can_manage_users`, `ensure_can_manage_markets`, `ROLE_CAPABILITIES`, `managed_session`, and `log_admin_audit`.
- [ ] Register the router exactly once before the general admin router without changing existing route ownership.
- [ ] Run the identity control-plane test file and require zero failures.

### Task 3: Canonical frontend API and type contracts

**Files:**
- Modify: `webapp/src/lib/types/core.ts`
- Modify: `webapp/src/lib/supportApi.ts`
- Test: `webapp/tests/administration-control-plane-contract.test.mjs`

**Interfaces:**
- Produces: typed admin user page, role catalog, team governance, user create/update/activate/deactivate/reset-password, security audit, and self password-change calls.
- Consumes only `apiRequest` from the existing transport authority.

- [ ] Add a static contract test that rejects raw `fetch`, a second API client, hard-coded role capability policy, or duplicate admin endpoints.
- [ ] Add the minimum required TypeScript interfaces to the existing core type authority.
- [ ] Add typed `supportApi` methods for the existing and newly added canonical endpoints.
- [ ] Run `cd webapp && npm test -- --test-name-pattern=administration` and require zero failures.

### Task 4: Account and administration product surfaces

**Files:**
- Create: `webapp/src/routes/account.tsx`
- Create: `webapp/src/routes/administration.tsx`
- Create: `webapp/src/features/account/AccountPage.tsx`
- Create: `webapp/src/features/account/lazy.ts`
- Create: `webapp/src/features/administration/AdministrationPage.tsx`
- Create: `webapp/src/features/administration/lazy.ts`
- Modify: `webapp/src/router.tsx`
- Modify: `webapp/src/app/navigation.ts`
- Modify: `webapp/src/app/AppShell.tsx`
- Test: `webapp/tests/administration-control-plane-contract.test.mjs`

**Interfaces:**
- `/account`: available to every authenticated operator; displays identity and changes password, then clears the session and returns to login.
- `/administration`: available when the backend grants `user.manage`, `security.read`, or `audit.read`; mutation controls require `user.manage`.

- [ ] Add contract tests for route registration, capability gating, lazy route splitting, AppShell account access, no second shell/navigation, and MUI-only presentation.
- [ ] Implement AccountPage with current identity and a protected password-change form.
- [ ] Implement AdministrationPage with user listing/search, create/edit, role/team/capability assignment, activate/deactivate, password reset, team governance, and read-only security audit for auditors.
- [ ] Add only `系统管理` to primary navigation; expose `账户设置` from the existing AppShell identity area.
- [ ] Run frontend architecture, lint, typecheck, tests, build, and route-splitting checks.

### Task 5: Product authority, residue prevention, and security review

**Files:**
- Modify: `webapp/PRODUCT.md`
- Modify: `webapp/tests/canonical-shell-contract.test.mjs`
- Create: `backend/tests/test_identity_control_plane.py`
- Create: `webapp/tests/administration-control-plane-contract.test.mjs`

- [ ] Update the product register so Account and Administration are canonical supporting domains, not a second product.
- [ ] Assert one route registry, one navigation authority, one HTTP transport, one shell, no route CSS, and no hard-coded duplicate permission policy.
- [ ] Review changed Python and TypeScript using SecPriv source-to-sink analysis: authorization, token freshness, password handling, audit redaction, IDOR, XSS, PII exposure, and retention.
- [ ] Resolve every finding with confidence at least 0.8 before opening the PR.

### Task 6: End-to-end verification and delivery

- [ ] Run `cd backend && pytest -q tests/test_identity_control_plane.py tests/test_security_audit_contract.py tests/test_admin_users_pagination.py`.
- [ ] Run `cd webapp && npm run verify`.
- [ ] Open one pull request from `feat/admin-control-plane-20260720` to `main`.
- [ ] Run the repository's sole canonical acceptance workflow and inspect every failed job/log if applicable.
- [ ] Confirm the final diff contains no duplicate route, API client, shell, theme, user-management service, session store, obsolete file, placeholder, or unreachable implementation.
- [ ] Report exact commit, PR, changed-file inventory, test counts, workflow run, unresolved limitations, and merge readiness.