# OSR Release Profile Core Contract

## Purpose

`backend/app/services/nexus_osr/release_profiles.py` is the single framework-light authority for OSR release-profile semantics. It prevents process health, an empty queue or a configured Provider from being interpreted as end-to-end business readiness.

This module is a contract foundation. It performs no database, network, filesystem, Provider, outbound or deployment operation and is not wired to `/healthz`, `/readyz` or an Admin API by this slice.

## Schema

The profile schema is:

```text
nexus.osr.release-profile.v1
```

Supported profiles:

- `development`
- `shadow`
- `pilot`
- `full_osr`

Every profile declares every v1 capability exactly once as `required`, `optional` or `forbidden`. Unknown profiles, capabilities and evidence states fail closed.

## Requirement semantics

### Required

- missing, disabled or failed → `not_ready`
- degraded → `degraded`
- ready → no finding

### Optional

- missing, disabled or ready → no finding
- degraded or failed → `degraded`

### Forbidden

- missing or disabled → no finding
- ready, degraded or failed → `not_ready`

Result precedence is:

```text
not_ready > degraded > ready
```

Reason codes are generated from fixed capability, requirement and state values. They are unique, sorted and low-cardinality. Evaluation output contains only:

- schema version;
- profile name;
- overall status;
- fixed reason codes.

It never returns caller-supplied details or evidence values.

## Capability coverage

The v1 registry includes local health, migration and storage identity; Tenant, Tracking Truth, Knowledge, escalation, Worker/progress and queue authority; Provider/canary, Dispatch/acknowledgement and external writes; observability, recovery and resilience; and external AI Runtime/RAG/deployment/Voice identity.

A profile may require a capability before its collector is implemented. In that case the later aggregate must report it missing and remain fail closed. A collector must not weaken the requirement matrix.

## Profile intent

- `development` requires core local identity and forbids Provider/Dispatch/external-write authority.
- `shadow` requires read-only truth, worker/progress, queue, observability and AI Runtime contract evidence while forbidding Dispatch and external writes.
- `pilot` requires all operating capabilities except Voice, which remains optional in v1.
- `full_osr` requires every capability.

These declarations do not authorize enabling any capability. They only define how evidence is evaluated.

## Configuration fingerprint

`safe_configuration_fingerprint()` produces a lowercase SHA-256 digest from bounded JSON-like configuration.

Controls:

- deterministic key ordering;
- maximum 64 mapping entries and 64 sequence items;
- maximum depth 4;
- maximum string length 512 and key length 128;
- finite numeric values only;
- unsupported objects fail closed;
- snake, kebab, space and camel-case key segmentation;
- redaction for terminal secret, password, authorization, credential, cookie, payload and token keys, plus API/private/access/signing-key pairs;
- plural token-count parameters such as `max_tokens`, `input_tokens`, `output_tokens`, `total_tokens`, `context_tokens`, `usage_tokens` and `budget_tokens` remain non-secret so their drift changes the digest.

Consequently, changing a secret value does not change the digest, while changing non-secret configuration does. The digest is an identity aid, not proof that the source configuration is safe.

## Consumer rules

Future #549 collectors and API/runtime integration must:

1. consume this profile registry rather than defining another matrix;
2. translate only accepted bounded collector states into `CapabilityState`;
3. treat missing collector authority as missing evidence;
4. keep normalized Settings as configuration authority;
5. never claim Pilot or Full OSR ready without accepted #546 Tenant and #567 Dispatch authority;
6. preserve read-only, Tenant-safe and redacted collection;
7. keep deployment rollout and mandatory-setting compatibility in a separate reviewed slice.

## Non-authority

Merging this core contract does not:

- activate a release profile;
- change `/healthz` or `/readyz`;
- enable Providers, Dispatch, Voice or external writes;
- prove recovery, resilience or final M12 readiness;
- authorize deployment, release tags, production configuration or data mutation.

## Verification

Run:

```bash
PYTHONPATH=backend python -m unittest -v backend.tests.test_nexus_osr_release_profiles
python -m py_compile \
  backend/app/services/nexus_osr/release_profiles.py \
  backend/tests/test_nexus_osr_release_profiles.py
```

The dedicated `osr-release-profile-contract` Workflow runs the same contract on every relevant PR and main change.

## Rollback

Revert the additive module, test, Workflow and documentation. No database, runtime, deployment or external cleanup is required.
