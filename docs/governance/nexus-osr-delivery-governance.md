# Nexus OSR Delivery Governance

## Purpose

This document defines how Nexus OSR work is planned, claimed, implemented, reviewed, accepted, handed off, recovered, and archived through GitHub Issues and Pull Requests.

The control model is intentionally limited to GitHub surfaces that the connected operating agent can read and update reliably. GitHub Projects is optional and non-authoritative.

## Source-of-truth hierarchy

Use the following authority order:

1. **Work Item Issue** — lifecycle, account-level owner, dependencies, acceptance criteria, current PR, and closure state for one independently deliverable unit of work.
2. **Work Item comment thread** — transient Agent Run identity, claim lease, heartbeat, interruption evidence, handoff, and reclaim history.
3. **Epic Issue** — durable M1–M12 capability outcome, child Work Items, dependency graph, and completion boundary.
4. **Pull Request** — implementation facts, exact SHAs, changed files, migration impact, tests, runtime evidence, review, and rollback.
5. **Issue #489** — stable navigation index for Epics, open Work Items, governance rules, and query links. It must not duplicate volatile implementation details.
6. **Architecture and roadmap documentation** — durable doctrine and milestone intent, not live execution state.
7. **Audit portfolio #467** — point-in-time findings and remediation evidence, not task authorization.

The user-created GitHub Project may be used as a personal visualization, but no Nexus OSR workflow, release gate, ownership decision, claim decision, or merge decision depends on it.

## Immutable safety kernel

Every Epic, Work Item, Agent comment, and Pull Request must preserve these rules:

- No C-end long-term customer memory; only short-lived, case-scoped Case Context.
- MCP and approved operational systems are authoritative for live facts.
- Customer claims and previous AI replies are not facts.
- Customer-visible output uses `CustomerVisibleMessageService` or the governed outbound contract.
- AI actions pass the governed policy and controlled execution boundary.
- Human online uses handoff; human offline uses automatic ticket creation when escalation requires it.
- Complaints, compensation, refunds, legal threats, personal-data requests, and other high-risk cases use configurable escalation policy.
- Country, language, channel, queue, tool, policy, and routing behavior remain configuration-driven.
- Tenant, country, channel, permission, and privacy isolation are mandatory.
- Raw prompts, provider payloads, tool arguments/results, credentials, tracking numbers, phone/email, addresses, and provider group IDs do not appear on unsafe surfaces, Issue comments, PR descriptions, logs, or artifacts.

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

A Work Item is a coherent, independently claimable, implementable, reviewable, rollbackable, and closable unit of work. It owns:

- parent Epic;
- current verified behavior;
- expected observable outcome;
- allowed and forbidden scope;
- lifecycle state;
- account-level owner;
- blocked-by and blocking relationships;
- acceptance criteria;
- test and runtime evidence requirements;
- migration, rollout, repair, and rollback requirements;
- declared conflict resources;
- current implementation Pull Request, when one exists.

Stable requirements belong in the Work Item. Volatile implementation facts such as exact head SHA, actual changed files, final migration revision, and test output belong in the Pull Request. Transient session state belongs in structured comments.

### Work Item sizing

Prefer a substantial vertical outcome over micro-Issues. One Work Item may span backend, database, worker, API, UI, tests, and documentation when all changes implement one dominant contract and share one acceptance and rollback boundary.

Split a Work Item only when at least one of these is true:

- a real dependency requires ordered delivery;
- the parts have independent rollback or release risk;
- different exclusive resources or owners are required;
- the parts are independently valuable and independently acceptable;
- one review would become too broad to reason about safely;
- schema or external side effects require a separate migration or rollout gate.

Do not create one Issue per file, test, endpoint, minor refactor, or cosmetic change.

### Pull Request

One Work Item may have only one current implementation Pull Request. The Pull Request must:

- link the Work Item and parent Epic;
- identify the active or reclaimed Agent Run;
- start from then-current `main`, or explicitly declare and validate a stacked dependency;
- remain Draft until exact-head evidence is accepted;
- contain implementation, migration, validation, runtime, failure, and rollback facts;
- list material items not verified;
- close the Work Item only after accepted merge.

