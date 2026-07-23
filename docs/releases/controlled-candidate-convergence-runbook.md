# Controlled Candidate Convergence

## Authority and scope

This runbook governs the immutable software candidate that may be deployed to a
controlled Nexus production host.

It does **not** fabricate or authorize Provider, LiveKit, Carrier, DID, SMTP,
WhatsApp, Speedaf or Operations credentials. Customer-facing effects remain
fail-closed until the production activation authority accepts real
capability-specific evidence.

The active authorities are:

- `.github/workflows/canonical-acceptance.yml`;
- `.github/workflows/controlled-candidate-convergence.yml`;
- `scripts/release/run_rc_test_candidate.sh`;
- `scripts/release/build_controlled_candidate_manifest.py`;
- `scripts/deploy/validate_controlled_server_preflight.py`;
- `deploy/docker-compose.controlled.yml`;
- `docs/runbooks/production-activation.md`.

## 1. Automatic exact-main publication

Every push to `main` runs **Canonical Acceptance**.

Only when that exact `main` SHA completes successfully does
`controlled-candidate-convergence` run. It rejects:

- pull-request workflow runs;
- failed or cancelled acceptance runs;
- non-`main` branches;
- a triggering SHA that is no longer the current `origin/main`;
- a dirty checkout.

There is no request JSON, issue-comment command, bridge workflow or manual
publication bypass.

## 2. One-build candidate chain

The candidate workflow reuses the existing RC image build. It does not build a
second image.

The same binary is then:

1. started in the isolated RC topology;
2. exercised through browser smoke;
3. checked for runtime imports and Gunicorn configuration;
4. scanned with Trivy;
5. converted to CycloneDX SBOM;
6. checked for license and compliance findings;
7. pushed to GHCR;
8. pulled back and compared with the local image ID;
9. bound to PostgreSQL recovery evidence;
10. attested with GitHub build provenance.

A failure before publication produces bounded, sanitized evidence and blocks
all subsequent publication steps.

## 3. Final evidence artifact

A successful run publishes:

```text
controlled-candidate-<main-sha>
```

The artifact contains, among other bounded evidence:

```text
controlled-candidate-manifest.json
canonical-acceptance-receipt.json
controlled-candidate.env
registry-publish-receipt.json
release-image-manifest.json
release-image-compliance-binding.json
recovery-evidence.json
artifact-scan.json
```

`canonical-acceptance-receipt.json` binds the candidate to the successful
Canonical Acceptance run ID, URL and exact `main` SHA.

`controlled-candidate.env` contains only non-secret immutable identity values:

```text
CONTROLLED_IMAGE
IMAGE_TAG
GIT_SHA
FRONTEND_BUILD_SHA
EXPECTED_MIGRATION_HEAD
BUILD_TIME
APP_VERSION
ACTIVATION_EVIDENCE_SOURCE_SHA
ACTIVATION_EVIDENCE_IMAGE_DIGEST
```

Copy these values into the controlled host environment. Never commit real
database passwords, JWT keys, Provider keys or Carrier credentials.

## 4. Complete the host environment

Start from `deploy/.env.controlled.example` or the local-PostgreSQL example.

Replace every placeholder with a real value. Required host facts include:

- six distinct PostgreSQL service identities;
- application signing and metrics secrets;
- approved public origins and proxy addresses;
- uploads and backup paths;
- an immutable GHCR digest from the final candidate artifact.

Provider, WebChat AI, Voice, outbound and Operations flags must remain disabled
during controlled deployment.

## 5. Validate before starting containers

External PostgreSQL example:

```bash
python scripts/deploy/validate_controlled_server_preflight.py \
  --env-file deploy/.env.controlled \
  --compose-file deploy/docker-compose.controlled.yml \
  --manifest /secure/evidence/controlled-candidate-manifest.json \
  --expected-database-host <approved-host> \
  --expected-database-port 5432 \
  --expected-domain <approved-domain> \
  --check-host-paths \
  --output /secure/evidence/controlled-preflight.json
```

The preflight rejects mutable images, wrong SHA or migration identity, duplicate
database users, placeholder secrets, missing paths and enabled external
effects.

## 6. Controlled deployment

After preflight passes:

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  up -d migrate-controlled app-controlled \
    worker-background-controlled worker-webchat-ai-controlled \
    worker-handoff-snapshot-controlled
```

Verify:

- `/healthz`;
- `/readyz`;
- exact source, frontend, image and migration identity;
- queue health;
- storage backup equality or remote storage;
- PostgreSQL pool budget;
- zero Provider and external write traffic.

## 7. Provider Canary

Provider Canary is a separate profile. It permits only bounded model-provider
traffic between 1% and 25%.

During Canary, customer WebChat AI, Voice, outbound and Operations effects
remain disabled. The kill switch and rollback path must be verified before
promotion.

## 8. Full production activation

Follow `docs/runbooks/production-activation.md`.

Full activation requires:

- a successful controlled deployment;
- exact candidate-bound HTTPS production evidence;
- real Provider configuration;
- separate evidence for each enabled capability;
- LiveKit/SIP/STT/TTS evidence when Voice is enabled;
- `production_authorized=true` in **System Runtime → Release & Activation**.

No UI action or environment toggle can override a failed readiness collector.

## 9. Rollback

Keep the prior immutable image digest and prior environment snapshot.

On degradation:

1. set `PROVIDER_RUNTIME_KILL_SWITCH=true`;
2. disable affected capability flags;
3. reapply the controlled profile;
4. restore the prior immutable image;
5. preserve failure evidence before another activation attempt.

Never use a mutable image tag or unbounded Docker prune.
