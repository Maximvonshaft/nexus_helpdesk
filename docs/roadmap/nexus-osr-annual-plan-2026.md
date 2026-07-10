# Nexus OSR Annual Plan 2026

## Governance status

This file is a strategic capability plan, not the live delivery board.

Authoritative live execution state is maintained in:

1. Delivery Index Issue #489
2. M1–M12 Epic Issues #490–#501
3. executable Work Item Issues
4. one current Pull Request per active Work Item

GitHub Project #1 is optional and non-authoritative. No workflow, release gate, or merge decision may depend on Project fields or views.

Current point-in-time main at this reconciliation: `dddd7c4f8b579bb8653d4f4fc8e452365df72c14`.

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

| Milestone | Epic | Capability | Strategic status at reconciliation |
|---|---:|---|---|
| M1 | #490 | RuntimeDecision and Case Context Kernel | Delivered baseline |
| M2 | #491 | Escalation, Handoff and Auto-ticket Closure | Active |
| M3 | #492 | Governed Tool Execution | Delivered baseline |
| M4 | #493 | Tracking Truth Layer | Ready |
| M5 | #494 | Customer-visible Knowledge Quality | Ready; K1 before K2 |
| M6 | #495 | Channel Gateway and Operations Routing | Delivered baseline |
| M7 | #496 | Debug, Eval and Audit | Active |
| M8 | #497 | Admin Policy APIs and Control Tower | Delivered baseline |
| M9 | #498 | Operator Workspace and Queues | Backlog |
| M10 | #499 | Operations Analytics and Control Tower Maturity | Backlog |
| M11 | #500 | Internal SOP SkillBank | Backlog |
| M12 | #501 | Production Hardening and Readiness | `NO_GO` until runtime evidence is complete |

The table is a strategic snapshot only. Current lifecycle, ownership, blockers, current PRs, and release state must be read from #489 and the linked Work Items.

## Stable dependency doctrine

- M2 ticket-safety work precedes configured escalation entry.
- M5 Knowledge K1 data safety precedes K2 readiness.
- M9 follows stable M2, M4, M5, M6, and M8 foundations.
- M10 follows an operationally complete M9 workflow.
- M11 follows safe Knowledge, Admin, and Operator foundations.
- M12 is the final release program and cannot be inferred from green CI alone.

## Issue-first delivery policy

- Product-code changes require a narrow executable Work Item.
- Work Item ownership is assignee plus lifecycle state plus one linked current Draft PR.
- Comment-based claims and leases are not used.
- Maximum active product Work Items: two.
- Maximum release candidates: one.
- Merge one PR at a time.
- Re-read main after every merge.
- Recompute downstream base, migration chain, and affected tests after every merge.
- Old-base green checks are not merge authority.
- Parent defect and audit Issues are evidence/portfolio records when executable child Work Items exist.
- Roadmap files are not updated after every status transition; #489 is the live index.

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
- #489 is the authoritative live delivery index.
- Historical and superseded PRs remain reference evidence only.
- GitHub Project #1 may be retained for personal visualization, but it is not maintained by the delivery workflow and must not be treated as authoritative.
