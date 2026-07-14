# Canonical Operator Console Consolidation Implementation Plan

> **For agentic workers:** execute this plan task-by-task on the single integration branch `work/744-canonical-operator-console-consolidation`. Do not create a competing implementation branch or a second task tracker. Each task ends with a testable commit and an exact-head verification record on #747.

**Goal:** Consolidate the current Operator Workspace, Support Console and legacy static admin frontend into one understandable, accessible and maintainable Nexus OSR operator console, then permanently remove duplicate product surfaces after parity evidence.

**Architecture:** `/workspace` remains the sole case-work spine and canonical queue consumer. Knowledge, channels, runtime and management become separate route domains inside one shared application shell. Shared transport, authorization/session handling, semantic tokens and React primitives become the only frontend authorities; transitional surfaces are migrated capability-by-capability and deleted only after their consumers and tests have moved.

**Tech Stack:** React 18, TypeScript 5, Vite 8, TanStack Router, TanStack Query, Radix primitives, Playwright, Node test runner, FastAPI backend contracts.

## Global constraints

- Authority: #747, parent #744, product/design contracts in `webapp/PRODUCT.md`, `webapp/DESIGN.md`, `webapp/design/frontend-product-foundation.v1.json`.
- Baseline: `main@4bc76c3607db2732388d33634bc26968a880ee07`.
- Branch: `work/744-canonical-operator-console-consolidation`.
- No direct writes to `main`; final merge requires current-main reconciliation, exact-head CI and squash merge.
- No production deployment, production-data mutation, live Provider enablement or real customer outbound.
- Backend authorization, confirmation tokens, idempotency, policy and audit remain final.
- No big-bang rewrite. Each capability moves with characterization tests, parity evidence, caller migration and retirement in one coherent sequence.
- UI language is operational and plain. Do not expose Tenant keys, model names, Runtime traces, Job IDs or implementation terminology in the primary operator hierarchy.
- No marketing hero, staged-number story, decorative gradient/glass styling or generic AI dashboard grammar.
- WCAG AA, 44×44 primary/touch targets, visible focus, reduced-motion behavior and non-color-only status are release requirements.
- One business capability must end with one route, one feature owner, one server-owned state contract, one domain adapter and one shared component vocabulary.

---

## Task 1: Establish the migration ledger and anti-expansion boundary

**Files:**
- Create: `webapp/design/operator-console-consolidation.v1.json`
- Create: `webapp/tests/operator-console-consolidation-contract.test.mjs`
- Modify: `docs/engineering/frontend-product-foundation.md`
- Reference: `config/governance/legacy-surface-domains.v1.json`

**Produces:** A machine-readable list of canonical, transitional and legacy operator surfaces, route ownership, capability dispositions, exit conditions and forbidden new authorities.

- [ ] Write a contract test that requires exactly one canonical operator route (`/workspace`) and records `/webchat` as transitional only.
- [ ] Require target supporting routes `/knowledge`, `/channels`, `/runtime`, `/control-tower` in the migration contract.
- [ ] Record `operator-workspace` as `CANONICAL`; Support Console conversation UI and `frontend/` as `LEGACY_ACTIVE_MIGRATE_THEN_DELETE`.
- [ ] Record the three existing API transport implementations and the required final `single_transport_authority` disposition.
- [ ] Add a route-file allowlist covering current and target canonical routes so a new unowned product route fails the Node contract test.
- [ ] Record deletion prerequisites: consumer inventory, parity, keyboard/degraded evidence, production build identity and rollback.
- [ ] Run `cd webapp && node --test tests/operator-console-consolidation-contract.test.mjs`.
- [ ] Run `cd webapp && npm test`.
- [ ] Commit as `test(frontend): freeze canonical console authority`.

## Task 2: Simplify the operator login and remove AI/marketing presentation

**Files:**
- Modify: `webapp/src/routes/login.tsx`
- Modify: `webapp/src/styles/auth.css`
- Modify: `webapp/e2e/login-semantic.spec.ts`
- Modify: `webapp/e2e/smoke.spec.ts`

