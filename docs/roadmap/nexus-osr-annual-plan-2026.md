# Nexus OSR Annual Plan 2026

## Governance status

This file is a strategic capability plan, not a live delivery board.

Live execution state is owned by:

1. executable Work Item Issues;
2. M1–M12 Epic Issues;
3. one current Pull Request per active Work Item;
4. Issue #489 as a stable navigation index.

GitHub Project #1 is optional and non-authoritative. No workflow, release gate, ownership decision, or merge decision may depend on Project fields or views.

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
- No production deploys, release tags, real external customer messages, funds/legal/identity actions, or irreversible deletion are authorized by planning status.

## Product doctrine

Nexus OSR closes logistics cases safely through facts, policies, tickets, handoff, governed tools, durable operations dispatch, debug/eval/audit, Admin, Operator, Analytics, SkillBank, and Production Readiness capabilities. AI acts only inside configured and auditable boundaries.

## Capability map

| Milestone | Epic | Capability | Stable dependency |
|---|---:|---|---|
| M1 | #490 | RuntimeDecision and Case Context Kernel | Foundation |
| M2 | #491 | Escalation, Handoff and Auto-ticket Closure | Ticket safety before configured escalation |
| M3 | #492 | Governed Tool Execution | M1 safety kernel |
| M4 | #493 | Tracking Truth Layer | MCP/approved current-status authority |
| M5 | #494 | Customer-visible Knowledge Quality | K1 data safety before K2 readiness |
| M6 | #495 | Channel Gateway and Operations Routing | Configuration-driven routing and durable outbox |
| M7 | #496 | Debug, Eval and Audit | Redacted runtime evidence |
| M8 | #497 | Admin Policy APIs and Control Tower | Permission and tenant isolation |
| M9 | #498 | Operator Workspace and Queues | M2, M4, M5, M6, M8 |
| M10 | #499 | Operations Analytics and Control Tower Maturity | M9 |
| M11 | #500 | Internal SOP SkillBank | M5, M8, M9 |
| M12 | #501 | Production Hardening and Readiness | Final release program |

Current lifecycle, ownership, blockers, current PRs, exact SHAs, CI results, and release state must be read from the linked Work Items and Pull Requests, not from this file.

## Issue-only delivery policy

- Product-code changes require a narrow executable Work Item.
- Every open Work Item contains a `Control` block with parent Epic, lifecycle, owner, current PR, blockers, and supersession state.
- Work Item ownership is the Issue assignee.
- Comment-based claims and leases are not used.
- Maximum active implementation Work Items: two.
- Maximum release candidates: one.
- One current Pull Request per Work Item.
- Merge one PR at a time.
- Re-read main after every merge.
- Recompute downstream base, migration chain, and affected tests after every merge.
- Old-base green checks are not merge authority.
- Parent defect and audit Issues are evidence/portfolio records when executable child Work Items exist.
- Roadmap files are not updated after every status transition.

## Release evidence doctrine

A production release decision requires more than unit or PR CI. M12 must include, as applicable:

- real PostgreSQL concurrency and idempotency evidence;
- migration upgrade, downgrade, re-upgrade, and repair rehearsal;
- backup restore and recovery-objective evidence;
- queue backlog, worker restart, timeout, retry, and circuit-breaker tests;
- load, capacity, rate-limit, and failure-injection results;
- logs, metrics, traces, alerts, and PII redaction;
- incident, rollback, canary, retention, and DSAR runbooks;
- exact release-candidate and post-merge acceptance.

## Historical guidance

- #461 is the closed historical swarm control log and accepts no new claims.
- #467 is the audit and remediation evidence portfolio.
- #489 is the Issue-only navigation index.
- #505 records the rejected Project-control path and is closed as `not_planned`.
- Historical and superseded PRs remain reference evidence only.
- GitHub Project #1 may be retained for personal visualization, but it is not maintained by the delivery workflow and is never authoritative.
