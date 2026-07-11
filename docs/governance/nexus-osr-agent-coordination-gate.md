# Nexus OSR Agent Coordination Gate

## Purpose

The Agent Coordination Gate converts the accepted Issue claim/lease protocol into a deterministic pull-request preflight. It does not replace Work Item Issues, comments, reviews, CI or the release owner. It verifies one valid Work Item authority, one current implementation PR, valid implementation/review timing, satisfied dependencies and declared resource coordination.

The gate is read-only. It never modifies Issues, Pull Requests, branches, branch protection, deployments, Providers, outbound channels or production data.

## Authority model

1. Work Item Issue: lifecycle, blockers, Current PR, acceptance and closure.
2. Work Item comments: `AGENT_CLAIM`, `AGENT_HEARTBEAT`, `AGENT_HANDOFF`, `AGENT_RECLAIM` and `AGENT_DELIVERY` timing authority.
3. Pull Request: exact implementation SHA, resource manifest, tests, migration and rollback evidence.
4. Issue #489: stable navigation and governance rules.

Historical PRs are excluded from resource-conflict comparison unless an open Work Item names them as Current PR. A historical PR that still tries to close the same Work Item remains visible to duplicate-PR detection.

## Coordination manifest

Current PRs should include a bounded machine-readable block:

```json
{
  "schema": "nexus.osr.coordination.manifest.v1",
  "work_item": 521,
  "agent_run_id": "coord-521-reclaim-20260711T2117Z",
  "dependency": {
    "mode": "independent",
    "stack_parent_pr": null
  },
  "write_paths": [
    "scripts/ci/agent_coordination_*.py",
    "scripts/ci/tests/**"
  ],
  "read_paths": [
    "docs/governance/**"
  ],
  "contracts": ["agent-coordination-v1"],
  "database": [],
  "migrations": [],
  "generated_files": [],
  "workflows": [
    ".github/workflows/agent-coordination-gate.yml",
    ".github/workflows/agent-coordination-self-test.yml"
  ]
}
```

Resource values are bounded identifiers, not prose or payloads.

- `write_paths` owns files the PR may modify. Actual changed files must be covered.
- `read_paths` declares broad source dependencies that can create semantic coupling but do not grant write authority.
- `contracts`, `database`, `migrations`, `generated_files` and `workflows` represent exclusive governed resources where exact identity matters.

Path specifications use POSIX repository semantics:

- `*`, `?` and character classes match only within one path segment and never consume `/`;
- `**` is the only recursive directory wildcard;
- a trailing slash such as `backend/` means the complete directory subtree;
- exact files remain exclusive write resources, while broad directory/glob scopes produce review warnings unless an actual or provably narrow collision exists.

## Claim, delivery and review timing

For a first implementation, the Claim must be valid when the PR is created.

A release after PR creation does not retroactively invalidate the historical authorization that created the PR. A completed Builder posts:

```markdown
## AGENT_DELIVERY
- Agent Run ID: `...`
- Exact head: `<40-character commit SHA>`
```

The recorded exact head is mandatory. Delivery authority applies only when that SHA equals the current PR head. A later commit cannot reuse an older Delivery comment and requires a valid Reclaim before implementation resumes.

A delivered unchanged head can enter `ready_for_review`, `edited` or `reopened` evaluation after the writer lease is released. A new `synchronize` event after delivery is a new implementation write and requires an active Reclaim.

An expired but undelivered Claim is not review authority.

## Existing-PR reclaim

A later Agent must continue the existing Current PR instead of creating a duplicate. A valid Reclaim requires:

1. a prior Claim or Reclaim lease exists;
2. the prior lease is released or expired;
3. a server-timestamped top-level `## AGENT_RECLAIM` names the same Run ID used by the PR manifest;
4. GitHub records the PR as updated at or after the Reclaim comment;
5. the reclaimed lease remains active for new writes.

Quoted examples and ordinary Claims posted after PR creation are not Reclaim authority.

## Blocker authority

Every Issue number in `Blocked by:` is fetched directly, regardless of label. This includes security, governance, integration, Epic and defect Issues.

- an open blocker remains blocking;
- a closed blocker is resolved;
- unavailable or incomplete blocker lookup fails closed with a bounded reason code;
- a stacked PR must identify an open parent that closes every remaining blocker and match the parent head branch.

