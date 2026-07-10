# Nexus OSR Delivery Governance

## Purpose

This document defines how Nexus OSR work is planned, owned, implemented, reviewed, accepted, and archived. It replaces comment-driven task locking and manually synchronized status documents.

Nexus OSR is a multi-country logistics customer-service and operations-closure runtime. Governance must preserve its safety kernel while allowing small, independently reviewable increments to move quickly.

## Source-of-truth hierarchy

Use the following authority order:

1. **GitHub Project `Nexus OSR 2026 Delivery`** — live status, priority, owner, iteration, risk, and release stage.
2. **Epic Issue** — durable M1–M12 capability outcome and completion boundary.
3. **Work Item Issue** — independently assignable, implementable, reviewable, and closable unit of work.
4. **Pull Request** — implementation facts, exact SHAs, diff, migration, tests, runtime evidence, and rollback.
5. **Architecture and roadmap documentation** — durable doctrine and milestone intent, not live execution state.
6. **Audit portfolio** — point-in-time findings and remediation evidence, not task authorization.

A lower layer must not contradict a higher layer. Point-in-time SHAs in Issues or reports are evidence, not manually maintained live status fields.

## Immutable safety kernel

Every Epic, Work Item, and Pull Request must preserve these rules:

- No C-end long-term customer memory; only short-lived, case-scoped Case Context.
- MCP and approved operational systems are authoritative for live facts.
- Customer claims and previous AI replies are not facts.
- Customer-visible output uses `CustomerVisibleMessageService` or the governed outbound contract.
- AI actions pass the governed policy and controlled execution boundary.
- Human online uses handoff; human offline uses automatic ticket creation when escalation requires it.
- Complaints, compensation, refunds, legal threats, personal-data requests, and other high-risk cases use configurable escalation policy.
- Country, language, channel, queue, tool, policy, and routing behavior remain configuration-driven.
- Tenant, country, channel, permission, and privacy isolation are mandatory.
- Raw prompts, provider payloads, tool arguments/results, credentials, tracking numbers, phone/email, addresses, and provider group IDs do not appear on unsafe surfaces.

## Planning model

### Epic

An Epic represents one durable product capability, normally one annual milestone. It owns:

- final business and operational outcome;
- product and architecture boundaries;
- child Work Items;
- dependency graph;
- completion evidence.

An Epic is not a branch, a release candidate, or a place for execution logs.

### Work Item

A Work Item is the smallest unit that can be independently assigned, implemented, reviewed, accepted, and closed. It owns:

- current verified behavior;
- expected behavior;
- allowed and forbidden scope;
- acceptance criteria;
- test and runtime evidence requirements;
- migration, rollout, repair, and rollback requirements;
- blocked-by and blocking relationships.

Stable requirements belong in the Work Item. Volatile implementation facts such as exact head SHA, actual changed files, final migration revision, and test output belong in the Pull Request.

### Pull Request

One Work Item may have only one current implementation Pull Request. The Pull Request must:

- link the Work Item and parent Epic;
- start from then-current `main`;
- remain Draft until exact-head evidence is accepted;
- contain implementation, migration, validation, runtime, failure, and rollback facts;
- list material items not verified;
- close the Work Item only after accepted merge.

Old-base, superseded, or abandoned Pull Requests are closed and retained as historical evidence. They are never current merge authority.

## Project fields

The Project should contain these fields:

| Field | Values |
|---|---|
| Status | Backlog / Ready / In Progress / In Review / Release Gate / Blocked / Done |
| Type | Epic / Feature / Bug / Tech Debt / Security / Research / Release |
| Capability | Runtime / Escalation / Tools / Truth / Knowledge / Routing / Debug / Admin / Operator / Analytics / SkillBank / Production |
| Priority | P0 / P1 / P2 / P3 |
| Risk | Low / Medium / High / Critical |
| Runtime Evidence | None / SQLite / PostgreSQL / Staging / Production |
| Release Gate | Not Ready / Focused Green / Full Green / Accepted |
| Target Date | Date |

## Standard lifecycle

1. A requirement, defect, or audit finding is linked to an Epic.
2. A Work Item is created with complete acceptance and safety boundaries.
3. Dependencies are recorded before the Work Item enters `Ready`.
4. Ownership is established by assignee plus `Status = In Progress`.
5. One Draft Pull Request is opened from current `main`.
6. Focused validation moves the item to `In Review`.
7. Exact-head full checks and required runtime evidence move it to `Release Gate`.
8. The release owner accepts or rejects the exact head.
9. Merge closes the Work Item and moves it to `Done`.
10. The parent Epic is closed only when all completion criteria are satisfied.

## WIP and ownership

- Maximum active Work Items: two.
- Maximum release candidates: one.
- One owner per Work Item.
- One current implementation Pull Request per Work Item.
- Shared review does not create a second implementation branch.
- Ownership is visible through Project status, assignee, and linked Pull Request; comment-based leases are not used.

## Dependency and merge policy

- Use native blocked-by/blocking relationships where available.
- Merge one Pull Request at a time.
- Re-read `main` after every merge.
- Recompute downstream base, migration chain, and affected tests after every merge.
- Old-base green checks are not merge authority.
- Parallel schema work must converge to one expected Alembic head before release acceptance.

## Release evidence

A release decision must distinguish:

- focused unit and contract tests;
- full regression checks;
- PostgreSQL migration and concurrency evidence;
- staging or production-like runtime evidence;
- restore and rollback evidence;
- load, failure-injection, alert, and incident readiness.

Green CI alone is not production proof. M12 remains `NO_GO` until the required runtime and operational evidence is complete.

## Historical records

- Issue #461 is the closed historical swarm control log.
- Issue #467 is the audit and remediation evidence portfolio.
- Issue #489 is the governance migration and Epic index.
- Historical Pull Requests remain available for code and review evidence after closure.

Roadmap Markdown may describe architecture and milestone intent. Roadmap YAML must not be manually maintained as a second live execution database. If machine-readable exports are required, generate them from the GitHub Project.
