# Nexus Exact-Head Acceptance Runbook

This is the sole operational sequence for accepting PR #763 or a later
canonical candidate. `scripts/verify_repository.py` is the only final verifier.
The qualification scripts it invokes are bounded subroutines, not alternative
release authorities.
The final evidence manifest is evaluated by
`scripts/qualification/exact_head_acceptance.py`;
`scripts/qualification/postgres_acceptance.py` and
`scripts/qualification/infrastructure_decision.py` remain subordinate to that
single verifier sequence.

## Safety boundary

- Run from a clean clone of one exact candidate Head.
- Use a disposable PostgreSQL database whose name contains `test`,
  `acceptance`, `scratch`, `ci` or `tmp`.
- Prefer a local/isolated PostgreSQL host. A remote disposable database requires
  `NEXUS_ACCEPTANCE_REMOTE_DATABASE_CONFIRM=I_UNDERSTAND_DISPOSABLE_ONLY` and the
  explicit verifier flag.
- Keep Provider, WebChat AI, voice, outbound, WhatsApp, email synchronization,
  SpeedAF writes and Operations Dispatch disabled.
- Use synthetic test data only.
- Store all generated evidence outside the repository.
- Never use production credentials, customer records, production databases,
  production uploads or production hosts.
- A passing result never grants production, Provider or outbound authorization.
- Any source SHA, tree SHA or immutable-input change invalidates all evidence.

## 1. Freeze the candidate

```bash
export EXPECTED_SHA='<40-hex-candidate-sha>'
test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
test -z "$(git status --porcelain)"
export EXPECTED_TREE="$(git rev-parse 'HEAD^{tree}')"
```

Do not add commits after evidence collection begins.

## 2. Prepare external directories

```bash
export NEXUS_ACCEPTANCE_EVIDENCE_DIR="$(mktemp -d /tmp/nexus-acceptance.XXXXXX)"
export NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR="$(mktemp -d /tmp/nexus-supply-chain.XXXXXX)"
```

Both paths must resolve outside the candidate repository and must not be
symbolic links.

## 3. Prepare immutable image evidence

Build one image from the exact candidate and reference it by Digest:

```text
ghcr.io/maximvonshaft/nexus_helpdesk@sha256:<64-hex-digest>
```

Generate outside the repository:

```text
$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR/sbom.spdx.json
$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR/cosign.bundle.json
```

Then assemble provenance:

```bash
python scripts/release/assemble_supply_chain_evidence.py \
  --image 'ghcr.io/maximvonshaft/nexus_helpdesk@sha256:<digest>' \
  --sbom-source "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR/sbom.spdx.json" \
  --signature-bundle-source "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR/cosign.bundle.json" \
  --output-dir "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR"
```

Verify the image signature with the approved verification identity and create:

```text
$NEXUS_ACCEPTANCE_EVIDENCE_DIR/signature-verification.json
```

Required schema and invariants:

```json
{
  "schema": "nexus.signature-verification.v1",
  "status": "pass",
  "source_sha": "<EXPECTED_SHA>",
  "tree_sha": "<EXPECTED_TREE>",
  "verified": true,
  "image": "ghcr.io/...@sha256:<digest>",
  "verification_identity": "<bounded identity>",
  "sanitized": true,
  "contains_customer_data": false,
  "contains_secrets": false
}
```

Do not include certificates, credentials or unbounded signer output in the
acceptance packet; keep the cryptographic bundle in the supply-chain directory.

## 4. Prepare environment-generated evidence

The final verifier automatically creates these files:

```text
supply-chain.json
migration-rehearsal.json
postgres-qualification.json
database-capacity.json
upload-backup.json
infrastructure-decisions.json
acceptance-manifest.json
acceptance-qualification.json
canonical-verification.json
```

Before running it, the controlled environment or independent reviewer must
create the following bounded JSON files in
`$NEXUS_ACCEPTANCE_EVIDENCE_DIR`.

### Representative workload

```text
representative-workload.json
schema: nexus.representative-workload.v1
```

It must contain a positive `sample_count` and numeric metrics for:

- `latency_p50_ms`;
- `latency_p95_ms`;
- `latency_p99_ms`;
- `throughput_per_second`;
- `error_rate_percent`;
- `pool_checkout_wait_p95_ms`;
- `worker_busy_ratio_percent`;
- `cpu_headroom_percent`.

### Worker fault injection

```text
worker-fault-injection.json
schema: nexus.worker-fault-injection.v1
```

All scenarios must pass:

- `kill_after_claim`;
- `kill_during_external_wait`;
- `database_disconnect_before_commit`;
- `lease_transfer`;
- `stale_worker_resume`;
- `ambiguous_external_result` against a non-production idempotency stub;
- `full_worker_restart`.

The document must explicitly prove no stuck processing, no stale completion,
no duplicate durable action, no duplicate external action, and bounded
retry/dead state.

### Backup and recovery rehearsal

```text
recovery-rehearsal.json
schema: nexus.recovery-rehearsal.v1
```

It must be based on a disposable restore and record:

- database restore success;
- uploads restore success;
- referential checks;
- attachment reads;
- measured `rpo_seconds` and `rto_seconds`;
- no production restore.

### Controlled deployment and rollback

```text
controlled-deployment.json
schema: nexus.controlled-deployment-acceptance.v1

rollback-rehearsal.json
schema: nexus.rollback-rehearsal.v1
```

