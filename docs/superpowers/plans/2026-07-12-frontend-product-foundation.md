# Nexus OSR Frontend Product Foundation — Implementation Plan

> **Work Item:** #613  
> **Design:** `docs/superpowers/specs/2026-07-12-frontend-product-foundation-design.md`  
> **Execution:** Superpowers RED → GREEN → REFACTOR, then specification and code-quality review

## Goal

Establish an additive, machine-verifiable product/design authority for the Nexus OSR operator frontend without changing production routes, components, CSS behavior, API calls, database state or external systems.

## Non-goals

- No frontend visual refactor.
- No `/workspace` implementation.
- No runtime state or backend API changes.
- No migration.
- No Provider, outbound, deployment or production-data effects.

## Task 1 — Commit the approved design specification

**Create**

- `docs/superpowers/specs/2026-07-12-frontend-product-foundation-design.md`

**Verification**

- Confirms product thesis, users, IA, visual direction, signature, design-system target, state language, accessibility and anti-patterns.
- Explicitly maps implementation to #525/#564/#573.

## Task 2 — Write the contract test first (RED)

**Create**

- `webapp/tests/frontend-product-foundation-contract.test.mjs`

The test must require files that do not yet exist:

- `webapp/PRODUCT.md`
- `webapp/DESIGN.md`
- `webapp/design/frontend-product-foundation.v1.json`
- `docs/engineering/frontend-product-foundation.md`

**Test requirements**

1. Required files exist.
2. JSON uses schema `nexus.frontend-product-foundation.v1`.
3. Canonical route domains include `/workspace`, `/knowledge`, `/channels`, `/runtime`, `/control-tower` and `/login`.
4. `/webchat` is transitional, not canonical.
5. Semantic token and component authorities point to existing target paths.
6. State vocabulary distinguishes source, evidence, action, outcome and closure.
7. Quality floor requires contrast 4.5, target size 44 and reduced motion.
8. Prohibited language includes technical-status-as-closure and long-term-memory terminology.
9. PRODUCT and DESIGN documents contain Nexus-specific product/design commitments.
10. Engineering integration document assigns implementation to #525/#564/#573.

**RED verification**

```bash
cd webapp
node --test tests/frontend-product-foundation-contract.test.mjs
```

Expected: failure because the required authority files do not exist.

## Task 3 — Open one Draft PR and capture RED evidence

**Create Draft PR**

- Work Item: #613
- Current branch only
- Describe expected RED and additive safety boundary

**Verify**

- The dedicated test fails for missing product/design authority, not syntax or unrelated errors.
- Do not claim completion.

## Task 4 — Implement the minimum product/design authority (GREEN)

**Create**

- `webapp/PRODUCT.md`
- `webapp/DESIGN.md`
- `webapp/design/frontend-product-foundation.v1.json`
- `docs/engineering/frontend-product-foundation.md`

### PRODUCT.md

Define:

- product identity and job;
- users/roles;
- canonical journey;
- route domains and capability rules;
- product vocabulary;
- prohibited terminology and false-success semantics;
- non-goals and no-long-term-memory rule.

### DESIGN.md

Define:

- physical operating context;
- dense-calm cockpit thesis;
- Case Spine signature;
- semantic palette;
- typography, spacing, radius, elevation and motion;
- accessibility floor;
- anti-patterns and migration direction.

### Machine-readable contract

Define exact bounded fields for:

- schema/version/owner/lifecycle;
- product job and visual thesis;
- route domains;
- token/component authorities;
- state vocabulary;
- terminology rules;
- quality floor;
- downstream Work Items.

### Engineering guide

Document:

- current split authority inventory;
- staged migration order;
- ownership boundaries for #525/#564/#573;
- architecture-gate expectations;
- rollback and release boundary.

**GREEN verification**

```bash
cd webapp
node --test tests/frontend-product-foundation-contract.test.mjs
npm test
npm run typecheck
npm run build
```

## Task 5 — Refactor contract quality without widening scope

- Remove duplicated prose where the JSON contract is authoritative.
- Keep JSON bounded and deterministic.
- Ensure all referenced paths exist or are declared future routes rather than current routes.
- Ensure the contract never claims current runtime implementation.
- Run `git diff --check`.

## Task 6 — Specification-compliance review

Review the exact head against the design specification:

- Nexus-specific, not generic SaaS guidance;
- one justified signature element;
- no fourth design system;
- route IA separates operator/admin/runtime/management jobs;
- state language aligns with #587/#526;
- no runtime behavior added;
- all acceptance criteria in #613 are addressed by the slice.

Any specification blocker returns the PR to Draft/changes-required.

## Task 7 — Code-quality and design-system review

Review:

- JSON schema consistency and stable keys;
- test strength and failure quality;
- documentation clarity;
- accessibility floor;
- migration feasibility;
- no contradictory token/component authority;
- no unsafe or misleading business-state claim.

## Task 8 — Exact-head verification and delivery

Before Ready:

1. Re-read latest main and compare branch resources.
2. Re-run focused and full webapp checks on exact head.
3. Inspect all required GitHub Actions.
4. Verify no unresolved Review Thread.
5. Publish `AGENT_DELIVERY` on #613 with exact head and evidence.
6. Mark Ready only when all gates pass.

Do not merge without explicit accepted merge authority and expected head SHA.