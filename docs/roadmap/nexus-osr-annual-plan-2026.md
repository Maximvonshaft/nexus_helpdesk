# Nexus OSR Annual Plan 2026

Current authority baseline: `main` at `0ddebe8081ea2401b51fcb36c332b85d9d494d2b` (`feat(osr): add admin debug and control tower surfaces (#456)`).

This roadmap is the current-main control file for Nexus OSR delivery. It records only merged capability as mainline state. Open Draft PRs are listed with their exact integration verdict and must not be treated as landed. PR #450 remains strategic source material only.

Nexus OSR means Nexus Operations Service Runtime: a multi-country logistics customer-service and operations-closure runtime. It is not a chatbot and not a C-end long-term memory system.

## Immutable safety kernel

- No C-end long-term customer memory. Use short-lived, case-scoped Case Context only.
- MCP and approved operational systems are the highest authority for live facts.
- Customer claims are signals, not verified facts.
- Previous AI replies are never factual evidence.
- Customer-visible output must pass through `CustomerVisibleMessageService` or the governed outbound contract.
- AI tool execution must pass `ToolExecutionPolicy`, `PolicyGate`, `ControlledActionExecutor`, or the governed equivalent.
- Human online means existing handoff. Human offline means automatic ticket creation when escalation is required.
- Complaints, compensation, refunds, legal threats, personal-data requests, and other high-risk cases follow configurable policy.
- Country, language, channel, queue, tool, policy, WhatsApp routing, and group behavior remain configuration-driven.
- Raw prompts, provider payloads, tool arguments/results, tracking numbers, phone/email, credentials, and provider group IDs must not leak into unsafe surfaces.
- No production deploys, release tags, real external customer messages, funds/legal/identity actions, or irreversible deletion from this workflow.

## Product doctrine

Nexus OSR closes logistics cases safely through facts, policies, tickets, handoff, governed tools, durable operations dispatch, debug/eval/audit, Admin, and Control Tower surfaces. AI is not an unconstrained responder; it acts only inside configured and auditable boundaries.

## Current implementation state

Evidence date: 2026-07-10, from main, control Issue #461, portfolio Issue #467, manual batch `OSR-20260710-A`, and Work Orders #459/#466/#469-#474/#480.

| Area | Current state | Next control action |
| --- | --- | --- |
| Runtime and governed customer-visible boundary | Main contains WebChat runtime, Runtime Context Guard, TrackingFactResult, Knowledge models, CustomerVisibleMessageService, Tool Registry, Policy Gate, Handoff Service, and QA observability foundations. | Preserve the immutable boundary; no direct customer-visible bypass. |
| Runtime audit integration | PR #452 is merged. Final persistence-level sanitizer and complete model-drift registration remain blocked under #470. | Implement #470 before claiming full audit persistence safety. |
| Case Context lifecycle | Foundation is merged, but database-enforced active uniqueness and default exclusion of closed/expired contexts remain unimplemented under #469. | Implement #469 with PostgreSQL migration and data-remediation evidence. |
| Human hours, escalation, and auto-ticket | PR #453 is merged. PRs #468 and #479 were accepted on their reviewed heads but are still Draft and now require reconstruction on current main. | Reconstruct/validate #468 first, then #479. |
| Governed tool execution | PR #454 is merged as the M3 baseline. | Reopen only for verified regressions. |
| WhatsApp operations routing | PR #464 is merged; original #455 is closed unmerged as superseded. | Preserve configuration-driven, no-sidecar/no-customer-send behavior. |
| Durable operations dispatch | PR #477 has a sound outbox core and green reviewed-head checks, but is `REPAIR`: its model is not loaded by Alembic metadata/drift tooling and provider/runtime contention evidence remains incomplete. | Complete #470/model registration and repair #477 before release gate. |
| Admin, Debug, and Control Tower | PR #456 merged as `0ddebe8081...` after exact-head permission, tenant, redaction, migration, regression, and smoke gates. | Treat as M8 baseline. Existing policy tables remain explicitly global configuration by current schema. |
| Tracking truth contract | PR #475 is `ACCEPT` at reviewed head but must be reconstructed on current main and revalidated. | Reconstruct and run exact-head checks before merge. |
| Knowledge data safety | PR #476 is `ACCEPT` at reviewed head but must be reconstructed on current main and revalidated. | Merge #476 before dependent #478. |
| Knowledge readiness | PR #478 is `ACCEPT` at reviewed head and depends on #476. | Reconstruct only after #476 merges, then rerun exact-head checks. |
| Production readiness | Green PR CI is not production proof. PostgreSQL concurrency, provider adapter idempotency, retention, restore, load, alerting, rollback, and failure-injection evidence remain incomplete. | M12 remains `NO_GO`. |

