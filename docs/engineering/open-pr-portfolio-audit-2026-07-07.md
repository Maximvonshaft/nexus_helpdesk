# Nexus Open PR Portfolio Audit

Generated: 2026-07-07

Main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

## Executive Decision

This document turns the accepted open PR portfolio audit into a repository-managed cleanup plan. It is a management document only. It does not merge, close, deploy, tag, or approve any PR.

### Current merge strategy

The current merge order is now:

1. **Baseline CI Stabilization**
2. **#440** — after Baseline CI Stabilization, #440 is the first merge candidate.
3. **#439A** — docs/audit/runbook extraction from #439.
4. **#439B** — metrics wiring extraction from #439.
5. **#439C** — baseline CI / smoke contract extraction from #439.
6. **T1 Tracking** — replacement for #395.
7. **K1/K2 Knowledge** — replacements/extractions for #353/#323 where still valid.
8. **W1-W4 WhatsApp** — split only from #438 source material.
9. **Frontend / Email / WebCall** — explicitly post-release cleanup lanes.

### Corrected handoff notice rule

- `origin=handoff_notice` is forbidden.
- `v3 reply.type=handoff_notice` is not forbidden.
- Runtime-generated `handoff_notice` is allowed when it has a valid `provider_runtime` / `ai_runtime` origin and passes the customer-visible message contract.

### Hard do-not-merge boundaries

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
| 1 | Baseline CI Stabilization | required first | Main CI must be stable before any PR enters merge consideration. | backend-ci, backend-full-regression, Frontend CI, webapp-build, round-a-smoke, production-readiness, postgres-migration green |
| 2 | #440 | first merge candidate after baseline stabilization | P0 active-content attachment download hardening. | Focused test, security review, all required CI green |
| 3 | #439A | replacement PR | Docs/audit/runbook split from #439. | Markdown/doc diff only; no runtime behavior change |
| 4 | #439B | replacement PR | Metrics wiring split from #439. | Metrics tests; no WebChat UI or WhatsApp sidecar changes |
| 5 | #439C | replacement PR | Baseline CI / smoke contract split from #439. | Smoke contract and workflow impact reviewed separately |
| 6 | T1 Tracking | replacement PR for #395 | Tracking primary truth and enrichment contract. | `speedaf_hybrid` allow-list, primary source tests, v3 `used_sources` / tool-source tests |
| 7 | K1 Knowledge | replacement extraction for #353 | Runtime/readiness fixes only. | No old migration direct merge; current-main extraction |
| 8 | K2 Knowledge | replacement extraction for #323/#353 | Knowledge quality/golden/conflict tests. | Schema-compatible tests only |
| 9 | W1-W4 WhatsApp | split from #438 only | #438 remains draft source material. #414 is superseded. | Outbound contract, admin status, sidecar lifecycle, UI/docs split; no deployment to 34 |
| 10 | Frontend / Email / WebCall | deferred | Post-release cleanup lanes. | Only after security/release/tracking/knowledge/WhatsApp split queue stabilizes |

## PR Inventory

