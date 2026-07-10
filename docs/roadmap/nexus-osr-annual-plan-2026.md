# Nexus OSR Annual Plan 2026

Current authority baseline: `main` at `f5f4cd13d87a7766ca5fd5b43751979a326825c8` (`feat(osr): add governed tool execution path (#454)`).

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

Evidence date: 2026-07-10, from current main, control Issue #461, Work Orders #458/#459/#460, and current PR state.

| Area | Current state | Next control action |
| --- | --- | --- |
| WebChat runtime and governed customer-visible boundary | Main contains WebChat AI runtime, Runtime Context Guard, TrackingFactResult, KnowledgeItem/KnowledgeChunk, CustomerVisibleMessageService, Tool Registry, Policy Gate, Handoff Service, support memory ledger, and WebChat QA observability foundations. | Preserve boundaries; no customer-visible bypasses. |
| Runtime audit integration | PR #452 merged into main as `6261ecf0d81ccfef3a8790a4c7ca1d9f163e69f8`. | Treat as baseline. |
| Human hours and escalation orchestration | PR #453 merged into main as `57b1f89351df04b00e95b57db5aa1fe00aaacc6a`, default-off and using existing handoff/auto-ticket services. | Address post-merge P2 findings through Work Order #459 when unblocked. |
| Governed tool execution | PR #454 merged into main as `f5f4cd13d87a7766ca5fd5b43751979a326825c8` after Admin gate. Validated head `0b7bf941d460a364b41c8078b79d128b8e68ed87` had required checks green. | Treat as M3 baseline. Do not reopen unless a verified regression appears. |
| WhatsApp operations routing | PR #455 is next. Remote head remains `200069b02d4d086e76382a9a40257b682abf940f` on old base. Agent 09 produced a clean local rebase target but could not push because remote auth/object creation was unavailable. | A writer with authenticated push or Git object creation should repeat/apply the clean rebase onto `f5f4cd13`, then run focused WhatsApp routing checks. |
| Admin debug and Control Tower surfaces | PR #456 remains draft/queued and behind current main. A preflight finding exists around raw provider group ID exposure on create/get/update detail surfaces. | Keep last until #455 lands, then rebase and fix or explicitly authorize runtime-admin provider group ID detail exposure. |
| Roadmap control files | PR #462 restores this file and `docs/roadmap/nexus-osr-plan.yaml`; this refresh updates them after #454 merged. | Admin can review/merge docs-only recovery after confirming no newer main change invalidates facts. |

## 2026 capability map

Month labels are sequencing hints, not rigid calendar commitments. Current SHA, dependencies, and control-plane claims determine execution order.

### M1 — Runtime decision and case-context foundation

Target outcome: all AI decisions are represented as governed runtime state, not ad hoc text generation.

Acceptance focus:

- No C-end long-term memory introduced.
- Case Context remains short-lived and case-scoped.
- MCP facts remain higher authority than customer claims, previous AI replies, and knowledge hits.
- Customer-visible messages remain governed.

Current status: runtime audit and human-hours/escalation foundations have landed on main. Post-merge P2 escalation findings remain under Work Order #459.

### M2 — Handoff, auto-ticket, and offline closure path

Target outcome: human handoff and offline auto-ticket closure work as production-safe runtime decisions.

Acceptance focus:

- Human online routes to existing handoff.
- Human offline creates tickets when escalation requires it.
- No Agent path generates customer-visible body text outside the governed boundary.
- Reused escalation tickets remain visible for human/operator review.
- Configured escalation policies can trigger even when terms are not in legacy hard-coded filters.

Current status: main includes default-off escalation orchestration; Work Order #459 remains the follow-up holder.

### M3 — Governed tool execution baseline

Target outcome: OSR tool proposals can become policy-controlled operational actions without bypassing audit, idempotency, or customer-visible governance.

Acceptance focus:

- `OSR_TOOL_EXECUTION_MODE` remains safe by default, currently observe-only unless explicitly configured.
- Policy execute allow-list remains limited to `ticket.create`, `handoff.request.create`, and `timeline.event.create`.
- High-risk Speedaf write tools stay blocked by default.
- Safe customer-visible results are never direct sends.
- Raw tool arguments do not leak to audit/debug/customer surfaces.

Current status: PR #454 is merged and is now the mainline M3 baseline.

### M4 — MCP truth contract and tracking authority

Target outcome: tracking/status answers clearly separate current status facts from history enrichment.

Acceptance focus:

- `speedaf.order.query` can satisfy current status when trusted.
- `speedaf.express.track.query` can enrich history but cannot override current status.
- Customer claims about delivery/non-delivery remain claims until verified.
- Previous AI replies are not evidence.

