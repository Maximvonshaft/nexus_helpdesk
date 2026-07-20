# Canonical Administration Control Plane Implementation Plan

> **For agentic workers:** Execute this plan only on the canonical branch and remove superseded work in the same delivery.

**Goal:** Deliver one production-grade account and administration control plane inside the existing Nexus OSR application, with self-service password rotation, forced first-login/admin-reset rotation, global session revocation, server-authoritative user/role/team/capability governance, credential lifecycle governance, and security audit visibility.

**Architecture:** Extend the current FastAPI authentication and `/api/admin` authorities; do not create a second backend or duplicate user-management implementation. Extend the canonical TanStack Router registry, existing AppShell, MUI theme, `apiClient.ts`, and `supportApi.ts`; do not add another shell, transport, theme, or CSS authority. Canonical `User.updated_at` and the server capability fingerprint remain the sole token-freshness rule for HTTP and agent WebSocket authentication. `user_credential_policies` stores only password-rotation policy and bounded login metadata; it is not a session store and never owns token freshness.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic v2, PyJWT, Argon2, React 18, TypeScript, TanStack Router/Query, Material UI, pytest, Node test runner, Playwright, canonical GitHub Actions acceptance.

## Global Constraints

- Base commit: `827d577e3edcd403b0567d3113e77284334d8df1`.
- Work only on `feat/admin-control-plane-20260720`.
- Preserve `webapp/src/routes/` as the only route registry.
- Preserve `webapp/src/lib/apiClient.ts` as the only generic HTTP transport.
- Preserve Material UI, one Nexus theme, one AppShell, and `OperatorPresentation` as the only generic visual authorities.
- Reuse existing `/api/admin/users`, capability, security-audit, lookup, and audit services; no parallel user CRUD.
- Preserve `User.updated_at` as the only mutable token-revocation version and the capability fingerprint as the only permission-freshness version.
- Permit one credential-policy table only for `must_change_password`, password-change time, and last-login time; no token, device, refresh-token, or session-version persistence.
- Passwords and tokens must never appear in audit payloads, errors, or UI logs.
- Every write remains backend-authorized and durably audited.

---

### Task 1: Canonical token freshness and credential recovery

**Files:**
- `backend/app/auth_service.py`
- `backend/app/api/deps.py`
- `backend/app/api/auth.py`
- `backend/app/api/webchat_ws.py`
- `backend/app/models_identity_policy.py`
- `backend/app/services/credential_policy_service.py`
- `backend/alembic/versions/20260720_0064_user_credential_policy.py`

- [x] Bind JWT freshness to exact `User.updated_at` and the server capability fingerprint.
- [x] Share the strict token loader between normal HTTP authorization and agent WebSocket authentication.
- [x] Separate recovery authentication from normal application authorization.
- [x] Force new administrator-issued accounts and administrator password resets to rotate credentials.
- [x] Block business APIs and agent WebSockets while rotation is required; allow only login, `/auth/me`, password change, and logout-all recovery.
- [x] Implement audited self-service password change using the existing password policy and require reauthentication.
- [x] Implement audited self `logout-all` by advancing the existing user identity version.
- [x] Backfill existing users as not requiring rotation while making future issued credentials fail closed.

### Task 2: Server-authoritative identity and credential governance

**Files:**
- `backend/app/api/admin_identity.py`
- `backend/app/bootstrap/routers.py`
- `backend/app/api/admin_password_policy.py`
- `backend/app/services/credential_creation_context.py`

- [x] Project role defaults directly from `ROLE_CAPABILITIES`.
- [x] Deliver team create/update/deactivate and explicit user-team clearing without duplicating user CRUD.
- [x] Deliver a self-contained credential-policy projection for all users.
- [x] Deliver audited administrator commands to require password rotation and revoke all sessions.
- [x] Protect administrator-issued credential creation with an explicit request-scoped context instead of username or role heuristics.
- [x] Preserve existing last-admin, self-deactivation, password-policy, and security-audit authorities.

### Task 3: Canonical frontend API and type contracts

**Files:**
- `webapp/src/lib/types/core.ts`
- `webapp/src/lib/supportApi.ts`
- `webapp/tests/administration-control-plane-contract.test.mjs`

- [x] Keep `apiClient.ts` as the only generic transport and `supportApi.ts` as the typed product API.
- [x] Add typed user pagination, role policy, team governance, credential policy, password change, logout-all, force-rotation, and revoke-session contracts.
- [x] Reject raw `fetch`, duplicate API clients, hard-coded role capability tables, session-version clients, and duplicate identity routes through static contracts.

### Task 4: Account and administration product surfaces

**Files:**
- `webapp/src/routes/account.tsx`
- `webapp/src/routes/administration.tsx`
- `webapp/src/features/account/AccountPage.tsx`
- `webapp/src/features/administration/AdministrationPage.tsx`
- `webapp/src/features/administration/UserGovernance.tsx`
- `webapp/src/features/administration/CredentialGovernance.tsx`
- `webapp/src/features/administration/TeamGovernance.tsx`
- `webapp/src/features/administration/SecurityAuditPanel.tsx`
- `webapp/src/app/AuthenticatedAppPage.tsx`

- [x] Keep `/account` as the only current-user credential and session surface.
- [x] Fail closed and redirect forced-rotation users to `/account` before protected content renders.
- [x] Show current identity, last login, password-change time, forced-rotation warning, password change, and logout-all.
- [x] Keep `/administration` as the only user, credential, role, team, scope, and security-audit surface.
- [x] Separate user CRUD, credential/session actions, team lifecycle, and audit into bounded panels inside the same route.
- [x] Disable administrator credential actions against the current account; self-service remains under `/account`.
- [x] Preserve one AppShell, one primary navigation, MUI-only presentation, and lazy route splitting.

### Task 5: End-to-end verification and residue prevention

**Files:**
- `backend/tests/test_identity_control_plane.py`
- `backend/tests/test_admin_issued_credential_scope.py`
- `backend/tests/test_webchat_forced_rotation_auth.py`
- `webapp/e2e/identity-credential-lifecycle.spec.ts`
- `webapp/tests/administration-control-plane-contract.test.mjs`
- `webapp/PRODUCT.md`

- [x] Prove first-login recovery, admin-reset recovery, self password change, self logout-all, administrator force rotation, and administrator session revocation.
- [x] Prove forced-rotation tokens are accepted only by recovery authentication and rejected by the WebSocket/application loader.
- [x] Prove role/team/user governance and credential/session browser journeys.
- [x] Prove passwords and tokens never enter audit payloads.
- [x] Prove no second route registry, shell, navigation, transport, theme, RBAC table, user CRUD, session store, or unreachable frontend implementation exists.
- [ ] Complete exact-head Canonical Acceptance and record final backend, frontend, PostgreSQL, image, supply-chain, and CodeQL evidence.
- [ ] Update PR exact-head evidence, resolve all review findings, and merge only after the required gate passes.
