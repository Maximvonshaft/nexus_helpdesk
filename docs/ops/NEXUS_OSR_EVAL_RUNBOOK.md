# Nexus OSR governed evaluation runbook

## Purpose

The M7 evaluation program proves Nexus OSR decision boundaries with versioned, synthetic and redacted fixtures. It is a read-only release-evidence system. It does not send customer messages, execute tools, call production providers, collect production payloads, mutate runtime defaults or write production data.

The authoritative dataset is:

- `backend/evals/nexus_osr/datasets/m7-governed-eval-v1.json`
- formal JSON Schema: `backend/evals/nexus_osr/dataset.schema.json`
- semantic and privacy validator: `backend/evals/nexus_osr/schema.py`
- deterministic runner: `backend/evals/nexus_osr/runner.py`
- CLI: `backend/scripts/run_nexus_osr_eval.py`

The runner evaluates the real `RuntimeDecision` guardrail contract. Tenant, permission and synthetic unsafe-output checks are added as eval-only boundary checks; they do not alter runtime behavior.

## Governance lifecycle

Every dataset version must declare:

- semantic version;
- owner and approver IDs;
- `draft`, `approved` or `deprecated` status;
- valid-from, review and expiry dates;
- approving Issue and approval date;
- source classification `synthetic_redacted`;
- a fail-closed runner contract declaring zero messages, zero tool executions, zero production mutations and no production payload collection.

Only an approved dataset is accepted by CI. The validator fails before the validity window, after expiry, or when the review date is overdue. A changed dataset requires a new semantic version, explicit approval metadata and review of the generated coverage report.

## Coverage contract

The machine-readable matrix declares required and actual coverage for:

- country;
- channel;
- language;
- risk level;
- scenario category;
- actor permission.

The initial dataset covers normal and ambiguous requests, positive and negative high-risk closure, MCP tracking truth, customer-claim and previous-AI negative tracking cases, customer-visible and internal Knowledge boundaries, handoff, auto-ticket, governed-tool observe-only behavior, unsafe output, tenant isolation and permission allow/deny cases.

Adding a required matrix value without a matching case makes the run fail and records the exact gap in `coverage.json`.

## Local execution

From the repository root:

```bash
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_nexus_osr_eval_schema.py \
  backend/tests/test_nexus_osr_eval_runner.py

rm -rf /tmp/nexus-osr-eval
PYTHONPATH=backend python backend/scripts/run_nexus_osr_eval.py \
  --strict \
  --max-artifact-bytes 65536 \
  --output-dir /tmp/nexus-osr-eval
```

A passing run exits `0`. `--strict` exits non-zero on a case mismatch or coverage gap. No network or database is required.

## CI evidence

`.github/workflows/osr-eval.yml` runs the same focused tests and CLI. It uploads exactly four JSON files for seven days:

- `summary.json` — dataset identity, deterministic run summary, safety contract and per-case pass/fail codes;
- `failures.json` — at most 25 bounded failure records, with a truncation flag;
- `coverage.json` — required, actual and missing matrix values;
- `manifest.json` — artifact hashes, byte counts and bounded/redacted assertions.

Each file is limited to 64 KiB. Artifacts intentionally omit customer reply bodies, evidence source objects, raw prompts, provider payloads, tool arguments/results, credentials, tracking/contact/address identifiers and provider group IDs.

## Add or change a case

1. Confirm the scenario is synthetic and contains no copied production content.
2. Add one deterministic case with country, channel, language, risk, tenant, actor permission and required permission.
3. Define the expected policy result, violation codes, customer-visible boolean and boundary decision.
4. Use empty tool arguments. Model unsafe material only through an allowed synthetic marker; never store representative secrets or identifiers.
5. Increment the dataset semantic version and update approval/review metadata.
6. Run schema, golden, coverage and artifact tests locally.
7. Review `coverage.json` for accidental gaps or low-value duplication.
8. Keep the PR Draft until exact-head focused and full regression checks are accepted.

## Failure triage

- `json_schema_violation` — structural drift or unknown fields; update the formal schema only when the contract intentionally changes.
- `dataset_review_overdue`, `dataset_expired` or approval failures — stop release use and complete governance review; do not extend dates without approval.
- case mismatch — inspect only case ID, expected/actual violation codes and mismatch fields; do not add raw payload logging.
- coverage gap — add an approved positive or negative case, or explicitly revise the required matrix with approval.
- `artifact_too_large` — reduce cardinality or safe diagnostic detail; never increase the limit to carry raw payloads.
- forbidden field/value — replace it with categorical synthetic metadata; never waive the redaction scanner for production-shaped examples.

## Rollback

This capability has no database migration, feature flag, runtime default or production data. Rollback is a revert of the eval package, dataset, CLI, tests, workflow and this runbook. Delete only generated local/CI artifacts; no data repair is required. A rollback does not weaken the existing runtime decision, customer-visible message or governed-tool boundaries.