Current status: PR #395 and PR #446 are historical/reference material; do not revive or merge without current-main Work Order and conflict review.

### M5 — Customer-visible knowledge quality

Target outcome: knowledge answers are scoped, customer-visible, and safe for policy/service commitments.

Acceptance focus:

- Knowledge never answers live tracking status.
- Country/channel/audience filters are enforced.
- Customer-visible templates are separated from internal notes.
- Unsupported or stale answers become knowledge gap tasks.

### M6 — WhatsApp operations routing

Target outcome: OSR-created tickets/cases can route to the correct operations group through configuration without direct sidecar coupling.

Acceptance focus:

- No hard-coded countries or groups.
- No send without enabled routing rule.
- Provider group IDs are not exposed in general surfaces.
- Customer-visible behavior remains unchanged.
- No WhatsApp sidecar change unless separately approved.

Current status: PR #455 is the next runtime dependency after #454. Remote branch still needs a current-main rebase/update and focused tests.

### M7 — Debug, eval, and regression gate

Target outcome: AI behavior is inspectable, replayable, and continuously regression-tested.

Acceptance focus:

- Tracking without current MCP fact must not answer live status.
- Complaint/compensation escalates according to policy.
- Human offline creates a ticket when required.
- Tool disabled blocks execution.
- Customer-visible replies contain no raw internal payloads.

### M8 — Admin policy APIs and Control Tower

Target outcome: operators can manage OSR safely through admin APIs and read-only observability surfaces.

Acceptance focus:

- RuntimeDecisionAudit remains read-only through admin APIs.
- Sensitive fields remain redacted.
- Admin/debug/control tower work merges after runtime foundations.

Current status: PR #456 is draft/queued last and has a known provider group ID detail-surface decision/fix pending.

### M9 — Operations workspace and queues

Target outcome: human operators can close cases with evidence, SLA context, and safe action suggestions.

Acceptance focus:

- Operator-visible state reflects high-risk and offline escalations.
- Actions are auditable and permission-gated.
- No raw sensitive data leaks in shared surfaces.

### M10 — Operations analytics and Control Tower maturity

Target outcome: operational leaders can measure AI closure, handoff, tickets, tool execution, routing, and knowledge quality.

Acceptance focus:

- Metrics are sourced from durable audit/read-model records.
- Dashboards do not expose raw PII, tool payloads, provider payloads, or provider group IDs.

### M11 — Internal SOP SkillBank

Target outcome: internal-only skills assist operators without becoming uncontrolled customer-facing behavior.

Acceptance focus:

- Skills do not bypass ToolExecutionPolicy or customer-visible message governance.
- External-facing outputs still pass governed outbound boundaries.

### M12 — Production hardening and readiness

Target outcome: OSR is production-ready for multi-country operations with rollback, privacy, load, and regression discipline.

Acceptance focus:

- Full CI and relevant integration/migration checks are green for exact release head.
- Rollback implications are documented.
- No unresolved safety/governance review blocker remains.
- No production deployment is performed from autonomous development workflows.

## Active dependency order

1. Treat PR #454 as merged M3 baseline on main `f5f4cd13d87a7766ca5fd5b43751979a326825c8`.
2. Finish the PR #455 WhatsApp routing branch update/rebase onto `f5f4cd13`, then run focused WhatsApp routing, persistence, and auto-ticket checks.
3. Keep PR #456 queued until #455 lands; then rebase and fix/authorize provider group ID detail exposure.
4. Close or supersede stale PR #450 once these restored roadmap files are merged.
5. Address Work Order #459 escalation follow-up on a current-main branch when it does not conflict with the active runtime PR.
6. Continue M4/M5/M7+ only through current-main Work Orders and exact-head conflict checks.

## Swarm execution policy

- Product-code changes require an explicit `[OSR Work Order]` issue.
- Every external write must be preceded by a valid ACTION_CLAIM in the control issue unless creating the control issue itself.
- A claim must be read back and conflict-checked before execution.
- One coherent action per agent run.
- Full GitHub Actions, release locks, ready-for-review transitions, and merges are Admin/release-gate responsibilities.
- If no material non-conflicting action exists, agents should exit quietly.

## Stale PR guidance

- PR #450: strategic annual-plan source, but stale against current main. Supersede with the restored `docs/roadmap/*` files once reviewed.
- PR #455: current primary runtime dependency; remote branch still needs authenticated update after #454 merge.
- PR #456: keep queued last; admin/debug surfaces depend on stable runtime foundations and provider-group-ID redaction decision.
- Older WhatsApp/native, tracking, and knowledge PRs must not be revived without current-main Work Order, exact-head review, and conflict check.
