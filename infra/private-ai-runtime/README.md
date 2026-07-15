# Private AI Runtime deployment authority

This directory is the repository boundary for the reproducible Private AI Runtime owned by Work Item #591.

This directory contains the deployment-manifest contract and the reviewed Nexus live-voice Runtime source. It does **not** contain host inventory, model files, credentials, generated rollback packages, or production customer data.

`live_voice_runtime/app.py` is the canonical source for the voice media edge. It owns VAD, STT and TTS only. Every customer-visible reply is created by the Nexus Provider Runtime through the authenticated live-voice turn endpoint, using the same conversation history, tools, knowledge and reply contract as text WebChat. During TTS playback, microphone input is ignored to prevent speaker echo from terminating the response.

Production runs `nexus-live-voice-media.service`. Its non-secret endpoint settings live in `/etc/nexus/live_voice_media.env`; its shared callback credential is loaded by systemd from `/etc/nexus/secrets/live_voice_token`. The retired demo service is not a fallback and must not coexist on port 8060.

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
- commands are shell strings rather than explicit argv arrays, or any argv token names a POSIX, PowerShell or CMD shell interpreter;
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
