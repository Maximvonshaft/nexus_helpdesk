# Nexus OSR Annual Plan 2026

## Governance status

This file defines the annual capability plan and stable dependency doctrine. It is not a live delivery board.

Live execution state is owned by:

1. executable Work Item Issues;
2. structured claim, heartbeat, handoff, and reclaim comments on those Issues;
3. M1–M12 Epic Issues;
4. one current Pull Request per active Work Item;
5. Issue #489 as a stable navigation index.

GitHub Project #1 is optional and non-authoritative. No workflow, claim, release gate, ownership decision, or merge decision may depend on Project fields or views.

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
- Raw prompts, provider payloads, tool arguments/results, tracking numbers, phone/email, credentials, and provider group IDs must not leak into unsafe surfaces, Issue comments, logs, or artifacts.
- No production deploys, release tags, real external customer messages, funds/legal/identity actions, or irreversible deletion are authorized by planning status or an Agent claim.

## Product doctrine

Nexus OSR closes logistics cases safely through facts, policies, tickets, handoff, governed tools, durable operations dispatch, debug/eval/audit, Admin, Operator, Analytics, SkillBank, and Production Readiness capabilities. AI acts only inside configured and auditable boundaries.

## Annual capability map

| Milestone | Epic | Capability outcome |
|---|---:|---|
| M1 | #490 | RuntimeDecision and short-lived Case Context kernel |
| M2 | #491 | Configurable escalation, handoff, safe auto-ticket and offline closure |
| M3 | #492 | Policy-controlled and auditable tool execution |
| M4 | #493 | MCP-first Tracking truth contract with structured source/freshness semantics |
| M5 | #494 | Safe customer-visible Knowledge quality and readiness |
| M6 | #495 | Configuration-driven channel routing and durable operations dispatch |
| M7 | #496 | Redacted debug, eval, audit and regression evidence |
| M8 | #497 | Secure Admin policy APIs and Control Tower foundations |
| M9 | #498 | End-to-end Operator Workspace and queues |
| M10 | #499 | Operations analytics and Control Tower maturity |
| M11 | #500 | Versioned internal SOP SkillBank |
| M12 | #501 | Production hardening, runtime proof and release readiness |

Current lifecycle, ownership, claims, blockers, current PRs, CI evidence, and merge state must be read from the Epic, Work Item, Issue comments, and Pull Request—not from this file.

## Stable dependency doctrine

- M2 ticket-safety work precedes configured escalation entry.
- M5 Knowledge K1 data safety precedes K2 readiness.
- M9 follows stable M2, M4, M5, M6, and M8 foundations.
- M10 follows an operationally complete M9 workflow.
- M11 follows safe Knowledge, Admin, and Operator foundations.
- M12 is the final release program and cannot be inferred from green CI alone.

Dependencies restrict merge and release order, not unrelated development. Independent Work Items may be developed, reviewed, and tested concurrently. Explicitly stacked PRs may be developed concurrently while preserving parent-before-child merge order.

## Issue-only delivery policy

- Product-code changes require a coherent executable Work Item.
- Prefer substantial vertical Work Items with one dominant contract, one acceptance boundary, and one rollback boundary; do not create micro-Issues per file or test.
- The Work Item Issue owns lifecycle, account-level owner, blockers, current PR, acceptance and closure state.
- Structured Issue comments own transient Agent Run claims, 120-minute leases, heartbeats, interruption evidence, handoffs, and reclaims.
- The Pull Request owns exact SHAs, changed files, migration impact, tests, runtime evidence and rollback.
- Issue #489 is navigation-only and must not duplicate volatile implementation evidence.
- There is no fixed repository-wide limit on independent active Work Items or Agent sessions.
- One valid unexpired Agent Run claim and one current implementation PR are allowed per Work Item.
- Conflicting resources, dependencies, migration chains, generated artifacts, external mutable resources, main integration, deployment, tags, and production actions are serialized when required.
- A graceful incomplete Agent exit must leave a structured handoff comment. A hard crash is recovered after lease expiry through reclaim and complete fact re-verification.
- Merge accepted PRs into `main` in a controlled sequence.
- Re-read main after every merge.
- Recompute downstream base, migration chain, resource conflicts, and affected tests after every merge.
- Old-base green checks are not merge authority.
- Parent defect and audit Issues are evidence/portfolio records when executable child Work Items exist.

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
- #505 records the rejected GitHub Project control path and is closed as `not_planned`.
- Historical and superseded PRs remain reference evidence only.
