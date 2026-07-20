# Canonical Administration Control Plane Implementation Plan

> **For agentic workers:** Execute this plan only on the canonical branch and remove superseded work in the same delivery.

**Goal:** Deliver one production-grade account, identity, runtime-recovery and channel-governance control plane inside the existing Nexus OSR application, with password and MFA lifecycle management, stale-session revocation, server-authoritative user/role/team/capability governance, tenant isolation, security audit visibility, dead-record recovery, and SMTP/IMAP account administration.

**Architecture:** Extend current FastAPI and canonical frontend authorities; do not create a second user directory, admin product, channel product, Runtime product, transport, shell, theme, RBAC table, MFA store or session store. `User.updated_at` and the server capability fingerprint remain the only access-token freshness authorities for HTTP and agent WebSocket authentication. `user_credential_policies` stores password policy, bounded login metadata, encrypted TOTP state and hashed one-use recovery codes, but no access token, refresh token, device session, role, permission or tenant ownership.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic v2, PyJWT, Argon2, cryptography/Fernet, React 18, TypeScript, TanStack Router/Query, Material UI, pytest, Node test runner, Playwright, canonical GitHub Actions acceptance.

## Global constraints

- Work only on `feat/admin-control-plane-20260720` and one PR.
- Preserve `webapp/src/routes/` as the only route registry.
- Preserve `webapp/src/lib/apiClient.ts` as the only generic HTTP transport and `supportApi.ts` as the typed product API.
- Preserve Material UI, one Nexus theme, one AppShell and `OperatorPresentation` as the only generic visual authorities.
- Reuse existing `/api/admin/users`, audit, queue recovery, outbound-email and channel-onboarding authorities; no parallel CRUD or workflow.
- Preserve `User.updated_at` as the only mutable token-revocation version and the capability fingerprint as the only permission-freshness version.
- Derive tenant scope only from the authenticated server principal. The browser never owns tenant selection for authorization.
- Passwords, MFA secrets, MFA challenges, recovery codes and access tokens must never appear in audit payloads, errors or UI logs.
- Every mutation remains backend-authorized, tenant-scoped where applicable, rate-limited where existing policy requires it, and durably audited.

---

## Task 1: Canonical password and session lifecycle

- [x] Bind JWT freshness to exact `User.updated_at` and the server capability fingerprint.
- [x] Share the strict token loader between normal HTTP authorization and agent WebSocket authentication.
- [x] Separate recovery authentication from normal application authorization.
- [x] Force new administrator-issued accounts and administrator password resets to rotate credentials.
- [x] Block business APIs and agent WebSockets while rotation is required; allow only bounded recovery endpoints and `/account`.
- [x] Implement audited self-service password change and logout-all using existing password and identity authorities.
- [x] Backfill existing users as not requiring rotation while future issued credentials fail closed.
- [x] Add Alembic `20260720_0064` for the canonical credential-policy row.

## Task 2: Production MFA lifecycle

- [x] Add Alembic `20260720_0065` for encrypted TOTP state, hashed recovery codes, confirmation/verification timestamps and TOTP anti-replay step.
- [x] Add purpose-specific identity-MFA encryption through the existing `SecretCryptoService` authority.
- [x] Make correct password authentication produce only a five-minute MFA challenge for MFA-enabled users; no access token is issued before second-factor verification.
- [x] Support TOTP and one-use Argon2 recovery codes.
- [x] Reject repeated TOTP time steps and consume recovery codes atomically.
- [x] Implement account setup begin/cancel/confirm, status, recovery-code regeneration and MFA disable.
- [x] Display TOTP secret and recovery codes only once in the current UI state; never persist them in browser storage or logs.
- [x] Revoke HTTP and agent WebSocket sessions after MFA enable, disable, recovery-code regeneration or administrator reset.
- [x] Implement same-tenant administrator MFA reset, deny self-reset, and audit without secret material.
- [x] Cover full MFA lifecycle in backend and browser tests.

## Task 3: Server-authoritative identity and tenant governance