**Produces:** A direct, accessible login screen that identifies the system, explains account-derived access and gets the operator into work without promotional storytelling.

- [ ] Replace the numbered `事实 / 受控动作 / 安全结案` presentation with a factual system-use panel.
- [ ] Use the visible product name `Nexus OSR` and operator-facing name `客服与运营工作台`.
- [ ] State that queues, countries, channels and actions load from the signed-in account; do not ask the operator to understand configuration keys.
- [ ] Keep one semantic form, visible labels, password reveal, bounded error message, error focus and Enter submission.
- [ ] Keep the session-storage boundary copy concise and place account-support guidance outside the primary form flow.
- [ ] Verify 375px no-overflow and minimum control height.
- [ ] Run `cd webapp && npm run typecheck`.
- [ ] Run `cd webapp && npx playwright test e2e/login-semantic.spec.ts e2e/smoke.spec.ts --grep "login|unauthenticated"`.
- [ ] Commit as `feat(frontend): simplify operator login experience`.

## Task 3: Build one application shell and canonical navigation

**Files:**
- Create: `webapp/src/app/AppShell.tsx`
- Create: `webapp/src/app/AppNavigation.tsx`
- Create: `webapp/src/app/app-shell.css`
- Create: `webapp/src/app/navigation.ts`
- Modify: `webapp/src/routes/workspace.tsx`
- Modify: `webapp/src/router.tsx`
- Modify: `webapp/src/main.tsx`
- Test: `webapp/tests/canonical-navigation-contract.test.mjs`
- Test: `webapp/e2e/canonical-navigation.spec.ts`

**Produces:** One header, one product identity, one capability-derived navigation system and predictable route behavior.

- [ ] Define route metadata for Workspace, Knowledge, Channels, Runtime and Control Tower with capability predicates.
- [ ] Render navigation through TanStack Router links rather than raw `<a href>` transitions.
- [ ] Keep the signed-in user and logout in one consistent location.
- [ ] Preserve draft-leave guards through a route-blocking interface rather than feature-specific browser history manipulation.
- [ ] Ensure unknown routes fail closed to `/workspace` or `/login` without implying the old WebChat product still exists.
- [ ] Verify keyboard order: skip link, product navigation, page heading, primary task.
- [ ] Commit as `feat(frontend): establish canonical application shell`.

## Task 4: Replace manual scope keys with server-authorized scope

**Files:**
- Modify: `webapp/src/lib/types.ts`
- Modify: `webapp/src/hooks/useAuth.ts`
- Create: `webapp/src/features/scope/AuthorizedScopeSwitcher.tsx`
- Create: `webapp/src/features/scope/authorized-scope.css`
- Modify: `webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx`
- Modify: `webapp/src/lib/operatorWorkspaceApi.ts`
- Backend contract owner: #546/#547/#571; consume accepted response rather than inventing local authority.
- Test: `webapp/e2e/operator-scope.spec.ts`

**Produces:** Normal operators see their allowed work scope and optional permitted choices, never free-text Tenant/country/channel configuration.

- [ ] Characterize current `X-Nexus-Tenant`, country and channel request behavior.
- [ ] Extend the authenticated session/read model only through the accepted backend scope contract.
- [ ] Remove free-text Tenant and channel key fields from normal operator UI.
- [ ] Provide a labelled select only when more than one server-authorized scope is returned.
- [ ] Fail closed with a clear support message when no authorized operational scope exists.
- [ ] Do not persist secrets or unbounded scope objects in browser storage.
- [ ] Commit as `feat(frontend): bind workspace to authorized scope`.

## Task 5: Decompose and complete the canonical Workspace