| PR | Title | Category | Risk | Decision | Reason | Next Action |
|---:|---|---|---|---|---|---|
| #440 | fix: harden active-content attachment downloads | security | P0 | needs security review | Security hardening is narrow, but only first after Baseline CI Stabilization. | Fix baseline CI, rerun focused attachment test, security review. |
| #439 | chore: freeze visible message contract milestone and add production audits | customer-visible contract / observability | P1 | split required | Do not continue monolith repair. | Split into #439A docs/audit/runbook, #439B metrics wiring, #439C baseline CI/smoke contract. |
| #438 | Harden native WhatsApp channel candidate | WhatsApp | P1 | split required | Keep draft as source material only. Too broad for direct merge. | Create W1-W4 current-main split PRs. |
| #422 | Add production drift audit for 178.105.160.174 | drift/deploy/docs | low | keep as design reference | Historical drift report is useful but stale as current truth. | Refresh audit before operational use. |
| #414 | add whatsapp webchat channel bridge | WhatsApp / WebChat | P1 | close / superseded | Old outbound bypass risk and stale bridge path. | Do not merge; replace only through W1 outbound contract if needed. |
| #413 | govern provider runtime defaults | customer-visible contract | P1 | needs product decision | Provider defaults/deploy changes are governance-sensitive. | Product/release decision before extraction. |
| #395 | add Speedaf hybrid tracking source | Tracking/Tool | P1 | replace with T1 Tracking Contract PR | Valuable truth-source concept, stale branch. | Build T1 from current main. |
| #376 | frontend production closure IA RBAC E2E | Frontend IA/RBAC | P2 | split required | Draft and too broad. Required target validation was not completed. | Split route map, lookup route, state views, E2E support. |
| #353 | harden Knowledge Runtime v2 production deployment | Knowledge/RAG | P1 | replace with K1/K2 extraction | Old migration and compose changes block direct merge. | K1 runtime/readiness, K2 quality/golden tests. |
| #337 | webcall operator workbench and handoff capability gating | WebCall/Voice mixed | P1 | split required | Mega aggregate across WebCall, Email, RAG, Persona, QA, Bulletin and migrations. | Archive as source material or split into clean WebCall-only slices. |
| #336 | Security & Audit lens | security/audit | P2 | superseded by main | Overlaps current #439 split direction. | Extract missing assertions only. |
| #335 | Bulletin impact audit preview | observability/audit | P1 | needs product decision | Bulletin can affect AI/customer context. | Product decision before revival. |
| #334 | Email mailbox polling daemon | Email | P1 | needs product decision | Email daemon/inbound requires product and security decision. | Decide Email roadmap before recreating. |
| #333 | Email mailbox queue projection | Email | P2 | superseded by main | Old stacked Email queue slice. | Archive or extract tests only. |
| #332 | Email delivery receipts | Email | P2 | superseded by main | Old migration/timeline/UI slice. | Recreate only if approved. |
| #331 | Email inbound ingest sync | Email | P1 | superseded by main | Old migration/timeline/UI slice. | Recreate only after Email decision. |
| #330 | WebCall session action commands | WebCall/Voice | P2 | extract small patch only | Useful command ledger concept, old migration/stale branch. | Recreate clean WebCall action-command PR if approved. |
| #329 | WebCall transcript evidence API | WebCall/Voice | P2 | extract small patch only | Useful read-only evidence concept, stale branch. | Recreate clean evidence-read PR if missing. |
| #328 | WebCall call note write path | WebCall/Voice | P2 | extract small patch only | Useful notes concept, stale branch. | Recreate clean call-note PR if approved. |
| #327 | Control Tower governance actions | Frontend IA/RBAC | P2 | needs product decision | Manager governance action model is product/platform scope. | Hold. |
| #326 | QA Training knowledge gap drafts | Knowledge/RAG | P2 | needs product decision | KB draft creation affects operations. | Hold for QA/KB workflow decision. |
| #325 | Persona runtime evidence endpoint | Knowledge/RAG | P2 | needs product decision | Depends on current RAG/persona architecture. | Hold. |
| #324 | QA Training agent appeals | Frontend IA/RBAC | P2 | superseded by main | Old QA template slice. | Archive unless revived. |
| #323 | Knowledge Studio conflict and golden tests | Knowledge/RAG | P2 | extract small patch only | Useful K2 test material. | Extract schema-compatible tests only. |
| #322 | Persona Builder approval workflow | Knowledge/RAG | P2 | extract small patch only | Old migration blocks direct merge. | Recreate only if approved. |
| #321 | Persona Builder template API | Knowledge/RAG | P2 | superseded by main | Superseded by later persona/RAG work. | Archive. |
| #320 | Knowledge Studio template API | Knowledge/RAG | P2 | superseded by main | Superseded by later Knowledge/RAG work. | Archive. |
| #319 | Harden WebCall workbench thread events | WebCall/Voice | P2 | superseded by main | Superseded by later WebCall iterations. | Archive. |
| #318 | QA training template API | Frontend IA/RBAC | P2 | superseded by main | Old template read-model. | Archive. |
| #317 | Control Tower template API | Frontend IA/RBAC | P2 | superseded by main | Old template slice. | Archive. |
| #316 | Today Workbench template API | Frontend IA/RBAC | P2 | superseded by main | Old template slice. | Archive. |
| #315-#309 | Email mailbox/thread/template stack | Email | P2 | superseded by main | Old stacked Email lane. | Archive or recreate current-main minimal PRs only after Email decision. |
| #308 | WebChat v1.7.8 template block parity | WebChat | P1 | superseded by main | Stale customer reply surface. | Archive. |
| #307-#284 | Older frontend/WebCall/observability/product workbench PRs | mixed | P2 | superseded by main | Duplicate or stale template iterations. | Archive unless a current-main extraction is approved. |
| #283/#295 | Realtime Health workbench variants | WebChat | P2 | superseded by main | Duplicate/stale realtime line. | Archive. |
| #281 | Refine workbench navigation groups | Frontend IA/RBAC | low | superseded by main | Old nav grouping likely covered by later work. | Archive. |
| #278 | WebChat public poll origin handling | WebChat | P1 | extract small patch only | Historically useful but stale. | Verify main; extract if regression remains. |
| #276/#272 | WebChat demo/handoff hotfixes | WebChat | P1 | superseded by main | Old hotfixes likely covered by later governance. | Archive after main verification. |
| #263/#234 | Email account registry / Email production pack | Email | P1 | close/archive candidate | Old broad Email implementation stack. | Archive; no direct merge. |
| #246/#238 | AGENTS / Chatwoot docs | drift/deploy/docs | low | keep as design reference | Useful design/process references, not release merge candidates. | Review separately or archive. |
| #239 | OpenClaw Codex auth reference | security/vendor | P1 | needs security review | Vendor submodule/reference has license and supply-chain implications. | Security/license review required. |
| #186 | Speedaf UAT smoke report sanitizer | security | P0 | extract small patch only | Privacy sanitizer may still be useful but stale. | Verify main; recreate tiny patch if absent. |
| #159-#134 | Old Codex provider/runtime stack | customer-visible/provider runtime | P1/P2 | keep as design reference | Current phase forbids new provider and provider routing changes. | Keep as reference only. |

