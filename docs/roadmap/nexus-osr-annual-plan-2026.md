# Nexus OSR Annual Plan 2026

Current authority baseline: `main` at `aed11916884bcf7ef0b2f7703589b6069c7a9a10` (`feat(osr): recover WhatsApp operations routing on current main (#464)`).

This roadmap is the current-main control file for Nexus OSR delivery. It supersedes stale prompt assumptions and uses PR #450 only as strategic source material because #450 was authored against the older `ec0af3fd8e853b447f47f901a69d036cae3d86e7` baseline.

Nexus OSR means Nexus Operations Service Runtime: a multi-country logistics customer-service and operations-closure runtime. It is not a chatbot and not a C-end long-term memory system.

## Immutable safety kernel

- No C-end long-term customer memory. Use short-lived, case-scoped Case Context only.
- MCP and approved operational systems are the highest authority for live facts.
- Customer claims are signals, not verified facts.
- Previous AI replies are never factual evidence.
- Customer-visible output must pass through `CustomerVisibleMessageService` or the current governed outbound contract.
- AI tool execution must pass `ToolExecutionPolicy`, `PolicyGate`, `ControlledActionExecutor`, or the current governed equivalent.
- Tool executors may produce safe summaries but must not send directly to customers.
- Human online means existing handoff. Human offline means automatic ticket creation when escalation is required.
- Complaints, compensation, refunds, legal threats, personal-data requests, and other high-risk cases must follow configurable escalation policy.
- Country, language, channel, queue, tool, policy, WhatsApp routing, and group behavior must be configuration-driven.
- Raw prompts, provider payloads, raw tool arguments, tracking numbers, phone numbers, email addresses, credentials, and provider group IDs must not leak into unsafe logs, customer messages, or general admin surfaces.
- No production deploys, release tags, real external customer messages, funds/legal actions, or irreversible deletion from this roadmap workflow.

## Product doctrine

Nexus OSR closes logistics cases safely through facts, policies, tickets, handoff, governed tools, WhatsApp routing, debug/eval/audit, and Control Tower surfaces. AI is not an unconstrained responder; it proposes and executes only governed operations inside configured boundaries.

## Current implementation state

Evidence date: 2026-07-10, from current main, control Issue #461, Work Orders #458/#459/#460/#463/#466, and current PR state.

| Area | Current state | Next control action |
| --- | --- | --- |
| WebChat runtime and governed customer-visible boundary | Main contains WebChat AI runtime, Runtime Context Guard, TrackingFactResult, KnowledgeItem/KnowledgeChunk, CustomerVisibleMessageService, Tool Registry, Policy Gate, Handoff Service, support memory ledger, and WebChat QA observability foundations. | Preserve boundaries; no customer-visible bypasses. |
| Runtime audit integration | PR #452 merged into main as `6261ecf0d81ccfef3a8790a4c7ca1d9f163e69f8`. | Treat as baseline. |
| Human hours and escalation orchestration | PR #453 merged into main as `57b1f89351df04b00e95b57db5aa1fe00aaacc6a`, default-off and using existing handoff/auto-ticket services. | Address post-merge P2 findings through Work Order #459 when non-conflicting. |
| Governed tool execution | PR #454 merged into main as `f5f4cd13d87a7766ca5fd5b43751979a326825c8` after Admin gate. | Treat as M3 baseline. Do not reopen unless a verified regression appears. |
| WhatsApp operations routing | PR #464 merged into main as `aed11916884bcf7ef0b2f7703589b6069c7a9a10`, from validated head `40437b21af12eda892584617b9a131e436ca55d6`. It adds the #455 recovery scope with no direct sidecar call and no customer-visible behavior change. Original PR #455 is now closed unmerged as superseded. | Treat as M6 baseline. Do not reopen #455 unless Admin explicitly decides otherwise. |
| Admin debug and Control Tower surfaces | PR #456 remains draft/queued last, old-base, mergeable=false. Known blocker: raw provider group ID detail-surface decision/fix. | Next backend workstream: rebase/refresh or recover #456 onto `aed11916884...`, resolve admin/debug conflicts, fix or explicitly authorize provider group ID detail exposure, then run focused admin/debug/control-tower tests. |
| Roadmap control files | PR #462 restored roadmap files; PR #465 is the active docs-only reconciliation branch after #464 merge and #455 superseded closure. | Admin can review/merge #465 after confirming facts remain current. |

## 2026 capability map

Month labels are sequencing hints, not rigid calendar commitments. Current SHA, dependencies, and control-plane claims determine execution order.

### M1 — Runtime decision and case-context foundation