**Files:**
- Refactor: `webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx`
- Create: `webapp/src/features/operator-workspace/queue/QueuePane.tsx`
- Create: `webapp/src/features/operator-workspace/case/CaseHeader.tsx`
- Create: `webapp/src/features/operator-workspace/case/CaseSpine.tsx`
- Create: `webapp/src/features/operator-workspace/evidence/EvidencePanel.tsx`
- Create: `webapp/src/features/operator-workspace/conversation/ConversationPanel.tsx`
- Create: `webapp/src/features/operator-workspace/actions/ActionPanel.tsx`
- Create: `webapp/src/features/operator-workspace/outcomes/OutcomeTimeline.tsx`
- Create: `webapp/src/features/operator-workspace/lifecycle/LifecyclePanel.tsx`
- Modify: `webapp/src/lib/operatorWorkspaceTypes.ts`
- Modify: `webapp/src/lib/operatorWorkspacePresentation.ts`
- Test: `webapp/tests/operator-workspace-contract.test.mjs`
- Test: `webapp/e2e/operator-workspace.spec.ts`

**Produces:** One queue-driven case workspace covering Handoff, Ticket and Dispatch without WebChat-only assumptions.

- [ ] Keep `GET /api/admin/operator-queue/unified` as the sole queue truth.
- [ ] Preserve cursor pagination and stop any unbounded list read.
- [ ] Render authoritative evidence, stale/unavailable/contradictory evidence, customer claims, approved knowledge, AI assistance, human decision, system event, action receipt and notification receipt as distinct types.
- [ ] Replace string-scanning Case Spine inference with server-provided stage and blocker data when #525/#587/#526 contracts are accepted.
- [ ] Keep Ticket-only and Dispatch-only cases operable without conversation records.
- [ ] Present one primary next action and explicit disabled reason.
- [ ] Preserve unsent reply draft across refresh and require confirmation before destructive navigation.
- [ ] Present closure blocked, observation, eligible to close, safely closed, repair required and reopened only from durable backend state.
- [ ] Verify slow/unavailable/stale/conflict states and large queue/timeline behavior.
- [ ] Commit in focused vertical commits, ending with `feat(frontend): complete canonical case workspace`.

## Task 6: Extract Knowledge into `/knowledge`

**Files:**
- Create: `webapp/src/routes/knowledge.tsx`
- Create: `webapp/src/features/knowledge/KnowledgePage.tsx`
- Split from: `webapp/src/features/support-console/SupportConsolePage.tsx`
- Create: `webapp/src/features/knowledge/knowledge.css`
- Modify: `webapp/src/router.tsx`
- Test: `webapp/e2e/knowledge.spec.ts`

**Produces:** Knowledge list, editing, review, publication, retrieval testing and synchronization evidence inside the canonical shell.

- [ ] Move behavior without changing API semantics.
- [ ] Preserve unsaved-draft guards and publish confirmation.
- [ ] Paginate or bound the current 200-item read.
- [ ] Use plain knowledge/SOP language; keep Runtime synchronization detail secondary.
- [ ] Remove the Knowledge tab and its state from Support Console after route parity.
- [ ] Commit as `feat(frontend): move knowledge into canonical route`.

## Task 7: Extract Channels into `/channels`

**Files:**
- Create: `webapp/src/routes/channels.tsx`
- Create: `webapp/src/features/channels/ChannelsPage.tsx`
- Split from: `webapp/src/features/support-console/SupportConsolePage.tsx`
- Create: `webapp/src/features/channels/channels.css`
- Modify: `webapp/src/router.tsx`
- Test: `webapp/e2e/channels.spec.ts`

**Produces:** One bounded channel/account setup and health surface, separated from case access.

- [ ] Show active accounts, operational health, reconnect/attention state and last confirmed update.
- [ ] Do not grant or imply case access from channel-management permission.
- [ ] Remove the Channels tab from Support Console after parity.
- [ ] Commit as `feat(frontend): move channel management into canonical route`.

## Task 8: Extract Runtime and audit into `/runtime`

