# WebChat Live Edge Credential Rotation Runbook

## Control status

- Work Item: `#686`
- Default posture: **BLOCKED / UNVERIFIED / NO_GO**
- This document is a non-executing control plan. It is not production authorization.
- Execution requires both an **explicit production access approval** and a **controlled Credential Rotation Window** that identify the approver, operator, scope, start/end time, rollback owner, and incident escalation path.
- Without both approvals, an operator **must not read, validate, issue, install, revoke, reload, restart, or probe** any production credential, host, secret manager, upstream authority, or live traffic path.

The repository cannot prove whether the historical credential is still installed, active, superseded, or revoked. Until authorized production evidence is recorded, the only valid status is `UNVERIFIED`.

## Outcome and safety boundary

The authorized procedure must establish all of the following in one controlled evidence chain:

1. identify the active server-side credential reference without reading or copying its value;
2. issue and install a replacement through the approved secret boundary;
3. prove the replacement works before predecessor revocation;
4. revoke the predecessor and prove predecessor authentication is rejected;
5. prove `/webchat/live/health` and `/webchat/live/ws` work on the exact deployed release, including an **HTTP 101** WebSocket upgrade;
6. prove browser: zero secret, Git: zero secret, artifact: zero secret, and log: zero secret;
7. retain a complete custody, owner, timestamp, rollback, and incident record without publishing secret material.

This procedure must never enable unrelated Provider traffic, customer outbound, production data mutation, model replacement, or release promotion.

## Repository-authoritative inventory

The following statements describe repository contracts only; they are not evidence of the current production host:

| Boundary | Repository contract |
|---|---|
| Browser route | The browser uses same-origin `/webchat/live/ws`; it does not receive the private upstream URL or credential. |
| Health route | The edge forwards `/webchat/live/health` to the Nexus application, which performs the authenticated upstream request server-side. |
| WebSocket route | The edge forwards `/webchat/live/ws` to the Nexus application, which performs the authenticated upstream WebSocket connection server-side. |
| Secret setting | `LIVE_VOICE_UPSTREAM_TOKEN_FILE` is the preferred production input. |
| Example projection | `/run/nexus/ai_runtime_token` is the repository example for a server-only mounted secret file. |
| Fail-closed behavior | A configured but unreadable secret file raises a runtime configuration error; missing/disabled live voice closes the public path instead of exposing a fallback credential. |
| Logging | The current live-voice boundary records event names and exception types, not Authorization values or payloads. |

The authorized operator must reconcile these contracts against the active deployment before any rotation.

## Mandatory authorization packet

Record the following before the window opens. Missing fields block execution.

| Field | Required record |
|---|---|
| Change / Approval ID | External approval or change ticket identifier only; do not paste approval-system secrets. |
| Production Access Approver | Named accountable approver. |
| Rotation Window | UTC start and end timestamps. |
| Execution Owner | Named operator performing the bounded actions. |
| Custody Owner | Named owner responsible for credential issuance, storage, revocation, and audit custody. |
| Rollback Owner | Named operator authorized to invoke rollback. |
| Incident Owner | Named escalation owner and contact channel reference. |
| Allowed resources | Exact host/service aliases, secret-manager object reference, and upstream credential authority reference. |
| Allowed actions | Read-only inventory, replacement issue, secret projection update, controlled reload/restart, health/upgrade probes, predecessor revocation, invalidation proof, rollback. |
| Explicit exclusions | Customer data, production database writes, unrelated Providers, outbound dispatch, raw logs, secret export, browser-side credential use. |

## Evidence record schema

All evidence must be bounded, redacted, and attributable. Use UTC for every timestamp.

| Evidence field | Required content |
|---|---|
| Work Item | `#686` |
| Change / Approval ID | Approval reference. |
| Execution Owner | Operator identity. |
| Custody Owner | Credential custodian identity. |
| UTC Timestamp | Per-step start and finish. |
| release Git SHA | Exact deployed source revision. |
| image tag or digest | Immutable deployed application identity. |
| configuration fingerprint | Hash of an already-sanitized, secret-free configuration projection; never hash or publish the secret itself. |
| process or container identity | Bounded service/container name and immutable image/process identity. |
| secret reference identifier | Secret-manager object/version reference or mounted-file logical reference only. |
| predecessor reference | Non-sensitive credential object/version identifier only. |
| replacement reference | Non-sensitive credential object/version identifier only. |
| health result | HTTP status, duration, UTC timestamp, release identity, and redacted result category. |
| WebSocket result | Upgrade status, UTC timestamp, release identity, and redacted result category. |
| revocation result | Upstream status category and timestamp; no value or response payload. |
| invalidation result | Rejected-auth category/status and timestamp; no value or response payload. |
| rollback result | Trigger, action, status, owner, start/end timestamps. |
| evidence hashes | Hashes of sanitized evidence files only. |

