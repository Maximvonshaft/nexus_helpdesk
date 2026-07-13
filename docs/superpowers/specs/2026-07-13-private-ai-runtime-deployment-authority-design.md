# Private AI Runtime deployment authority design

## Problem

Issue #591 is factually valid: current main integrates with a Private AI Runtime but does not contain the complete reviewed source needed to reproduce the deployed Runtime on a replacement host. Server-local evolution, mutable image/model references and absent rebuild/rollback authority prevent repository-only recovery proof.

## First-slice objective

Establish a reviewable contract before importing or reconstructing live host assets. The contract must make incomplete or ambiguous deployment evidence fail closed without claiming that the Runtime is already reproducible.

## Chosen boundary

Use `infra/private-ai-runtime/` as an explicitly isolated package inside this repository. This is reversible: a future dedicated repository can preserve the same manifest schema and release identity. No live host content is copied by this slice.

## Contract

`nexus.private_ai_runtime.deployment_manifest.v1` has fifteen required top-level fields and rejects all unknown fields. It binds source identity, capability-contract identity, host requirements, immutable artifacts, derived mutable state, external secret references, images, models, services, acceptance, rollback and drift.

### Invariants

1. Git source and tree identities are exact lowercase 40-character SHAs.
2. File, model, capability-contract, service and rollback identities are exact SHA-256 values.
3. Images use `@sha256:` references; mutable tags are invalid.
4. Mutable paths, secret files, rollback packages and drift results remain outside the immutable release root.
5. Mutable Runtime state is never authoritative.
6. A service definition is present in the immutable artifact set and its hash matches that artifact.
7. Secrets are references only. Inline secret-shaped fields and command-line token/password/API-key arguments are invalid.
8. Acceptance and rollback are argv arrays, not shell strings.
9. Rollback is non-destructive and targets a different release.
10. Required acceptance checks cover GPU placement, generation, retrieval, voice, metrics and model identity.
11. Drift is fail-closed.
12. Input is bounded to 1 MiB; output contains reason codes and structural paths, never source values.

## Non-goals

- no deployment runner;
- no live A10 access or inventory;
- no model download or replacement;
- no Qdrant mutation or restore;
- no Provider traffic change;
- no production secret, endpoint or customer/provider payload;
- no workflow graph change;
- no claim that #591 is complete.

## Verification strategy

TDD covers:

- a valid manifest;
- unknown keys;
- non-exact source hashes;
- mutable image tags;
- immutable/mutable overlap;
- derived-state authority escalation;
- non-argv commands;
- destructive rollback;
- capability-schema drift;
- recursive inline secret fields;
- duplicate IDs and artifact paths;
- redacted bounded results;
- JSON Schema/validator top-level convergence;
- CLI exit semantics;
- service-definition hash mismatch;
- embedded secret command arguments;
- oversized input.

The focused test command is:

```bash
python -m pytest -q scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
```

Additional verification:

```bash
python -m py_compile \
  scripts/ci/check_private_ai_runtime_deployment_manifest.py \
  scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
```

Repository CI and independent review remain required on the exact PR head.