- [x] Keep `/api/admin/users` as the sole user CRUD authority.
- [x] Project role defaults directly from `ROLE_CAPABILITIES`.
- [x] Deliver tenant-stamped team create/update/deactivate and explicit user-team clearing.
- [x] Deliver credential/MFA policy projection, forced rotation, session revocation and MFA reset as explicit bounded commands.
- [x] Mount request-scoped identity policy on the canonical user Router so created users inherit actor tenant and the final active administrator cannot lose `user.manage`.
- [x] Apply server-derived tenant criteria to user pagination, users, teams, markets, capability overrides, security audit, channel accounts and outbound-email accounts.
- [x] Conceal cross-tenant resources and require tenant-bound email accounts to bind to an active same-tenant market.
- [x] Prevent tenant-bound email routing from falling back to an unowned global account.
- [x] Preserve existing last-admin, self-deactivation, password-policy and security-audit authorities.

## Task 4: Canonical account and administration surfaces

- [x] Keep `/account` as the only current-user password, MFA, recovery-code and session surface.
- [x] Fail closed and redirect forced-rotation users to `/account` before protected content or work-scope requests render.
- [x] Show identity, login/password metadata, password change, MFA setup/recovery and logout-all.
- [x] Keep `/administration` as the only user, credential, MFA recovery, role/capability, team and security-audit surface.
- [x] Separate user CRUD, credential/MFA/session actions, team lifecycle and audit into bounded panels inside one route.
- [x] Prevent the final active administrator from changing role or removing `user.manage` in both backend policy and UI guardrails.
- [x] Preserve one AppShell, one navigation authority, MUI-only presentation and lazy route splitting.

## Task 5: Runtime recovery control plane

- [x] Keep `/runtime` as the only runtime-health, evidence, queue-health and dead-record recovery surface.
- [x] Reuse existing `/api/admin/queues/summary`, dead-job requeue and dead-outbound requeue commands.
- [x] Require `runtime.manage` for mutation while allowing bounded read-only status.
- [x] Preserve existing rate limits, audit logging, idempotency and maximum batch size.
- [x] State explicitly that requeue is a technical reschedule, not business completion.
- [x] Cover background and outbound recovery journeys in Playwright and static contracts.

## Task 6: Email channel account governance

- [x] Keep `/channels` as the only channel onboarding, health and account-governance route.
- [x] Compose existing channel onboarding/WhatsApp behavior with SMTP/IMAP account governance in the same lazy route.
- [x] Reuse existing outbound-email CRUD, enable/disable and real test-send APIs.
- [x] Keep SMTP and IMAP credentials write-only, encrypted and masked on reads.
- [x] Validate same-tenant active market ownership on create and rebind.
- [x] Tenant-scope list, read, update, enable, disable and test-send account selection.
- [x] Preserve historical messages and audit after disablement.
- [x] Cover email test-send and disable journeys in Playwright and tenant routing in backend tests.

## Task 7: Authority, security and residue contracts

- [x] Keep `apiClient.ts` as the only generic transport and reject raw `fetch` in the new surfaces.
- [x] Reject duplicate route registries, shells, navigation authorities, themes, permission tables, session versions and parallel user/channel/runtime products.
- [x] Prove passwords, tokens, MFA secrets and recovery codes never enter audit payloads.
- [x] Prove tenant isolation for users, teams, markets, capability overrides, security audit, email administration and email routing.
- [x] Prove forced-rotation and MFA tokens are rejected by normal application/WebSocket authorization until recovery completes.
- [x] Remove superseded PR #778 implementation and all unused tenant/email helper modules and duplicate tests.
- [x] Update `webapp/PRODUCT.md` with final authorities and non-goals.

## Task 8: Exact-head verification and delivery

- [ ] Complete exact-head Canonical Acceptance with backend full regression, frontend architecture/lint/types/Node contracts/build/Playwright, PostgreSQL migration acceptance, static authority verification, production image assurance, secret/SAST/dependency checks and zero CodeQL findings.
- [ ] Confirm latest `main` is an ancestor of the exact candidate with zero behind commits.
- [ ] Resolve every review thread against the exact candidate.
- [ ] Update PR body with exact head, latest main, Run number/ID, test counts and authority conclusion.
- [ ] Merge only after the required gate passes.
- [ ] Verify merged `main` and final required workflow state.