Never record a secret value, derivative, digest, prefix, suffix, or length. Do not record raw request/response bodies, full environment output, rendered secret-bearing configuration, Authorization values, cookies, tokenized URLs, WebSocket query strings, or browser storage dumps.

## Phase 0 — pre-window preparation

1. Confirm #686 is still open and Blocked, and confirm no newer Work Item or incident authority supersedes this Runbook.
2. Re-read current `main`, current Alembic head, deployment authority #591, exact deployed release identity, active PR/review/check state, and current incident posture.
3. Confirm the authorized write/resource scope has not expanded.
4. Prepare a restricted evidence directory with owner-only permissions and a sanitizer that emits only the schema above.
5. Capture the known-good service definition and non-secret configuration reference for rollback. Do not copy the rendered secret-bearing configuration.
6. Confirm the replacement can be issued and the predecessor can be revoked by the Custody Owner during the same window.
7. Define stop conditions: missing identity, unexpected topology, unreadable secret reference, secret exposure, failed health/upgrade, ambiguous credential state, customer impact, or inability to roll back.

Any stop condition ends the procedure and triggers incident escalation; it does not authorize improvisation.

## Phase 1 — read-only active inventory

The authorized operator must inspect metadata only. Do not display file contents or environment values.

Record:

- release Git SHA;
- image tag or digest;
- configuration fingerprint generated from a sanitized, secret-free projection;
- process or container identity and service owner;
- active edge/application config file identity and owner/mode/mtime;
- the environment variable *name* `LIVE_VOICE_UPSTREAM_TOKEN_FILE` and its configured logical path, not its value;
- secret reference identifier, owner, access policy, current version/state, and last-rotation timestamp if exposed by the secret manager as metadata;
- predecessor credential object/version identifier and upstream status metadata, without authentication material;
- the exact public hostname under test in the restricted change record, not in GitHub.

Prohibited inventory techniques include `cat`, `printenv`, shell tracing, process command-line dumps containing values, copying `/proc/*/environ`, browser devtools exports, raw container inspection output, or uploading rendered config/log files.

## Phase 2 — issue and stage the replacement

1. The Custody Owner creates a new credential through the approved upstream authority.
2. Apply least privilege and the shortest operationally safe validity period supported by the authority.
3. Store the replacement directly in the approved secret manager or restricted server-side projection. No clipboard, chat, Issue, PR, terminal history, CI variable, or repository file may be an intermediate store.
4. Project the replacement to the server-only path referenced by `LIVE_VOICE_UPSTREAM_TOKEN_FILE`. The repository example is `/run/nexus/ai_runtime_token`; the active approved secret reference remains authoritative.
5. Enforce owner-only read permissions and the application service identity as the only runtime reader.
6. Record only the replacement secret reference identifier, secret version metadata, owner/mode metadata, and UTC Timestamp.
7. Validate that the public browser configuration, static assets, HTML, JavaScript, source maps, runtime-config response, and network requests still expose no upstream credential or private upstream destination.

## Phase 3 — controlled activation

1. Validate the service configuration without printing expanded values.
2. Perform only the approved bounded reload or restart for the exact application/edge service.
3. Record the service identity, image digest, restart/reload action, start/end UTC Timestamp, and exit/result category.
4. Confirm the service loaded the expected secret *reference/version metadata* without logging its value.
5. Stop and roll back before revocation if startup, health, WebSocket upgrade, or zero-secret checks fail.

## Phase 4 — replacement validation before revocation

The ordering invariant is: **replacement credential works before predecessor revocation**.

### Health probe

Run from the approved external probe location against the same-origin public route. Do not add an Authorization header and do not include an upstream URL.

```bash
curl --silent --show-error --max-time 10 \
  --output /dev/null \
  --write-out 'status=%{http_code} duration=%{time_total}\n' \
  'https://<approved-public-host>/webchat/live/health'
```

Record only the HTTP status, duration, UTC Timestamp, release identity, and bounded redacted result. Do not retain the response body.

### WebSocket upgrade probe

Generate the WebSocket handshake key locally for the probe. The key is protocol nonce material, not the upstream credential.

```bash
WS_KEY="$(openssl rand -base64 16)"
set +e
WS_RESULT="$(
  curl --http1.1 --silent --show-error --max-time 10 \
    --output /dev/null \
    --write-out 'status=%{http_code}' \
    --header 'Connection: Upgrade' \
    --header 'Upgrade: websocket' \
    --header 'Sec-WebSocket-Version: 13' \
    --header "Sec-WebSocket-Key: ${WS_KEY}" \
    'https://<approved-public-host>/webchat/live/ws'
)"
WS_EXIT=$?
set -e
printf 'result=%s curl_exit=%s\n' "$WS_RESULT" "$WS_EXIT"
test "$WS_RESULT" = 'status=101'
```