The gate never treats “not present in the `osr-work-order` list” as evidence that a blocker is resolved.

## Resource conflict semantics

The gate separates hard conflicts from review warnings.

### Blocking

- exact actual file collision;
- exact or provably intersecting narrow write/write authority;
- exact contract, database, generated-file or workflow ownership collision;
- migration/down-revision collision or ambiguous concurrent migration ownership.

### Warning

- broad directory or catch-all glob write overlap without an actual or narrow file collision;
- read/write overlap declared through `read_paths`;
- a current PR whose old-format manifest cannot be parsed completely.

Warnings require human review but do not falsely serialize all broad directory work. Actual file collisions remain blocking even when manifests use broad globs.

## Bounded evidence

The report schema is `nexus.osr.agent_coordination.report.v1`. It contains only bounded reason codes, PR/Issue numbers, safe resource identifiers and comparison counts. Raw Issue bodies, PR bodies, comments, credentials, PII and arbitrary API errors are excluded. Report size is capped at 64 KiB and fails closed if the bounded representation cannot fit.

Resource comparison occurs before presentation-layer redaction. This prevents two distinct long identifiers from collapsing into the same redaction marker and creating a false conflict.

## Workflow security

Enforcement and proposed-code testing are deliberately separated.

### Trusted enforcement

`.github/workflows/agent-coordination-gate.yml` uses `pull_request_target`, but checks out and executes only `github.event.pull_request.base.sha` into a dedicated `trusted` directory. It reads the proposed PR only as GitHub metadata. The proposed Head is never checked out or executed by the trusted job.

Trusted permissions are limited to:

- `contents: read`
- `pull-requests: read`
- `issues: read`

### Proposed policy self-test

`.github/workflows/agent-coordination-self-test.yml` uses `pull_request` and checks out the proposed Head only to compile, run focused tests and check diff hygiene. It grants only `contents: read`, does not receive a live GitHub API token in its test command and cannot produce the enforcement decision.

Both workflows pin third-party Actions to immutable SHAs, disable persisted checkout credentials and scope concurrency per PR.

## Rollout

1. Introduce both workflows as advisory checks.
2. Observe real independent parallel PRs and review warnings/false positives.
3. Keep branch-protection promotion as a separate repository-owner decision.
4. Never use this gate to bypass code, migration, security or release review.

## Failure handling

Correct the source condition instead of rerunning an unchanged head:

- update Work Item lifecycle, Current PR or blockers;
- Claim, Deliver, Handoff or Reclaim correctly;
- synchronize the existing PR after Reclaim;
- close a duplicate/historical PR;
- narrow write ownership or declare read dependencies;
- declare the real stack parent;
- serialize migration/generated-resource ownership.

## Rollback

Rollback is one coordinated revert of:

- `.github/workflows/agent-coordination-gate.yml`
- `.github/workflows/agent-coordination-self-test.yml`
- `scripts/ci/agent_coordination_model.py`
- `scripts/ci/agent_coordination_core.py`
- `scripts/ci/agent_coordination_gate.py`
- `scripts/ci/agent_coordination_reclaim_adapter.py`
- `scripts/ci/agent_coordination_policy_gate.py`
- `scripts/ci/agent_coordination_path_policy.py`
- `scripts/ci/agent_coordination_entrypoint.py`
- `scripts/ci/tests/conftest.py`
- `scripts/ci/tests/test_agent_coordination_gate.py`
- `scripts/ci/tests/test_agent_coordination_reclaim_adapter.py`
- `scripts/ci/tests/test_agent_coordination_review_fixes.py`
- `scripts/ci/tests/test_agent_coordination_codex_review.py`
- `scripts/ci/tests/test_agent_coordination_entrypoint.py`
- `scripts/ci/tests/test_agent_coordination_path_semantics.py`
- `scripts/ci/tests/fixtures/agent_coordination_snapshot.json`
- this document

No schema downgrade, data repair or external cleanup is required.

## Known limitations

- PR creation is the first-implementation timing proxy; a reclaimed PR uses the server Reclaim timestamp plus later GitHub PR update.
- A GitHub PR update proves activity after Reclaim, not semantic correctness; exact-head CI and independent review remain mandatory.
- Broad/read overlaps are warnings because semantic conflicts still require human judgment.
- Metadata is point-in-time and every edit or synchronization reruns the checks.
- The gate does not mutate branch protection or GitHub Projects.
