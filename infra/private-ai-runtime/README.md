# Private AI Runtime deployment authority

This directory is the repository boundary for the reproducible Private AI Runtime owned by Work Item #591.

The first slice contains a strict deployment-manifest contract and validator. It does **not** contain a deployable candidate, live host inventory, service implementation, model files, credentials, generated rollback package, or production acceptance evidence.

## Authority boundaries

- `nexus.private_ai_runtime.deployment_manifest.v1` binds one release to exact Git source, immutable artifacts, model revisions, digest-pinned images, service definitions, host requirements, external secret references, acceptance commands, rollback material and drift checks.
- `nexus.ai_runtime.capabilities.v1` remains owned by #586. The deployment manifest references its path and SHA-256; it does not redefine generation, retrieval or voice semantics.
- Provider traffic selection remains owned by #582.
- PostgreSQL Knowledge remains authoritative. Qdrant data is derived mutable state and must be declared with `authoritative=false`.
- Release GO/NO-GO remains owned by #533.

## Safety properties

The validator fails closed when:

- source, tree, artifact, model, service or rollback identities are not exact hashes;
- an image uses a mutable tag instead of an OCI digest;
- mutable data or secret paths overlap an immutable release root;
- derived Runtime state claims authority;
- service-definition hashes do not match their immutable artifacts;
- secrets appear as inline fields or command arguments;
- commands are shell strings instead of explicit argv arrays;
- rollback is destructive, drift is not fail-closed, or required acceptance checks are missing;
- the manifest exceeds 1 MiB, contains unknown fields or violates bounded identifiers and paths.

Validation reports contain only reason codes, structural paths and cryptographic identities. Manifest values, secret references and command arguments are never echoed into findings.

## Local validation

```bash
python scripts/ci/check_private_ai_runtime_deployment_manifest.py \
  --manifest /path/to/deployment-manifest.json \
  --output artifacts/private-ai-runtime/deployment-manifest-validation.json
```

Exit codes:

- `0`: contract-valid manifest;
- `1`: bounded validation finding;
- `2`: result artifact could not be written.

Contract validity is necessary but not sufficient for deployment readiness. A real candidate must still be reconstructed from reviewed source, built on a clean isolated host, accepted for generation/retrieval/voice/observability, backed up and restored, rolled back, drift-checked and rerun under the exact #533 candidate.

## Prohibited content

Never commit:

- tokens, passwords, private keys, credential values or Authorization headers;
- live internal addresses or customer/provider payloads;
- mutable image tags;
- unreviewed server-local scripts represented as repository authority;
- model binaries, Qdrant data or generated backup archives unless a separate reviewed artifact policy explicitly authorizes them.
