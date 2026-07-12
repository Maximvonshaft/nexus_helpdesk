# RC0 controlled test deployment runbook

## Purpose

This runbook produces one isolated Nexus OSR test candidate from an exact Git
commit. It proves build, migration, startup, core WebChat/operator flow,
browser rendering, worker health, side-effect shutdown, and teardown.

Passing this runbook means only:

```text
RC0_TEST_DEPLOYABLE=true
PRODUCTION_READY=false
FULL_OSR_AUTOMATION=NO_GO
```

It does not authorize production traffic, Provider execution, real outbound,
production-data mutation, public DNS changes, or a release tag.

## Authority and remote skills

Work Item: #626.

Remote skill versions are governed by
`docs/ai/remote-skills-registry.yaml`. For this release:

- Superpowers establishes investigation and verification discipline.
- Deployment Validation supplies isolation, recovery, observability, and
  simplicity checks.
- GitHub Actions Hardening supplies trigger, permission, immutable action, and
  expression-injection checks.
- SecPriv supplies source/sink and personal-data review.
- Anthropic Webapp Testing supplies browser reconnaissance and Playwright
  acceptance.

Nexus Issue #489, current code, tests, and #626 acceptance remain authoritative.

## Why this path is separate

The existing `deploy/docker-compose.candidate.yml` is a side-by-side
production-candidate topology. It can join a production runtime network, mount
production upload paths, reuse runtime tokens, and enable WhatsApp/Provider
features through its environment.

RC0 instead uses `deploy/docker-compose.rc-test.yml`:

- its own PostgreSQL volume and database;
- an internal project network for App, PostgreSQL and Workers;
- its own upload and backup volumes;
- no production mounts;
- no production network;
- a credential-free Nginx gateway on a project-local edge network that publishes
  only `127.0.0.1:${RC_APP_PORT}`;
- no direct App port or App edge-network membership;
- no WhatsApp sidecar or session;
- Provider traffic, outbound, Speedaf writes, and Operations Dispatch disabled.

The Nginx gateway does not load the RC environment file, business credentials,
Provider tokens, customer data, or runtime secrets. Its only function is to
expose the internal App to the local test host.

## Local or test-server prerequisites

- Docker Engine with Compose v2.
- Git checkout at the exact candidate commit.
- Free local port `18083`.
- Sufficient disk for one image and isolated PostgreSQL volume.
- Chrome available when browser smoke is enabled.
- Locked webapp dependencies installed with `cd webapp && npm ci`.

No production secret is required.

## Prepare the RC environment

```bash
cp deploy/.env.rc-test.example deploy/.env.rc-test
```

Replace every `<...>` placeholder. Use a unique image tag containing the exact
40-character commit SHA. The following identities must agree:

```text
RC_IMAGE_TAG = IMAGE_TAG
GIT_SHA = FRONTEND_BUILD_SHA = exact candidate commit
```

Do not change these safety values:

```text
PROVIDER_RUNTIME_ENABLED=false
PROVIDER_RUNTIME_CANARY_PERCENT=0
PROVIDER_RUNTIME_KILL_SWITCH=true
PRIVATE_AI_RUNTIME_ENABLED=false
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

## Execute the complete chain

```bash
cd /path/to/nexus_helpdesk
npm --prefix webapp ci
RC_RUN_BROWSER_SMOKE=true \
  bash scripts/release/run_rc_test_candidate.sh
```

The script performs:

1. environment safety validation;
2. exact-SHA image build;
3. Compose validation without writing a rendered secret-bearing config;
4. isolated PostgreSQL startup;
5. `alembic upgrade head`;
6. creation of a synthetic RC-only admin;
7. App, credential-free loopback Nginx gateway and explicit Worker startup;
8. condition-based App, gateway and Worker health waits;
9. exact runtime identity checks;
10. invalid and valid login checks;
11. synthetic WebChat init, send, poll, and operator read;
12. in-container side-effect configuration proof;
13. Playwright login/protected-route smoke through the loopback gateway;
14. teardown with isolated volume and network removal;
15. exact candidate manifest validation.

Evidence is written to `artifacts/rc-test/` and is ignored by Git.

## Controlled server execution

Run the same commands in a dedicated test directory or test VM. Do not run RC0
inside the live production checkout. Do not reuse:

- the production Compose project name;
- production PostgreSQL;
- production uploads;
- production WhatsApp sessions;
- production runtime token mounts;
- the public production Nginx target.

The included `nginx-rc` service binds only to loopback. Access it through an SSH
tunnel or an explicitly controlled test-only ingress after the local smoke
passes. Do not expose `app-rc` directly and do not join it to an external or
production network.

## Failure handling

The script collects only bounded synthetic logs and runs the repository artifact
scanner. It always tears down the isolated stack unless
`KEEP_RC_STACK=true` is explicitly set for diagnosis.

When a gate fails:

1. keep the failure evidence;
2. reproduce the exact failing step;
3. trace the component boundary;
4. change only the demonstrated blocker;
5. add a failing regression test before the fix;
6. rerun the complete chain.

Do not perform opportunistic cleanup or merge unrelated PRs.

## Rollback

RC0 has no production cutover. Its rollback is complete removal:

```bash
COMPOSE_PROJECT_NAME=<rc-project-name> \
docker compose \
  --env-file deploy/.env.rc-test \
  -f deploy/docker-compose.rc-test.yml \
  down --volumes --remove-orphans
```

Verify no RC containers remain:

```bash
COMPOSE_PROJECT_NAME=<rc-project-name> \
docker compose \
  --env-file deploy/.env.rc-test \
  -f deploy/docker-compose.rc-test.yml \
  ps -q --all
```

The command must return no container IDs.

## Acceptance evidence

Required files:

- `candidate-manifest.json`;
- `healthz.json`;
- `readyz.json`;
- `http-core-smoke.json`;
- `side-effect-safety.json`;
- `browser-smoke.txt`;
- `compose-ps-healthy.txt`;
- `migration.txt`;
- `teardown.txt`;
- `artifact-scan.json`.

The manifest is authoritative only for the exact image and source SHA recorded
inside it. A later commit requires a complete rerun.