Old-base, superseded, or abandoned Pull Requests are closed and retained as historical evidence. They are never current merge authority. A reclaiming Agent should continue the existing current PR when safe rather than create a duplicate.

## Lifecycle contract

Lifecycle is recorded directly in the Work Item Issue.

| Lifecycle | Required evidence |
|---|---|
| Backlog | Open Issue; outcome known; not yet acceptance-ready or dependency-ready |
| Ready | Open Issue; acceptance complete; no unresolved blocker; claimable through the comment lease protocol |
| In Progress | Open Issue; one valid Agent claim; implementation active; at most one current PR |
| In Review | Open Issue; one current Draft or review-ready PR linked; claim remains active or is explicitly handed off |
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
- Current PR: #... | pending | none
- Blocked by: #... | none
- Supersedes: #... | none
```

Issue state is authoritative for completion. Assignee is authoritative for account-level ownership. The comment thread is authoritative for the active Agent Run and lease. The linked current Pull Request is authoritative for implementation status and exact-head evidence.

## Agent claim, lease, heartbeat, handoff, and reclaim

### Claim eligibility

An unclaimed Work Item is normally claimable only when:

- the Issue is open;
- it carries the `osr-work-order` label;
- `Lifecycle: Ready`;
- `Current PR: none`;
- `Blocked by: none`;
- no valid unexpired claim exists in the comments;
- no other active Work Item or PR owns an exclusive conflicting resource.

An interrupted `In Progress` or `In Review` Work Item is reclaimable when the prior claim is explicitly released or its lease has expired.

### Claim format

Before any branch, file, PR, or Issue-body write, the Agent posts:

```markdown
## AGENT_CLAIM
- Run ID: <unique-run-id>
- Work Item: #<issue>
- Lease: 120 minutes from this comment timestamp
- Starting main: <sha>
- Intended branch: <branch>
- Intended PR: pending | #<pr>
- Dependency mode: independent | stacked on #<pr>
- Declared write resources:
  - paths: ...
  - contracts: ...
  - database/migrations: ...
  - workflows/generated files/external resources: ...
- Dependencies verified: yes | no — <details>
```

After posting, the Agent must re-read the Issue and all comments. The earliest valid unexpired claim wins. A later claimant must post:

```markdown
## AGENT_CLAIM_WITHDRAWN
- Run ID: <run-id>
- Winning claim: <comment reference or run-id>
- Writes performed: none
```

and stop work on that Issue.

### Lease semantics

The default lease is 120 minutes from the GitHub timestamp of the latest valid `AGENT_CLAIM` or `AGENT_HEARTBEAT` comment for that Run ID. A Work Item may define a different lease only in its stable requirements.

The lease is a coordination mechanism, not a lock on Git. It does not authorize dependency bypass, conflicting writes, merge, deployment, production mutation, or unsafe actions.

### Heartbeat

Post a heartbeat after branch creation, PR creation, meaningful commits, material test results, dependency changes, and before yielding control:

```markdown
## AGENT_HEARTBEAT
- Run ID: <run-id>
- Branch: <branch>
- PR: pending | #<pr>
- Head: <sha | none>
- Main last verified: <sha>
- Completed since last heartbeat: ...
- Checks: ...
- Current blocker: none | ...
- Next action: ...
- Lease renewed: 120 minutes from this comment timestamp
```

### Graceful incomplete exit and exception handoff

Before any graceful incomplete exit, timeout-aware shutdown, permission failure, unrecoverable test failure, merge conflict, dependency discovery, scope violation, or decision to stop, the Agent must post:

```markdown
## AGENT_HANDOFF
- Run ID: <run-id>
- Reason code: timeout | test_failure | permission | dependency | conflict | scope | interrupted | other
- Summary: <bounded explanation>
- Last verified main: <sha>
- Branch: <branch | none>
- PR: #<pr> | none
- Head: <sha | none>
- Files changed: ...
- Tests/checks completed: ...
- Tests/checks failing or not run: ...
- Migration/Alembic state: ...
- Blockers and bounded error evidence: ...
- Cleanup or rollback performed: ...
- Next safe action: ...
- Claim released: yes | no
```

The Agent must not claim completion and must not close the Issue. Comments must remain redacted and low-cardinality.

### Hard crash

A process that is killed abruptly cannot be guaranteed to write a handoff comment. Recovery therefore depends on lease expiry. The absence of a handoff does not make the prior work valid or invalid; the next Agent must re-establish all facts.

### Reclaim

After explicit release or lease expiry, a new Agent posts:

```markdown
## AGENT_RECLAIM
- New Run ID: <run-id>
- Prior Run ID: <run-id>
- Reclaim basis: released | lease expired at <timestamp>
- Current main: <sha>
- Existing branch/PR/head: ...
- Existing work disposition: continue | repair | supersede
- Verified resources and dependencies: ...
- Next action: ...
- Lease: 120 minutes from this comment timestamp
```

The reclaiming Agent must re-read current `main`, the full Issue, all comments, the current PR, changed files, reviews, checks, branches, migration heads, and relevant code. Continue the current PR when safe. If replacement is required, close or clearly supersede the old PR before creating another current PR.

### Completion

Normal code completion is:

1. accepted current PR contains `Closes #<work-item>`;
2. exact-head checks and required runtime evidence are accepted;
3. PR merges into then-current `main`;
4. GitHub closes the Work Item;
5. main, Epic, dependencies, and downstream bases are re-read and updated.