Acceptance requires HTTP 101 on the exact deployed release. The probe output is restricted to the HTTP status code and curl exit code. A timeout after a confirmed `status=101` can reflect an intentionally open upgraded connection; any result without `status=101` fails. Raw headers and response bodies must never be emitted, retained, hashed, or attached.

### Browser zero-secret proof

Using a fresh unauthenticated browser profile:

1. fetch the public page, `voice-entry.js`, `widget.js`, runtime-config response, loaded JavaScript chunks, and source maps if publicly served;
2. confirm all live-voice traffic remains same-origin until the Nexus server connects upstream;
3. confirm no request contains upstream Authorization values, credential query parameters, secret references, private upstream hostnames, or secret-manager paths;
4. confirm local/session storage, IndexedDB, cookies, HTML, JavaScript, console, and network response bodies contain no credential material;
5. save only a bounded pass/fail manifest with asset URL path, status, content hash, scanner version, finding count, and UTC Timestamp.

The manifest must state: browser: zero secret, Git: zero secret, artifact: zero secret, and log: zero secret.

### Repository, artifact, and log proof

- Scan the exact release tree and Git diff for known secret patterns and the predecessor/replacement identifiers where safe to do so.
- Scan generated browser assets and sanitized evidence artifacts.
- Query only the bounded time window for approved event names and secret-pattern findings; do not export raw production logs.
- Record scanner version, scope, finding count, exclusions, and UTC Timestamp.
- Any credible finding is a stop condition and incident escalation trigger.

## Phase 5 — revoke and prove predecessor invalidation

Proceed only after Phase 4 passes.

1. The Custody Owner revokes or disables the predecessor in the upstream credential authority.
2. Record the predecessor reference identifier, revocation status category, owner, and UTC Timestamp. Do not record the credential.
3. Prove predecessor authentication is rejected through an upstream-authority status check or an isolated server-side negative probe approved in the change ticket.
4. Do not place either credential in a command line, URL, environment dump, test fixture, log, shell history, Issue, PR, or artifact.
5. The negative probe must consume the predecessor from its restricted secret reference and emit only a bounded redacted result: rejected/accepted/error, approved status category, UTC Timestamp, and probe identity.
6. Acceptance requires a deterministic rejected-auth result. Timeout, network failure, unknown status, or missing metadata is not invalidation proof.
7. Repeat the replacement health and WebSocket probes after predecessor revocation to confirm the active path still works.

If the predecessor is accepted after revocation, stop, disable the live path, preserve sanitized evidence, and invoke incident escalation.

## Rollback

### Rollback before predecessor revocation

1. Stop the activation attempt.
2. Restore the known-good non-secret service definition and prior approved secret *reference* without copying values.
3. Perform the approved bounded reload/restart.
4. Re-run health and HTTP 101 probes.
5. Record rollback owner, trigger, exact release/config identity, timestamps, and result.
6. Keep the replacement disabled or revoke it through the Custody Owner after service recovery.

### Rollback after predecessor revocation

The revoked predecessor must never be re-enabled or restored.

1. If the replacement remains valid, restore only the previous known-good application/config version while retaining the replacement secret reference.
2. If the replacement is invalid or its state is uncertain, set `WEBCHAT_VOICE_ENABLED=false`, remove the live upstream URLs and token-file reference from the active approved configuration, and reload/restart into a fail-closed state.
3. Confirm `/webchat/live/health` is disabled/unavailable by policy and `/webchat/live/ws` is rejected rather than proxied unauthenticated.
4. The Custody Owner issues a second replacement under a new approval before any re-enable attempt.
5. Record customer-impact status, incident escalation, recovery owner, and all UTC timestamps.

Rollback success does not complete #686 unless the replacement is active, the predecessor is revoked and rejected, and all zero-secret, health, WebSocket, custody, and evidence gates pass.

## Completion decision

Mark the production acceptance boxes only when evidence from one authorized window proves:

- current production state was inventoried on an exact release;
- the active credential resides in the approved server-side secret boundary;
- the replacement works before and after predecessor revocation;
- predecessor authentication is rejected;
- same-origin health passes and WebSocket returns HTTP 101;
- browser, Git, artifact, and log scans report zero secret exposure;
- Custody Owner, Execution Owner, UTC Timestamp, Change / Approval ID, rollback, and incident records are complete.

Otherwise leave #686 **Blocked / UNVERIFIED / NO_GO**. A repository Runbook, green CI, review approval, or Draft/Ready PR is not evidence that a real credential was rotated.
