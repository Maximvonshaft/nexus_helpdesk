# Nexus Open PR Portfolio Audit

Generated: 2026-07-07
Updated: 2026-07-08

Main governance baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`
Current main after release stabilization lane: `8d05475f4c7ba73a367129ec7fdb63b83a242fd4`

## Completed Since This Audit Was Drafted

- Baseline CI Stabilization landed as #441 / `790afe0b915bf56dc83e88a9e9ad4cd939ce6b9e`.
- Active-content attachment download hardening landed as #440 / `521ae3f5b7247ab6d44d1765565b59e3dc3a9bdd`.
- PR Portfolio Governance docs landed as #442 / `8d05475f4c7ba73a367129ec7fdb63b83a242fd4`.
- Phase 1 cleanup close pass completed: 20 PRs closed, 0 skipped.

## Executive Decision

This document turns the accepted open PR portfolio audit into a repository-managed cleanup plan. It is a management document only. It does not deploy, tag, approve, or close additional PRs by itself.

Old PRs remain historical source material by default. Current-main replacement PRs are required for any business capability.

## Current Next Merge Order

1. **T1 Tracking Contract PR.**
2. **K1 Knowledge pgvector/backfill safety.**
3. **K2 Knowledge readiness provider config.**
4. **#439A** docs/audit/runbook extraction, only if still needed after #442.
5. **#439B** metrics wiring.
6. **W1-W4 WhatsApp split PRs.**
7. **Frontend / Email / WebCall later.**

## Corrected Handoff Notice Rule

- `origin=handoff_notice` is forbidden.
- `v3 reply.type=handoff_notice` is not forbidden.
- Runtime-generated `handoff_notice` is allowed when it has a valid `provider_runtime` / `ai_runtime` origin and passes the customer-visible message contract.

## Hard Do-not-merge Boundaries

- Do not merge large feature PRs directly.
- Do not merge old migration PRs directly.
- Do not merge stale stacked PRs directly.
- Do not merge any PR that bypasses CustomerVisibleMessageService / outbound contract.
- Do not merge canned reply or hardcoded customer-visible text without the customer-visible contract.
- Do not mix WhatsApp sidecar changes with WebChat UI, runtime fallback, or deployment changes.
- Do not continue repairing #439 as a monolith.

## Merge Queue

| Order | Item | Decision | Reason | Required Checks |
|---:|---|---|---|---|
| 1 | T1 Tracking Contract PR | next active PR | Live tracking answers must use tool facts and primary source evidence. | `speedaf_hybrid` allow-list, primary truth source tests, v3 `used_sources` / tool-source tests |
| 2 | K1 Knowledge pgvector/backfill safety | next knowledge hardening PR | Keep Knowledge Runtime type/backfill behavior safe without reviving #353. | pgvector/backfill tests, dry-run script, compileall |
| 3 | K2 Knowledge readiness provider config | next knowledge readiness PR | Readiness probe must reflect configured embedding provider/model/dim/base URL. | readiness/provider tests, compileall |
| 4 | #439A | optional replacement PR | Docs/audit/runbook extraction only if still needed after #442. | docs/audit/script-only scope; no runtime behavior unless explicitly approved |
| 5 | #439B | replacement PR | Metrics wiring split from #439. | Metrics emit tests; no runtime behavior change from metric emission |
| 6 | W1-W4 WhatsApp | split required | #438 remains draft source material only; #414 is superseded. | Sidecar lifecycle/admin/outbound-contract/runbook split; no deployment to 34 |
| 7 | Frontend / Email / WebCall | deferred | Post-release cleanup lanes. | Only after tracking, knowledge, and WhatsApp split queue stabilizes |

## PR Inventory Updates

| PR | Title | Category | Decision | Reason | Next Action |
|---:|---|---|---|---|---|
| #441 | test: baseline CI stabilization | release / CI | landed | Baseline CI Stabilization completed in `790afe0b915bf56dc83e88a9e9ad4cd939ce6b9e`. | none |
| #440 | fix: harden active-content attachment downloads | security | landed | Active-content attachment hardening completed in `521ae3f5b7247ab6d44d1765565b59e3dc3a9bdd`. | none |
| #442 | docs: add open PR portfolio cleanup plan | docs / governance | landed | PR portfolio governance docs and Phase 1 ledger completed in `8d05475f4c7ba73a367129ec7fdb63b83a242fd4`. | none |
| #439 | chore: freeze visible message contract milestone and add production audits | customer-visible contract / observability | split required | Do not continue monolith repair. | Split only into #439A docs/audit/runbook and #439B metrics wiring if still needed. |
| #439C | baseline CI / smoke contract extraction from #439 | release / CI | superseded by #441 | #441 now covers baseline CI / probe / public smoke stabilization. | Do not create unless a new CI/smoke gap appears. |
| #438 | Harden native WhatsApp channel candidate | WhatsApp | split required | Keep draft as source material only. | Create W1-W4 current-main split PRs. |
| #414 | add whatsapp webchat channel bridge | WhatsApp / WebChat | closed / superseded | Old outbound bridge was closed in Phase 1 cleanup. | Do not reopen; replace only through W1-W4 if needed. |
| #395 | add Speedaf hybrid tracking source | Tracking / Tool | replace with T1 | Valuable source-truth concept, stale branch. | Build T1 from current main. |
| #353 | harden Knowledge Runtime v2 production deployment | Knowledge / RAG | replace with K1/K2 | Old migration and compose changes block direct merge. | Create K1/K2 from current main only. |

## 30-Day Cleanup Plan

### Week 1 — Completed

- Baseline CI stabilized.
- Attachment hardening landed.
- Phase 1 cleanup completed.
- PR portfolio docs landed.

### Week 2 — Next Active Lane

- Create T1 Tracking Contract PR from current main.
- Keep tracking service structured; do not generate customer-visible natural language.
- Prove primary current status source and v3 tool-source grounding.

### Week 3 — Knowledge Lane

- Create K1 Knowledge pgvector/backfill safety PR.
- Create K2 Knowledge readiness provider config PR.
- Do not revive #353 directly and do not bring old migrations into main.

### Week 4 — WhatsApp and Deferred Lanes

- Keep #438 as source material.
- Build W1-W4 from current main only.
- Keep Frontend / Email / WebCall as deferred cleanup lanes unless a release-blocking regression appears.
- Do not start Phase 2 PR cleanup until main stays stable after #441/#440/#442.

## Security Priority

| Item | Security issue | Status |
|---|---|---|
| #440 | Active-content attachment download hardening | Landed in `521ae3f5b7247ab6d44d1765565b59e3dc3a9bdd` |
| #186 | Speedaf report sanitizer | Verify gap on main before any current-main extraction |
| #239 | Retired vendor/reference | Security/license/supply-chain review required before any action |

## Business Capability Lane

### Tracking

- #395 is replaced by T1 Tracking Contract PR.
- `/mcp/order/query` remains primary current-status truth.
- `/express/track/query` is enrichment only.
- T1 must test v3 `used_sources` and tool-source contracts.
- Tracking services must return structured facts only, not customer-visible natural language.

### RAG / Knowledge

- #353 is replaced by K1/K2 extraction.
- No old migration direct merge.
- K1 handles pgvector/backfill type safety.
- K2 handles readiness probe provider config alignment.

### WhatsApp

- #438 remains draft source material only.
- #414 is closed / superseded.
- W1-W4 replacement sequence: sidecar session hardening, backend admin status/pairing API, outbound contract alignment, smoke/runbook.
- No deployment to 34 and no direct sidecar cutover.

### Frontend / Email / WebCall

- Post-release cleanup lanes.
- Do not revive old aggregate PRs directly.
- Recreate only small current-main PRs with focused tests and clear rollback boundaries.

## PR Comment Templates

Copy-ready templates are stored in `docs/engineering/pr-cleanup-comment-templates.md`.

## Machine-readable Plan

The companion JSON cleanup plan is stored in `docs/engineering/open-pr-cleanup-plan-2026-07-07.json`.