**Files:**
- Create: `webapp/src/routes/runtime.tsx`
- Create: `webapp/src/features/runtime/RuntimePage.tsx`
- Split from: `webapp/src/features/support-console/SupportConsolePage.tsx`
- Create: `webapp/src/features/runtime/runtime.css`
- Modify: `webapp/src/router.tsx`
- Test: `webapp/e2e/runtime.spec.ts`

**Produces:** Bounded technical readiness and diagnostics for authorized runtime/audit operators.

- [ ] Keep model names, latency, request shape, fallback and traces out of Workspace primary content.
- [ ] Distinguish unavailable, degraded, warning and ready states without false green success.
- [ ] Remove the Runtime tab from Support Console after parity.
- [ ] Commit as `feat(frontend): move runtime diagnostics into canonical route`.

## Task 9: Build `/control-tower` from existing management evidence

**Files:**
- Create: `webapp/src/routes/control-tower.tsx`
- Create: `webapp/src/features/control-tower/ControlTowerPage.tsx`
- Reuse typed APIs from: `webapp/src/lib/api.ts` during transport migration
- Create: `webapp/src/features/control-tower/control-tower.css`
- Test: `webapp/e2e/control-tower.spec.ts`

**Produces:** Tenant-scoped workload, unowned work, SLA risk, repair-required cases, outcome quality and drill-down to the canonical Workspace.

- [ ] Use management evidence only; never create a second task or case truth.
- [ ] Every row drills into `/workspace?queue=<canonical queue id>`.
- [ ] Avoid hero-metric dashboard templates; prioritize actionable workload and risk.
- [ ] Commit as `feat(frontend): add canonical operations control tower`.

## Task 10: Consolidate API transport, auth and error behavior

**Files:**
- Create: `webapp/src/lib/http/authToken.ts`
- Create: `webapp/src/lib/http/httpClient.ts`
- Create: `webapp/src/lib/http/errors.ts`
- Create: `webapp/src/lib/http/telemetry.ts`
- Modify domain adapters: `webapp/src/lib/api.ts`, `webapp/src/lib/supportApi.ts`, `webapp/src/lib/operatorWorkspaceApi.ts`
- Delete after callers migrate: duplicate request/auth/error implementations in those files
- Test: `webapp/tests/http-client-contract.test.mjs`

**Produces:** One implementation of token storage, request ID, URL normalization, timeout, safe GET retry, auth expiry, error normalization and latency telemetry.

- [ ] Characterize differences before consolidation, especially retry and 401 behavior.
- [ ] Keep domain-specific typed methods but route every request through `httpClient`.
- [ ] Ensure one auth-expiry event clears the session once and routes to Login without loops.
- [ ] Preserve AbortSignal behavior and avoid duplicate listeners.
- [ ] Commit as `refactor(frontend): establish one HTTP transport authority`.

## Task 11: Converge tokens, primitives and feature styles

**Files:**
- Authority: `webapp/src/styles/tokens.css`
- Authority: `webapp/src/components/ui/`
- Modify: `webapp/src/styles.css`, `webapp/src/styles/components.css`, `webapp/src/styles/auth.css`
- Modify/migrate feature CSS under `webapp/src/features/**`
- Delete after migration: `webapp/src/features/support-console/support-console.css`
- Test: `webapp/tests/design-system-authority.test.mjs`
- Test: deterministic Playwright screenshots for normal/loading/empty/degraded/conflict/repair states

**Produces:** One semantic palette and one control/status vocabulary across all routes.

- [ ] Inventory raw colors, radii, shadows, buttons, fields, badges, tabs, alerts and tables.
- [ ] Map feature values to semantic tokens; allow narrow documented data-visualization exceptions only.
- [ ] Remove nested-card and repeated-eyebrow patterns that obscure hierarchy.
- [ ] Enforce 44px primary/touch controls, visible focus, contrast and reduced motion.
- [ ] Add a test that rejects new raw feature hex colors and private button/field/status systems.
- [ ] Commit as `refactor(frontend): converge on one design system`.

## Task 12: Retire Support Console conversation product spine

