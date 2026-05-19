# Codex App-Server Reply Runbook

## Objective

Validate and operate the private Codex reply bridge path without changing production WebChat traffic.

The current implementation has two layers:

1. Probe: `scripts/probe_codex_app_server_reply.sh`
2. Private sidecar: `tools/codex-reply-bridge/sidecar.py`

The sidecar can run in `disabled`, `stub`, or `upstream` mode. `stub` is only for local contract testing. `upstream` forwards to a later Codex app-server adapter endpoint and still normalizes the response through the Nexus strict parser.

## Required operator inputs

You need one of the following on the server or local dev host:

- a working private upstream Codex app-server adapter endpoint;
- a bridge shared token file for Nexus-to-sidecar authentication;
- an upstream token file if the upstream adapter requires authentication;
- the Nexus repository checkout.

Do not paste credentials into shell history. Prefer files under `/run/nexus/` with root-only permissions.

## Static tests

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_codex_app_server_reply_probe.py \
  backend/tests/test_codex_reply_bridge_sidecar.py
```

## Start sidecar in local stub mode

Use this only outside production:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk

mkdir -p /run/nexus
umask 077
printf '%s' 'replace-with-local-random-token' > /run/nexus/codex_reply_bridge_shared_token

export PYTHONPATH=backend
export APP_ENV=development
export CODEX_REPLY_BRIDGE_MODE=stub
export CODEX_REPLY_BRIDGE_REQUIRE_AUTH=true
export CODEX_REPLY_BRIDGE_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_shared_token
export CODEX_REPLY_BRIDGE_HOST=127.0.0.1
export CODEX_REPLY_BRIDGE_PORT=18793

python3 tools/codex-reply-bridge/sidecar.py
```

In another shell:

```bash
export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'

bash scripts/probe_codex_app_server_reply.sh --strict
cat artifacts/codex_reply_probe/final_verdict.txt
cat artifacts/codex_reply_probe/report.md
```

Expected result in stub mode: `PASS`.

## Start sidecar in upstream mode

Use this only after a private upstream adapter exists:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk

export PYTHONPATH=backend
export APP_ENV=development
export CODEX_REPLY_BRIDGE_MODE=upstream
export CODEX_REPLY_BRIDGE_REQUIRE_AUTH=true
export CODEX_REPLY_BRIDGE_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_shared_token
export CODEX_REPLY_BRIDGE_UPSTREAM_URL='http://127.0.0.1:18794/reply'
export CODEX_REPLY_BRIDGE_UPSTREAM_TOKEN_FILE='/run/nexus/codex_reply_bridge_upstream_token'
export CODEX_REPLY_BRIDGE_UPSTREAM_TIMEOUT_MS='15000'
export CODEX_REPLY_BRIDGE_HOST=127.0.0.1
export CODEX_REPLY_BRIDGE_PORT=18793

python3 tools/codex-reply-bridge/sidecar.py
```

The sidecar will reject upstream responses unless they satisfy the Fast Lane strict JSON contract.

## Probe-only environment variables

```bash
export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'
```

The probe also accepts `CODEX_APP_SERVER_BRIDGE_URL`, `CODEX_APP_SERVER_TOKEN_FILE`, and `CODEX_APP_SERVER_TIMEOUT_MS` aliases.

## Probe artifacts

```text
artifacts/codex_reply_probe/report.md
artifacts/codex_reply_probe/raw_sanitized.json
artifacts/codex_reply_probe/final_verdict.txt
```

You can override the output directory:

```bash
CODEX_REPLY_PROBE_ARTIFACT_DIR=/tmp/codex_reply_probe \
  bash scripts/probe_codex_app_server_reply.sh --strict
```

## Expected verdicts

- `PASS`: bridge returned a response that passed Nexus strict JSON parsing, secret leak check, and internal-term check.
- `CONFIG_MISSING`: no bridge URL configured.
- `CONFIG_REJECTED`: bridge URL shape failed safety validation.
- `FAIL`: endpoint was reachable but transport, HTTP status, parsing, or safety checks failed.

## Sidecar readiness states

- `/healthz` returns process liveness.
- `/readyz` returns unavailable when mode is disabled, auth is missing, production stub is forbidden, or upstream URL is missing.
- `/auth/status` reports whether request auth material is present without echoing secrets.

## Safety validation

The probe and sidecar enforce these rules:

- HTTP probe URLs are allowed only for loopback hosts such as `127.0.0.1` and `localhost`.
- Remote probe URLs must use HTTPS.
- URL userinfo is rejected.
- The response must pass `parse_openclaw_fast_reply`.
- Tool/function-call shaped payloads are rejected by the existing parser.
- Customer-visible internal terms are rejected by the existing parser.
- Sidecar `/reply` returns only the six strict reply fields on success.
- Artifact output is sanitized before writing.

## Release gate before backend provider integration

Do not implement or enable the backend `codex_app_server` provider until:

1. sidecar static tests pass;
2. stub mode probe returns `PASS`;
3. upstream mode probe returns `PASS` against a private adapter;
4. sanitized artifacts show no secret exposure.

## Rollback

Stop the sidecar process. Production provider settings remain unchanged.
