# Nexus Exact-Head Acceptance Runbook

This runbook is the sole operational sequence for accepting PR #763 or a later canonical candidate. It does not create a second verifier. Every executable check delegates to repository-local canonical tools.

## Safety boundary

- Run only from a clean clone of the exact candidate Head.
- Use a disposable PostgreSQL database whose database name contains `test`, `acceptance`, `scratch`, `ci`, or `tmp`.
- Keep Provider, WebChat AI, voice, outbound, WhatsApp and Operations writes disabled.
- Store generated evidence outside the repository.
- Do not use production credentials, customer data or production hosts.
- A green technical result never grants production, Provider or outbound authorization.
- Any Head or tree change invalidates all prior evidence.

## 1. Freeze identity

```bash
export EXPECTED_SHA='<40-hex-candidate-sha>'
test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
test -z "$(git status --porcelain)"
git rev-parse 'HEAD^{tree}'
```

Record:

- source SHA;
- tree SHA;
- UTC start time;
- repository URL;
- operator identity;
- execution host identity without private network details.

## 2. Prepare external evidence directories

```bash
export NEXUS_ACCEPTANCE_EVIDENCE_DIR="$(mktemp -d /tmp/nexus-acceptance.XXXXXX)"
export NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR="$(mktemp -d /tmp/nexus-supply-chain.XXXXXX)"
```

Both directories must resolve outside the repository.

## 3. Fail-closed environment

```bash
export APP_ENV=test
export AUTO_INIT_DB=false
export SEED_DEMO_DATA=false
export ALLOW_DEV_AUTH=false
export PROVIDER_RUNTIME_ENABLED=false
export PROVIDER_RUNTIME_TRAFFIC_MODE=control
export PROVIDER_RUNTIME_KILL_SWITCH=true
export PROVIDER_RUNTIME_CANARY_PERCENT=0
export PRIVATE_AI_RUNTIME_ENABLED=false
export WEBCHAT_AI_ENABLED=false
export WEBCHAT_AI_AUTO_REPLY_MODE=off
export WEBCHAT_VOICE_ENABLED=false
export ENABLE_OUTBOUND_DISPATCH=false
export OUTBOUND_PROVIDER=disabled
export OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false
export WHATSAPP_NATIVE_ENABLED=false
export WHATSAPP_DISPATCH_MODE=disabled
export EMAIL_MAILBOX_SYNC_ENABLED=false
export SPEEDAF_MCP_ENABLED=false
export SPEEDAF_TRACK_QUERY_ENABLED=false
export SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
export SPEEDAF_UPDATE_ADDRESS_ENABLED=false
export SPEEDAF_CANCEL_ENABLED=false
export SPEEDAF_VOICE_CALLBACK_ENABLED=false
export OPERATIONS_DISPATCH_MODE=disabled
export OPERATIONS_DISPATCH_ADAPTER=disabled
export PYTHONPATH=backend
```

Before continuing, verify that `DATABASE_URL` points to a disposable PostgreSQL database and does not contain a production database name.

## 4. Clean dependency installation

Use a clean host or disposable container with the approved Python and Node versions.

```bash
python -m pip install --requirement backend/requirements.txt
cd webapp
npm ci --ignore-scripts
cd ..
```

Do not reuse `node_modules`, a Python virtual environment or build artifacts from another SHA.

## 5. Generate and verify external supply-chain evidence

Build the immutable image from the exact candidate. Generate an SPDX JSON SBOM and a cryptographic signature bundle using the approved builder/signing identity. Then assemble the bounded provenance outside the repository:

```bash
python scripts/release/assemble_supply_chain_evidence.py \
  --image 'ghcr.io/maximvonshaft/nexus_helpdesk@sha256:<64-hex-digest>' \
  --sbom-source '/external/path/generated-sbom.spdx.json' \
  --signature-bundle-source '/external/path/generated-cosign.bundle.json' \
  --output-dir "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR"

python scripts/qualification/supply_chain.py \
  --release \
  --evidence-dir "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR" \
  --output "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/supply-chain.json"
```

Separately verify the image signature with the approved verification identity. Store the sanitized verification result outside the repository.

## 6. Canonical repository verification

```bash
python scripts/verify_repository.py \
  --release-evidence-dir "$NEXUS_SUPPLY_CHAIN_EVIDENCE_DIR" \
  --evidence-out "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/canonical-verification.json"
```

This runs:

- static authority and residue gates;
- frontend architecture, lint, typecheck, unit and production build;
- backend compile and full regression;
- browser acceptance;
- supply-chain qualification;
- same-SHA, same-tree and clean-worktree checks before and after.

Any skipped browser, focused-only test or dirty-tree result is insufficient for final acceptance.

## 7. PostgreSQL migration rehearsal

Run against a fresh disposable database:

```bash
cd backend
python -m alembic upgrade head
python -m alembic current
python -m alembic downgrade -1
python -m alembic current
python -m alembic upgrade head
python -m alembic current
cd ..
```