Target outcome: all AI decisions are represented as governed runtime state, not ad hoc text generation.

Current status: runtime audit and human-hours/escalation foundations have landed on main. Post-merge P2 escalation findings remain under Work Order #459.

### M2 — Handoff, auto-ticket, and offline closure path

Target outcome: human handoff and offline auto-ticket closure work as production-safe runtime decisions.

Current status: main includes default-off escalation orchestration; Work Order #459 remains the follow-up holder.

### M3 — Governed tool execution baseline

Target outcome: OSR tool proposals can become policy-controlled operational actions without bypassing audit, idempotency, or customer-visible governance.

Current status: PR #454 is merged and is now the mainline M3 baseline.

### M4 — MCP truth contract and tracking authority

Target outcome: tracking/status answers clearly separate current status facts from history enrichment.

Current status: PR #395 and PR #446 are historical/reference material; do not revive or merge without current-main Work Order and conflict review.

### M5 — Customer-visible knowledge quality

Target outcome: knowledge answers are scoped, customer-visible, and safe for policy/service commitments.

Acceptance focus: knowledge never answers live tracking status; country/channel/audience filters are enforced; unsupported or stale answers become knowledge gap tasks.

### M6 — WhatsApp operations routing

Target outcome: OSR-created tickets/cases can route to the correct operations group through configuration without direct sidecar coupling.

Current status: landed through PR #464. Main now includes `backend/app/services/nexus_osr/whatsapp_routing_service.py`, focused routing tests, and the Agent 4 architecture note. Original PR #455 is closed unmerged as superseded by #464.

### M7 — Debug, eval, and regression gate

Target outcome: AI behavior is inspectable, replayable, and continuously regression-tested.

Current status: queued; should align with #456 admin/debug/control-tower work and later eval hardening.

### M8 — Admin policy APIs and Control Tower

Target outcome: operators can manage OSR safely through admin APIs and read-only observability surfaces.

Current status: PR #456 is the next backend/admin dependency after #464. It must be refreshed or recovered onto current main and boundary-reviewed before any Admin gate.

### M9 — Operations workspace and queues

Target outcome: human operators can close cases with evidence, SLA context, and safe action suggestions.

### M10 — Operations analytics and Control Tower maturity

Target outcome: operational leaders can measure AI closure, handoff, tickets, tool execution, routing, and knowledge quality.

### M11 — Internal SOP SkillBank

Target outcome: internal-only skills assist operators without becoming uncontrolled customer-facing behavior.

### M12 — Production hardening and readiness

Target outcome: OSR is production-ready for multi-country operations with rollback, privacy, load, and regression discipline.

## Active dependency order

1. Treat PR #454 as merged M3 baseline, PR #462 as merged roadmap-control baseline, and PR #464 as merged M6 WhatsApp routing baseline on main `aed11916884bcf7ef0b2f7703589b6069c7a9a10`.
2. Treat original PR #455 as closed unmerged and superseded by #464 unless Admin explicitly reopens that path.
3. Refresh/rebase or recover PR #456 onto current main, resolve admin/debug conflicts, and fix or explicitly authorize provider group ID detail exposure.
4. Run focused #456 checks before any Admin gate: `backend/tests/test_nexus_osr_admin_api.py`, plus persistence/runtime bridge/auto-ticket coverage as relevant.
5. Address Work Order #459 escalation follow-up on a current-main branch when it does not conflict with #456.
6. Keep roadmap files aligned through #465 or a successor docs-only reconciliation PR.
7. Continue M4/M5/M7+ only through current-main Work Orders and exact-head conflict checks.

## Swarm execution policy

- Product-code changes require an explicit `[OSR Work Order]` issue.
- Every external write must be preceded by a valid ACTION_CLAIM in the control issue unless creating the control issue itself.
- A claim must be read back and conflict-checked before execution.
- One coherent action per agent run.
- Full GitHub Actions, release locks, ready-for-review transitions, and merges are Admin/release-gate responsibilities.
- If no material non-conflicting action exists, agents should exit quietly.

## Stale PR guidance

- PR #450: strategic annual-plan source only; superseded by restored roadmap control files.
- PR #455: original WhatsApp routing PR on an old branch; closed unmerged as superseded by merged PR #464. Do not reopen unless Admin explicitly decides otherwise.
- PR #456: current next backend/admin dependency; must be refreshed or recovered and safety-reviewed last.
- PR #465: docs-only roadmap reconciliation branch; should reflect #464 merged, #455 closed as superseded, and #456 next.
- Older WhatsApp/native, tracking, and knowledge PRs must not be revived without current-main Work Order, exact-head review, and conflict check.
