# Nexus OSR Delivery Governance

## Purpose

This document defines how Nexus OSR work is planned, owned, implemented, reviewed, accepted, and archived through GitHub Issues and Pull Requests.

The control model is intentionally limited to GitHub surfaces that the connected operating agent can read and update reliably. GitHub Projects is optional and non-authoritative.

## Source-of-truth hierarchy

Use the following authority order:

1. **Work Item Issue** — lifecycle, owner, dependencies, acceptance criteria, current PR, and closure state for one independently deliverable unit of work.
2. **Epic Issue** — durable M1–M12 capability outcome, child Work Items, dependency graph, and completion boundary.
3. **Pull Request** — implementation facts, exact SHAs, changed files, migration impact, tests, runtime evidence, review, and rollback.
4. **Issue #489** — stable navigation index for Epics, open Work Items, governance rules, and query links. It must not duplicate volatile implementation details.
5. **Architecture and roadmap documentation** — durable doctrine and milestone intent, not live execution state.
6. **Audit portfolio #467** — point-in-time findings and remediation evidence, not task authorization.

The user-created GitHub Project may be used as a personal visualization, but no Nexus OSR workflow, release gate, ownership decision, or merge decision depends on it.

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

### Epic Issue

An Epic represents one durable product capability, normally one annual milestone. It owns:

- final business and operational outcome;
- product and architecture boundaries;
- child Work Items;
- dependency graph;
- completion evidence.

An Epic is not a branch, a release candidate, or a place for execution logs. Close an Epic with `completed` only when all completion criteria are satisfied.

### Work Item Issue

A Work Item is the smallest unit that can be independently assigned, implemented, reviewed, accepted, and closed. It owns:

- parent Epic;
- current verified behavior;
- expected behavior;
- allowed and forbidden scope;
- lifecycle state;
- current owner;
- blocked-by and blocking relationships;
- acceptance criteria;
- test and runtime evidence requirements;
- migration, rollout, repair, and rollback requirements;
- current implementation Pull Request, when one exists.

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

## Lifecycle contract

Lifecycle is recorded directly in the Work Item Issue.

| Lifecycle | Required evidence |
|---|---|
| Backlog | Open Issue; outcome known; not yet acceptance-ready |
| Ready | Open Issue; acceptance complete; no unresolved blocker; no current owner required |
| In Progress | Open Issue; assignee set; implementation active; at most one current PR |
| In Review | Open Issue; one current Draft or review-ready PR linked |
| Release Gate | Open Issue; exact-head checks complete; release decision pending |
| Blocked | Open Issue; explicit `Blocked by #...` dependency or external evidence |
| Done | Issue closed with reason `completed` after accepted merge or verified non-code completion |
| Not Planned | Issue closed with reason `not_planned` and explicit disposition |

Each open Work Item body must contain a compact control block:

```markdown
## Control
- Parent Epic: #...
- Lifecycle: Backlog | Ready | In Progress | In Review | Release Gate | Blocked
- Owner: @username | unassigned
- Current PR: #... | none
- Blocked by: #... | none
- Supersedes: #... | none
```

Issue state is authoritative for completion. Assignee is authoritative for ownership. The linked current Pull Request is authoritative for implementation status and exact-head evidence.

## Standard lifecycle

1. A requirement, defect, or audit finding is linked to one Epic.
2. A Work Item is created with complete acceptance and safety boundaries.
3. Dependencies are recorded before the Work Item enters `Ready`.
4. Ownership is established by assigning the Work Item and setting `Lifecycle: In Progress`.
5. One Draft Pull Request is opened from current `main` and linked as `Current PR`.
6. The Work Item moves to `In Review` while focused validation and review run.
7. Exact-head full checks and required runtime evidence move it to `Release Gate`.
8. The release owner accepts or rejects the exact head.
9. Accepted merge closes the Work Item with `completed`.
10. The parent Epic closes only when all child completion criteria are satisfied.

## WIP and ownership

- Maximum active implementation Work Items: two.
- Maximum release candidates: one.
- One owner per Work Item.
- One current implementation Pull Request per Work Item.
- Shared review does not create a second implementation branch.
- Comment-based leases and centralized claim logs are not used.
- A parent defect or audit Issue is not counted as a second workstream when executable child Work Items exist.

## Dependency and merge policy

- Record `Blocked by #...` directly in the Work Item; use native GitHub dependencies where available.
- Merge one Pull Request at a time.
- Re-read `main` after every merge.
- Recompute downstream base, migration chain, and affected tests after every merge.
- Old-base green checks are not merge authority.
- Parallel schema work must converge to one expected Alembic head before release acceptance.

## Governance index

Issue #489 is a stable navigation surface, not a manually synchronized project database. It may contain:

- links to M1–M12 Epic Issues;
- links to open Work Items;
- links to current Pull Requests;
- WIP and release rules;
- saved GitHub search URLs;
- historical-control and audit references.

It must not copy exact head SHAs, CI results, changed files, or detailed lifecycle evidence already owned by a Work Item or Pull Request.

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
- Issue #489 is the Issue-only governance index.
- Issue #505 records the rejected GitHub Project control path and is closed as `not_planned`.
- Historical Pull Requests remain available for code and review evidence after closure.

Roadmap Markdown may describe architecture and milestone intent. Roadmap YAML must not be manually maintained as a second live execution database. Any machine-readable export must be generated from GitHub Issues and Pull Requests, not from a manually maintained Project board.