Creating a PR, passing old-base CI, posting `AGENT_DONE`, or manually setting `Lifecycle: Done` is not completion.

## Standard lifecycle

1. A requirement, defect, or audit finding is linked to one Epic.
2. A coherent Work Item is created with complete acceptance, resource, dependency, and safety boundaries.
3. Dependencies are recorded before the Work Item enters `Ready`.
4. An Agent claims the Work Item through a valid comment lease and wins the post-claim re-read race.
5. The Issue moves to `In Progress`; one branch is created from current main.
6. One Draft Pull Request is opened and linked as `Current PR`.
7. The Work Item moves to `In Review` while focused validation and review run.
8. Exact-head full checks and required runtime evidence move it to `Release Gate`.
9. The release owner accepts or rejects the exact head.
10. Accepted merge closes the Work Item with `completed`.
11. An incomplete Agent posts a handoff; a stale claim is reclaimed after expiry.
12. The parent Epic closes only when all child completion criteria are satisfied.

## Parallelism and ownership

- Independent Work Items may be claimed, implemented, reviewed, and tested concurrently. There is no fixed repository-wide Agent or active-Work-Item limit.
- One valid unexpired Agent Run claim per Work Item.
- One account-level owner per Work Item.
- One current implementation Pull Request per Work Item.
- Shared review does not create a second implementation branch.
- Explicit dependencies, exclusive files/contracts, database migration chains, generated artifacts, external mutable resources, main integration, deployment, release tags, and production actions are serialized when required.
- Parallel schema work must converge to one expected Alembic head before release acceptance.

## Dependency and merge policy

- Record `Blocked by #...` directly in the Work Item; use native GitHub dependencies where available.
- Independent development and CI are parallel by default.
- Stacked development is allowed only when the child PR explicitly targets and declares its parent PR/branch.
- Merge accepted Pull Requests into `main` in a controlled sequence.
- Re-read `main` after every merge.
- Recompute downstream base, migration chain, declared resources, and affected tests after every merge.
- Old-base green checks are not merge authority.

## Governance index

Issue #489 is a stable navigation surface, not a manually synchronized project database. It may contain:

- links to M1–M12 Epic Issues;
- links to open Work Items;
- links to the reusable Agent execution prompt;
- links to current Pull Requests;
- claim/lease and merge rules;
- saved GitHub search URLs;
- historical-control and audit references.

It must not copy exact head SHAs, CI results, changed files, heartbeat details, or execution logs already owned by a Work Item, comment thread, or Pull Request.

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