**Files:**
- Modify: `webapp/src/routes/webchat.tsx`
- Delete after parity: `webapp/src/features/support-console/SupportConsolePage.tsx`
- Delete after parity: `webapp/src/features/support-console/lazy.tsx`
- Delete after parity: `webapp/src/features/support-console/support-console.css`
- Update/delete tests: `webapp/tests/frontend-route-splitting-contract.test.mjs`, `webapp/e2e/route-splitting.spec.ts`, Support Console-specific smoke expectations

**Produces:** No competing conversation queue, reply composer, handoff controls, header or tab shell.

- [ ] Prove Workspace covers supported conversation selection, reply, takeover, release and resume behavior.
- [ ] Change `/webchat` to an explicit capability-safe redirect to `/workspace` or remove it after all generated links migrate.
- [ ] Ensure unknown old query parameters do not silently open unrelated administrative content.
- [ ] Add a negative test that `nexus-support-console` and Support Console source paths cannot return.
- [ ] Commit as `refactor(frontend): retire competing support console`.

## Task 13: Retire the static legacy frontend

**Files:**
- Delete after parity: `frontend/index.html`, `frontend/app.js`, `frontend/style.css` and all tracked files under `frontend/`
- Modify: `backend/app/settings.py`, `backend/app/main.py`, deployment and release scripts that still reference fallback behavior
- Modify/delete: legacy frontend tests and documentation
- Modify: `config/governance/legacy-surface-domains.v1.json` only through its owning governance acceptance
- Test: production build missing-modern-frontend negative tests

**Produces:** One production frontend source and fail-closed behavior when the modern build is absent.

- [ ] Complete route/capability parity matrix against the legacy static UI: overview, cases, bulletins, channel setup, signoff and management evidence.
- [ ] Migrate only supported useful capability; explicitly retire obsolete behavior.
- [ ] Prove every release profile builds `webapp/` from source and serves exact build identity.
- [ ] Prove no development, test or production profile silently falls back to `frontend/`.
- [ ] Delete the directory, consumers, tests and misleading deprecation docs in the same accepted slice.
- [ ] Commit as `refactor(frontend): permanently retire legacy static UI`.

## Task 14: Final browser, scale, accessibility and release acceptance

**Files:**
- Update: `webapp/e2e/*.spec.ts`
- Update: frontend Node contract tests
- Update: applicable backend route/static/release tests
- Update: #747 with exact-head evidence

**Produces:** One mergeable exact head with no duplicate UI authority.

- [ ] Run `cd webapp && npm test`.
- [ ] Run `cd webapp && npm run typecheck`.
- [ ] Run `cd webapp && npm run lint`.
- [ ] Run `cd webapp && npm run build`.
- [ ] Run the full Playwright suite at 375, 768, 1024 and 1440 representative layouts.
- [ ] Verify login, queue selection, evidence, ownership, controlled action, reply, closure/observation, Knowledge, Channels, Runtime and Control Tower.
- [ ] Verify keyboard-only completion, focus restoration, dialog behavior, bounded live regions, reduced motion and text enlargement.
- [ ] Verify representative large queues/timelines and slow/unavailable/stale/conflict/repair states.
- [ ] Verify no raw customer/provider identifiers leak into unsafe surfaces or artifacts.
- [ ] Reconcile branch with latest `main`; rerun exact-head CI and release-image identity checks.
- [ ] Confirm removed paths have no imports, routes, dynamic registrations, build/deploy consumers or generated Hrefs.
- [ ] Confirm architecture gates reject a second frontend, second operator product spine, second transport and second design-system authority.
- [ ] Open one Draft PR only when the branch contains a coherent review boundary; keep it Draft until all acceptance evidence passes.
- [ ] Squash merge only when mergeable, current with `main`, required checks green and #747 acceptance is reconciled.

## Rollback

All frontend-only slices roll back through normal Git reversion. Backend response-contract changes require backward-compatible rollout until the canonical frontend is accepted. No source deletion is merged before the preceding parity commit is independently restorable and the release profile fails closed without the deleted implementation.