## Manual batch integration verdict

### Merged

- PR #456 — Admin / Debug / Control Tower.
  - validated head: `930243ae2700f362b07f0d74fd396b03dd28dfc0`
  - merge commit: `0ddebe8081ea2401b51fcb36c332b85d9d494d2b`
  - Work Order #466: completed.

### ACCEPT, still Draft, current-main reconstruction required

- PR #468 — ticket identifier, transaction safety, reused-ticket operator projection. Must precede #479.
- PR #479 — configured escalation patterns and fail-closed orchestration entry. Depends on #468.
- PR #475 — primary tracking truth contract.
- PR #476 — Knowledge vector/backfill/retrieval data safety. Must precede #478.
- PR #478 — fail-closed Knowledge readiness. Depends on #476.

These verdicts accept the reviewed code and reviewed-head evidence. They do not authorize merging from the old base after main advanced.

### REPAIR

- PR #477 — durable Operations Dispatch Outbox.
  - migration: `20260710_0056 -> 20260709_0054`
  - blockers: outbox model registration in Alembic/drift tooling, #470 completion, PostgreSQL contention proof, and a separately governed provider adapter/idempotency contract before external dispatch is enabled.

### BLOCK

- Work Order #469 — no implementation PR for active Case Context uniqueness/lifecycle.
- Work Order #470 — no implementation PR for final audit sanitizer and complete model-migration drift coverage.

## 2026 capability map

### M1 — Runtime decision and Case Context foundation

Status: landed with P1 lifecycle and final-audit follow-up blocked under #469/#470.

### M2 — Handoff, auto-ticket, and offline closure

Status: accepted Drafts #468/#479 require ordered current-main reconstruction and validation.

### M3 — Governed tool execution

Status: merged mainline baseline through #454.

### M4 — MCP truth contract and tracking authority

Status: PR #475 accepted at reviewed head; reconstruction and exact-head gate required. Old PRs #395/#446 remain reference-only.

### M5 — Customer-visible Knowledge quality

Status: PRs #476/#478 accepted at reviewed heads; ordered reconstruction required. Knowledge must never answer live parcel status.

### M6 — WhatsApp operations routing

Status: merged mainline baseline through #464; #455 is superseded.

### M7 — Debug, eval, and regression gate

Status: Admin/debug baseline landed through #456; further eval hardening remains future work.

### M8 — Admin policy APIs and Control Tower

Status: merged mainline baseline through #456 at `0ddebe8081...`.

### M9 — Operations workspace and queues

Status: durable outbox PR #477 requires repair; complete operator evidence-to-closure journey remains future work.

### M10 — Operations analytics and Control Tower maturity

Status: future.

### M11 — Internal SOP SkillBank

Status: future.

### M12 — Production hardening and readiness

Status: `NO_GO`. Unit/CI success is not equivalent to runtime production proof.

## Active dependency order

1. Keep #469 and #470 open until implementation PRs provide PostgreSQL lifecycle, final sanitizer, model registration, migration-drift, and rollback evidence.
2. Reconstruct PR #468 on current main `0ddebe8081...`; run focused and exact-head checks; merge only when green.
3. After #468 merges, reconstruct and validate PR #479 on the resulting main.
4. Reconstruct and validate PR #475 independently on the then-current main.
5. Reconstruct and validate PR #476; after it merges, reconstruct and validate dependent PR #478.
6. Repair #477 with full model registration and PostgreSQL contention evidence; keep real external dispatch disabled until a separate provider-adapter contract is approved and verified.
7. Reconcile both roadmap files after every accepted merge.
8. Keep M12 at `NO_GO` until runtime load, concurrency, restore, retention, failure-injection, observability, rollback, and incident evidence is complete.

## Swarm and release policy

- Product-code changes require a narrow `[OSR Work Order]`.
- The first external write requires a valid Claim in #461 and a read-back/conflict check.
- One coherent action per development session.
- Development sessions may run focused tests and create Draft PRs, but Supervisor controls Ready, full release gate, merge, and post-merge acceptance.
- Merge one PR at a time; reread main and recalculate every downstream base after each merge.
- A green old-base head is not merge authority after main advances.
- If safe current-main reconstruction is unavailable, keep the PR Draft and record the exact blocker rather than forcing or fabricating a rebase.

## Stale and reference PR guidance

- PR #450: strategic annual-plan source only; superseded by merged roadmap control files.
- PR #455: closed unmerged; superseded by merged PR #464.
- PRs #395 and #446: reference-only; current T1 implementation is PR #475 once reconstructed and merged.
- No old WhatsApp/native, Tracking, or Knowledge PR may be revived without a current-main Work Order, exact-head review, and conflict check.
