# Codex App-Server Reply Probe Runbook

## Objective

Validate whether a private Codex reply bridge can produce NexusDesk WebChat Fast Lane strict JSON without changing production traffic.

This is a probe-only runbook. It does not enable `codex_app_server` as a production provider.

## Required operator inputs

You need one of the following on the server or local dev host that will run the private bridge:

- a working Codex app-server bridge endpoint, exposed only on loopback or a private Docker network;
- a bridge bearer token file if the bridge requires local authentication;
- the Nexus repository checkout for running the probe.

Do not paste credentials into the shell history. Prefer token files under `/run/nexus/` with root-only permissions.

## Environment variables

```bash
export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'
```

The probe also accepts `CODEX_APP_SERVER_BRIDGE_URL`, `CODEX_APP_SERVER_TOKEN_FILE`, and `CODEX_APP_SERVER_TIMEOUT_MS` aliases.

## Run

From the repository root:

```bash
bash scripts/probe_codex_app_server_reply.sh --strict
```

Artifacts are written to:

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
- `CONFIG_MISSING`: no bridge URL configured. Safe for CI and local development.
- `CONFIG_REJECTED`: bridge URL shape failed safety validation.
- `FAIL`: endpoint was reachable but transport, HTTP status, parsing, or safety checks failed.

## Safety validation

The probe enforces these safety rules:

- HTTP is allowed only for loopback hosts such as `127.0.0.1` and `localhost`.
- Remote bridge URLs must use HTTPS.
- URL userinfo is rejected.
- The response must pass `parse_openclaw_fast_reply`.
- Tool/function-call shaped payloads are rejected by the existing parser.
- Customer-visible internal terms are rejected by the existing parser.
- Artifact output is sanitized before writing.

## Minimal payload

The default request is intentionally low-risk:

```json
{
  "request_id": "codex-reply-probe-local",
  "tenant_key": "default",
  "channel_key": "website",
  "session_id": "codex-reply-probe-session",
  "body": "Hello, I want to check my parcel status.",
  "recent_context": [],
  "tracking_fact_summary": null,
  "tracking_fact_evidence_present": false,
  "strict_schema": "speedaf_webchat_fast_reply_v1"
}
```

Use a custom payload only for controlled tests:

```bash
bash scripts/probe_codex_app_server_reply.sh --payload-file /path/to/payload.json --strict
```

## Local static test

```bash
PYTHONPATH=backend pytest -q backend/tests/test_codex_app_server_reply_probe.py
```

## Release gate before any provider integration

Do not implement or enable the backend `codex_app_server` provider until the probe has produced at least one `PASS` result against a private bridge and the sanitized artifacts show no secret exposure.

## Rollback

No runtime rollback is required for this probe-only phase. Delete the branch or stop running the script. Production provider settings remain unchanged.
