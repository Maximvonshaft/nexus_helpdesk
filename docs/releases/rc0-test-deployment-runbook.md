# RC0 Isolated Test Deployment Runbook

## Purpose

RC0 is a disposable, synthetic qualification fixture. It verifies that one exact
candidate can build, migrate, start, serve bounded App/WebChat journeys and tear
down in a test-only environment.

Passing RC0 means only:

```text
RC0_TEST_DEPLOYABLE=true
PRODUCTION_READY=false
FULL_OSR_AUTOMATION=NO_GO
```

It does not authorize production traffic, Provider execution, real outbound,
production data, public DNS changes, a release tag or a controlled deployment.

## Relationship to canonical deployment

The sole release topology is `deploy/docker-compose.controlled.yml`, with the
optional local database overlay
`deploy/docker-compose.controlled-postgres.yml`.

`deploy/docker-compose.candidate.yml` is now only a thin compatibility alias to
the controlled topology. It contains no candidate-specific services or sidecars.

RC0 may continue to use `deploy/docker-compose.rc-test.yml` because it is an
isolated test fixture, not an alternative product or release topology. It must:

- use synthetic data and its own PostgreSQL volume;
- use its own internal network and uploads volume;
- expose only a loopback test gateway;
- mount no production path or credential;
- keep Provider, AI, voice, outbound, WhatsApp, SpeedAF and Operations writes disabled;
- remove its containers, network and volumes after qualification.

## Prerequisites

- Docker Engine and Compose v2;
- exact candidate checkout with a clean tree;
- free loopback port configured by the RC environment;
- locked frontend dependencies;
- no production secret.

## Prepare the isolated environment

```bash
cp deploy/.env.rc-test.example deploy/.env.rc-test
```

Replace synthetic placeholders only. Candidate identity must agree:

```text
RC_IMAGE_TAG = IMAGE_TAG
GIT_SHA = FRONTEND_BUILD_SHA = exact candidate commit
```

The RC local image tag is not a production reference. Release evidence later
requires an immutable registry Digest and external SBOM/provenance/signature.

Keep these controls disabled:

```text
PROVIDER_RUNTIME_ENABLED=false
PROVIDER_RUNTIME_CANARY_PERCENT=0
PROVIDER_RUNTIME_KILL_SWITCH=true
PRIVATE_AI_RUNTIME_ENABLED=false
WEBCHAT_AI_ENABLED=false
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
WHATSAPP_NATIVE_ENABLED=false
WHATSAPP_DISPATCH_MODE=disabled
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
OPERATIONS_DISPATCH_MODE=disabled
OPERATIONS_DISPATCH_ADAPTER=disabled
```

## Execute

```bash
cd /path/to/nexus_helpdesk
npm --prefix webapp ci
RC_RUN_BROWSER_SMOKE=true \
  bash scripts/release/run_rc_test_candidate.sh
```

The script is expected to perform only isolated work:

1. validate RC environment safety;
2. build the exact local candidate;
3. validate the RC Compose fixture;
4. start isolated PostgreSQL, App, gateway and dedicated Workers;
5. migrate the isolated database;
6. create only synthetic RC users/data;
7. run identity, health, login, WebChat, side-effect and browser smoke;
8. collect bounded synthetic evidence;
9. tear down the isolated project and volumes;
10. bind the manifest to the exact source/image used.

RC artifacts may be written to ignored `artifacts/rc-test/`; they are not release
SBOM, provenance or signature evidence.

## Test-server execution

Run in a dedicated test directory or VM, never in the live production checkout.
Do not reuse:

- production Compose project name;
- production PostgreSQL or uploads;
- production Nginx target;
- production Provider/AI/voice/WhatsApp credentials;
- production network attachments.

`KEEP_RC_STACK=true` is diagnostic-only and must not turn RC0 into a long-lived
environment.

## Failure handling

When a gate fails:

1. preserve bounded failure evidence;
2. reproduce the exact failing step;
3. fix the demonstrated root cause in the canonical implementation;
4. add a regression test;
5. rerun the complete RC chain on one unchanged Head.

Do not use RC0 to bypass `scripts/verify_repository.py` or the exact-head runbook.

## Teardown

```bash
COMPOSE_PROJECT_NAME=<rc-project-name> \
docker compose \
  --env-file deploy/.env.rc-test \
  -f deploy/docker-compose.rc-test.yml \
  down --volumes --remove-orphans
```

Then confirm no RC container remains:

```bash
COMPOSE_PROJECT_NAME=<rc-project-name> \
docker compose \
  --env-file deploy/.env.rc-test \
  -f deploy/docker-compose.rc-test.yml \
  ps -q --all
```

A later candidate Head requires a complete rerun.