Controlled deployment evidence must prove:

- immutable image identity matches the candidate;
- `/healthz` and `/readyz` pass;
- queue, database pool and storage readiness pass;
- service-specific database identities remain isolated;
- all external writes remain fail closed.

Rollback evidence must use a previously approved immutable release identity and
prove health after rollback with a measured duration.

### Queue, realtime and storage baselines

```text
queue-baseline.json       schema: nexus.queue-baseline.v1
realtime-baseline.json    schema: nexus.realtime-baseline.v1
storage-baseline.json     schema: nexus.storage-baseline.v1
```

Each must have `status=pass`, a positive `sample_count`, bounded sanitized data,
and the fields consumed by
`scripts/qualification/infrastructure_decision.py`.

### Independent review

```text
independent-review.json
schema: nexus.independent-review.v1
```

It must include:

- a non-empty reviewer identity;
- `independent=true`;
- `decision=approved`;
- `unresolved_findings=[]`;
- explicit confirmation that no second UI, Transport, permission, Provider,
  Worker or release authority remains.

The author of the implementation cannot manufacture this evidence as a
substitute for an independent review.

### Repository protection

```text
repository-protection.json
schema: nexus.repository-protection.v1
```

It must prove:

- direct writes to `main` are restricted;
- independent review is required;
- Head changes invalidate stale evidence;
- expected-Head merge is enforced;
- administrator bypass is restricted.

## 5. Prepare disposable PostgreSQL and uploads

Set a disposable PostgreSQL URL. The URL is passed to the PostgreSQL acceptance
subprocess through the environment and is never written to evidence.

```bash
export DATABASE_URL='postgresql+psycopg://.../nexus_acceptance'
```

Prepare two different synthetic upload directories:

```bash
export ACCEPTANCE_UPLOAD_SOURCE='/tmp/nexus-upload-source'
export ACCEPTANCE_UPLOAD_BACKUP='/tmp/nexus-upload-backup'
```

The backup directory must already contain a complete copy of the source. The
verifier compares content manifests and writes a bounded marker only when they
are equal.

## 6. Run the one final acceptance command

```bash
python scripts/verify_repository.py \
  --expected-sha "$EXPECTED_SHA" \
  --release-evidence-dir "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR" \
  --acceptance-evidence-dir "$NEXUS_ACCEPTANCE_EVIDENCE_DIR" \
  --acceptance-database-url "$DATABASE_URL" \
  --acceptance-upload-source "$ACCEPTANCE_UPLOAD_SOURCE" \
  --acceptance-upload-backup "$ACCEPTANCE_UPLOAD_BACKUP" \
  --evidence-out "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/canonical-verification.json"
```

For an explicitly approved remote disposable database, add:

```bash
export NEXUS_ACCEPTANCE_REMOTE_DATABASE_CONFIRM=I_UNDERSTAND_DISPOSABLE_ONLY
```

and pass:

```text
--allow-remote-acceptance-database
```

Final acceptance deliberately rejects:

- `--static-only`;
- `--focused-backend`;
- `--skip-browser`;
- missing release evidence;
- missing disposable PostgreSQL URL;
- missing upload source/backup;
- any evidence directory inside the repository.

## 7. What the final command executes

On one unchanged Head, the verifier performs:

1. candidate SHA/tree/immutable-input capture and clean-tree check;
2. zero-duplicate/zero-residue static gates;
3. service-authority AST qualification;
4. actual FastAPI method + normalized-path qualification;
5. Alembic Head inventory;
6. clean frontend installation;
7. frontend architecture, Transport, dependency, lint, typecheck, unit and build;
8. Python compilation;
9. complete backend regression;
10. Playwright browser acceptance;
11. disposable PostgreSQL upgrade → downgrade → re-upgrade;
12. PostgreSQL privacy, authorization and Worker lease tests;
13. sanitized connection-budget and hot-query snapshot;
14. upload source/backup manifest equality;
15. evidence-based infrastructure decisions;
16. SHA-256 Manifest assembly for all 17 required artifacts;
17. semantic qualification of every artifact;
18. final SHA/tree/hash and clean-worktree reconciliation.

Every failed subprocess produces a structured failure stage in
`canonical-verification.json`. Command output is not copied into the evidence
packet.

## 8. Acceptance packet rules

`acceptance-manifest.json` uses schema
`nexus.exact-head-acceptance-manifest.v1` and binds every required artifact by:

- exact relative path;
- expected Schema;
- passing status;
- SHA-256 file hash;
- exact source SHA and tree SHA at packet level.

The qualification fails when:

- a file is missing, empty, too large or symbolic-linked;
- a path escapes the evidence directory;
- a file changes after Manifest assembly;
- a Schema or status is wrong;
- an artifact references another source/tree;
- sanitization boundaries are absent;
- Review or repository protection is missing;
- any required fault, recovery, health or rollback invariant is false.

## 9. Decision

The candidate may move from Draft to review only when:

```text
canonical-verification.json: status=pass
acceptance-qualification.json: status=pass
same_identity=true
browser_executed=true
release_evidence_checked=true
acceptance_evidence_checked=true
```

A passing acceptance packet still sets:

```text
production_authorized=false
provider_enablement_authorized=false
outbound_enablement_authorized=false
```

Merge, deployment, Provider enablement, outbound activation, credential action
or production data operation remains a separate explicit authorization.