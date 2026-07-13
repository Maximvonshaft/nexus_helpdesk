# Nexus OSR Operator Workspace Overhaul — Implementation Plan

## Authority

- Work Item: #525
- Owner-authorized frontend-first slice: #525 comment `4952519415`
- Original implementation base: `main@528309deed01f246568867e69cdbd235026cfc61`
- Latest merge-candidate base verified during delivery: `main@9ae6e9f6aa3742e8576dbe7270a6f17d691dc312`
- Product authority: `webapp/PRODUCT.md`
- Design authority: `webapp/DESIGN.md`
- Machine authority: `webapp/design/frontend-product-foundation.v1.json`
- Queue truth: #524 / `GET /api/admin/operator-queue/unified`
- Transitional source: `/webchat`

## Goal

Replace the WebChat-first mental model with one understandable operator workflow:

`understand → find task → act safely → backend effect → durable receipt/outcome → understandable feedback → next action or explicit blocked state`

This slice must organize existing server truth. It must not fabricate Tenant ownership, factual evidence, business-result reconciliation, lifecycle closure, Provider acknowledgement, or production readiness that upstream contracts do not yet provide.

## Ten coordinated lanes

| Lane | Primary ownership | Acceptance signal |
|---|---|---|
| 1. Route and navigation | `routes/workspace.tsx`, router/index/root | `/workspace` is canonical; `/webchat` is compatibility |
| 2. Unified queue | `operatorWorkspaceApi.ts`, queue components | Handoff/Ticket/Dispatch are first-class queue rows |
| 3. Scope gate | Workspace state and API headers | Tenant/country/channel are explicit and fail closed |
| 4. Case identity | case header/context | Source, risk, owner, SLA and retry are understandable |
| 5. Case Spine | `CaseSpine` | Supported stages are visible; unsupported closure truth is blocked |
| 6. Evidence taxonomy | evidence components | Fact, claim, Knowledge, AI, event, outcome and notification differ |
| 7. Governed actions | action panel | Existing permissions, prerequisites and disabled reasons are explicit |
| 8. Outcome feedback | receipt timeline | Queued, technical, operational, notified and business-result states differ |
| 9. Conversation and responsive | conversation/mobile navigation | Delivery state is visible; context/actions are never silently hidden |
| 10. Verification | Node contracts, Playwright, CI | Exact-head tests, typecheck, lint, build and browser evidence |

## Information architecture

### Application shell

- Product title and explicit current scope.
- Capability-derived navigation:
  - Workspace: `operator_queue.read` or `ticket.read`.
  - Knowledge: `ai_config.read` or `ai_config.manage`.
  - Channels: `channel_account.manage`.
  - Runtime: `runtime.manage`.
- System administration is separated from primary case work.

### Workspace desktop

1. **Scoped queue rail**
   - scope editor;
   - state/source/owner/SLA/sort controls;
   - HMAC cursor paging;
   - queue row: source, priority, owner, SLA, retry, reopened and source status;
   - priority and retry are fully visible in task state; dedicated priority/retry filter controls are intentionally left for a bounded follow-up because they are not required for the action/outcome loop to function.
2. **Case work surface**
   - case identity and source-safe summary;
   - Case Spine;
   - current task and conversation where available;
   - customer communication with delivery receipt.
3. **Context rail**
   - facts and evidence classes;
   - governed action and prerequisites;
   - action/outbound receipt timeline;
   - closure blocker;
   - technical detail disclosure.

### Tablet/mobile

- Four explicit views: `队列`, `案例`, `沟通`, `动作`.
- Selecting a case opens `案例`.
- No context or action surface is hidden without a reachable navigation control.
- Use `100dvh`; no horizontal page scrolling.

## Truth mapping

### Queue/source

- `terminal` and Ticket `resolved/closed` are source states only.
- `reopened` is separately visible.
- SLA `unavailable`, `stale`, `paused`, `at_risk`, and `breached` stay distinct.
- Retry `pending`, `processing`, `retry_scheduled`, `exhausted`, and `settled` stay distinct.

### Evidence

Existing `support_memory.evidence_timeline` is classified conservatively:

- Tool/Tracking success with current source evidence → `事实与依据`.
- Customer messages → `客户主张`.
- Knowledge source → `知识与政策`.
- AI turn/history → `AI 建议/历史`.
- Human decision or handoff → `人工决定`.
- Outbound → `客户通知回执`.
- Ticket/WebChat events → `系统事件`.

Unknown evidence remains `系统记录`; it is never promoted to authoritative fact.

### Action and outcome

- API acceptance/Job enqueue → `请求已接受` or `请求已排队`.
- Job/provider technical completion → `技术处理完成`.
- Explicit `operational_completed` → `运营已完成`.
- Explicit delivery/notification receipt → `已通知客户`.
- Explicit `business_result_confirmed` → `业务结果已确认`.
- Error, exhausted retry, contradictory evidence → `需要修复`.
- Missing #587/#526 evidence → `尚不能判定业务完成/安全结案`.

## Existing action integration

Only existing governed server actions are integrated:

- accept/force takeover;
- decline/release;
- resume AI;
- customer reply through Support Conversation reply boundary;
- Speedaf phone lookup, work order, address update and cancel preview/confirm.

Frontend checks are usability hints only. Backend authorization remains final. Every disabled action must expose a bounded reason.

The existing read-state value is shown when supplied by the backend. A manual mark-unread toggle is not claimed as part of this delivery.

## Test-first sequence

1. Add `webapp/tests/operator-workspace-contract.test.mjs` before implementation.
2. Witness RED because `/workspace`, unified queue client and Workspace components do not exist.
3. Implement route, types, API, layout and truth presentation.
4. Add browser cases for scope gate, queue source types, delivery states, responsive navigation and keyboard order.
5. Run exact-head test, typecheck, lint, build and Playwright workflows.
6. Perform specification-compliance and code-quality/design-system reviews.
7. Reconcile latest main and merge only with expected-head acceptance.

## Rollback

Rollback is a normal revert of the frontend PR. No migration, backend schema, Provider call, deployment, production data change or external mutation is introduced.