## Close / Supersede Candidates

| PR | Why superseded or obsolete | Safe action |
|---:|---|---|
| #414 | Old WhatsApp bridge with outbound bypass risk. | Close/supersede after human approval; replace only via W1 if needed. |
| #234 | Huge old Email production pack superseded by smaller staged lane. | Close/archive. |
| #337 | Mega aggregate across many lanes. | Split or archive; no direct merge. |
| #336/#286/#298 | Security/audit workbench variants superseded by #439 split. | Extract assertions only if missing. |
| #321/#320/#288/#287 | Old Persona/Knowledge Studio UI/template line. | Archive or hold for product decision. |
| #308/#300/#278/#276/#272 | Old WebChat template/hotfix line. | Verify main; extract only real missing regressions. |
| #315-#309/#334-#331/#263 | Old Email lane. | Archive unless Email roadmap is approved. |
| #307-#284 | Duplicate/stale WebCall/workbench/observability iterations. | Archive unless current-main extraction is approved. |
| #159-#134 | Old Codex runtime/provider stack. | Keep as design reference only. |

## Split Required

| PR | Proposed replacement PRs | Scope to extract |
|---:|---|---|
| #439 | #439A / #439B / #439C | docs-audit-runbook / metrics wiring / baseline CI and smoke contract |
| #438 | W1 / W2 / W3 / W4 | outbound contract / admin pairing-status / sidecar lifecycle / UI and candidate docs |
| #376 | Frontend-A/B/C/D | route permissions / lookup route / state views / E2E support |
| #337 | WebCall-A/B/C only if revived | notes / evidence / session actions; do not revive the aggregate |

## Extract Small Patch Only

| Source | Replacement | Scope |
|---:|---|---|
| #395 | T1 Tracking Contract PR | `speedaf_hybrid`, primary truth source, v3 `used_sources`, tests |
| #353 | K1 Knowledge runtime extraction | Runtime/readiness fixes only; no old migration direct merge |
| #323 | K2 Knowledge quality tests | Conflict/golden tests only if schema-compatible |
| #186 | Security sanitizer patch | Speedaf UAT smoke report sanitizer if absent from main |
| #278 | WebChat poll-origin patch | Extract only if regression remains on current main |
| #328/#329/#330 | WebCall clean slices | Notes, transcript/evidence, action commands if approved |

## Security Priority

| PR | Security issue | Merge condition |
|---:|---|---|
| #440 | Active-content attachment download hardening | Baseline CI stable, focused test green, security review complete |
| #186 | Speedaf report sanitizer | Verify gap on main, extract tiny patch, focused regression |
| #239 | OpenClaw vendor/reference | Security/license/supply-chain review |
| #149/#148 | Codex auth/login discovery | Keep as reference unless a new reviewed runtime plan exists |

## Business Capability Lane

### WhatsApp

- #438 remains draft source material only.
- #414 is close/supersede because of old outbound bypass risk.
- W1-W4 replacement sequence: outbound contract, admin pairing/status, sidecar lifecycle, operator UI/candidate docs.
- No deployment to 34 and no direct sidecar cutover.

### Tracking

- #395 is replaced by T1 Tracking Contract PR.
- `/mcp/order/query` remains primary current-status truth.
- `/express/track/query` is enrichment only.
- T1 must test v3 `used_sources` and tool-source contracts.

### RAG / Knowledge

- #353 is replaced by K1/K2 extraction.
- No old migration direct merge.
- K2 should extract only schema-compatible conflict/golden tests.

### Frontend

- #376 is split required.
- Frontend work is post-release unless needed for baseline CI/smoke stabilization.

### Email

- #234 is close/archive candidate.
- #331-#334 and #309-#315 require Email product/security decision before any revival.
- No old Email migration direct merge.

### WebCall

- #337 is source material only.
- #328/#329/#330 can inform clean WebCall-only replacement PRs.
- WebCall is post-release behind security, release, tracking, knowledge and WhatsApp split lanes.

## 30-Day Cleanup Plan

### Week 1

- Stabilize baseline CI.
- Prepare #440 security review.
- Split #439 into #439A/#439B/#439C.
- Mark #438/#414/#337/#353/#234 as do-not-merge-directly in the cleanup plan.

### Week 2

- Create T1 Tracking Contract PR.
- Create K1 Knowledge runtime/readiness extraction.
- Create K2 Knowledge quality/golden/conflict test extraction.
- Verify #186 sanitizer gap.

### Week 3

- Keep #438 as source material.
- Build W1-W4 from current main only.
- Close/supersede #414 after human approval.

### Week 4

- Process frontend, Email and WebCall lanes.
- Archive stale duplicates using standard templates.
- Do not comment or close PRs without maintainer approval.

## PR Comment Templates

Copy-ready templates are stored in `docs/engineering/pr-cleanup-comment-templates.md`.

## Machine-readable Plan

The companion JSON cleanup plan is stored in `docs/engineering/open-pr-cleanup-plan-2026-07-07.json`.