Record every command, return code and final Alembic revision. A failed rollback or re-upgrade blocks acceptance.

## 8. PostgreSQL privacy and authorization

```bash
python -m pytest -q \
  backend/tests/test_support_conversations_postgres.py \
  backend/tests/test_support_conversation_privacy.py \
  backend/tests/test_support_sensitive_access.py \
  backend/tests/resilience/test_postgres_worker_recovery.py
```

The evidence must prove:

- cross-Tenant/team identifiers do not reveal existence;
- minimized lists contain no raw customer message text;
- sensitive detail requires explicit capability and bounded audit;
- concurrent claims do not duplicate jobs;
- expired processing leases are reclaimed;
- old attempts lose ownership after lease transfer.

## 9. Database capacity and hot-query snapshot

```bash
python scripts/qualification/database_capacity.py \
  --database-url "$DATABASE_URL" \
  --process-pool web:2:5:5 \
  --process-pool outbound:1:2:2 \
  --process-pool background:1:3:2 \
  --process-pool webchat-ai:1:2:1 \
  --process-pool handoff:1:2:1 \
  --output "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/database-capacity.json"
```

The snapshot must remain sanitized and exclude SQL text and query parameters. Representative workload evidence must additionally capture pool checkout wait, p50/p95/p99, throughput, errors and saturation before any PgBouncer, Redis or Worker-scale decision.

## 10. Worker fault injection

In a disposable controlled environment, exercise each queue with bounded fixtures:

1. kill a Worker immediately after claim;
2. kill it during external-call wait;
3. simulate database disconnect before terminal commit;
4. transfer an expired lease to a second Worker;
5. allow the old Worker to resume and attempt completion;
6. simulate ambiguous Provider success/timeout using a non-production stub that records idempotency keys;
7. restart all Workers and reconcile queue state.

Required evidence:

- no permanently stuck `processing` row;
- no stale completion accepted;
- no duplicate durable action;
- no duplicate external stub action for the same idempotency key;
- bounded retry/dead state and operator-visible reason codes;
- no payload or customer data in logs.

## 11. Backup and recovery rehearsal

After creating a disposable backup copy, verify attachment equality:

```bash
python scripts/qualification/local_storage_backup.py \
  --source '/staging/uploads' \
  --backup '/staging-backup/uploads' \
  --output "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/upload-backup.json"
```

Restore a disposable PostgreSQL backup and the upload backup into a new environment. Record:

- backup start/end;
- restore start/end;
- measured RPO and RTO;
- final migration revision;
- ticket/message/attachment referential checks;
- attachment read checks;
- recovery environment identity.

No production restore is part of this runbook.

## 12. Controlled deployment and rollback

Use the immutable signed image and controlled Compose profile. Run the existing controlled preflight, migration, app and isolated Workers with all external writes disabled.

Verify:

- `/healthz` and `/readyz`;
- release identity equals the frozen source SHA and image digest;
- queue business health;
- database pool state;
- storage backup freshness;
- service-specific database identities and mounts;
- Provider/outbound/voice/Operations remain fail closed.

Then exercise rollback to the previously approved immutable image in the same controlled environment and record the rollback duration and health evidence.

## 13. Infrastructure decisions

Feed sanitized database, queue, realtime and storage evidence into:

```bash
python scripts/qualification/infrastructure_decision.py \
  --database "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/database-capacity.json" \
  --queue '/external/path/queue-baseline.json' \
  --realtime '/external/path/realtime-baseline.json' \
  --storage '/external/path/storage-baseline.json' \
  --output "$NEXUS_ACCEPTANCE_EVIDENCE_DIR/infrastructure-decisions.json"
```

`CONSIDER_ADR` authorizes only a design review, not implementation or activation. Missing evidence must remain `HOLD` or `BLOCKED`.

## 14. Independent review and repository protection

The reviewer must review the frozen exact Head and record:

- reviewer identity;
- source SHA and tree SHA;
- review decision;
- unresolved findings;
- confirmation that no second UI, transport, permission, Provider, Worker or release implementation remains.

Confirm repository protection separately:

- direct main writes are restricted;
- required review cannot be bypassed silently;
- stale evidence is invalidated when Head changes;
- squash merge uses the expected exact Head.

## 15. Final identity reconciliation

```bash
FINAL_SHA="$(git rev-parse HEAD)"
FINAL_TREE="$(git rev-parse 'HEAD^{tree}')"
test "$FINAL_SHA" = "$EXPECTED_SHA"
test -z "$(git status --porcelain)"
```

Every evidence document must reference the same source SHA and tree SHA. Evidence without exact identity, sanitization status, command result and timestamp is not acceptance evidence.

## 16. Decision

The candidate can move from Draft to review only when every section passes on one unchanged Head. Merge, Provider enablement, outbound activation, deployment or production action requires a separate explicit authorization after the evidence packet is complete.
