# WebChat Live Voice Edge Credential Rotation

## Authority and safety

This runbook prepares and verifies a credential rotation. It does not authorize a production change.

A production rotation requires explicit authorization for:

- the exact controlled image digest;
- the exact server and secret path;
- the maintenance window;
- the old credential revocation;
- the rollback action.

Never place a credential, token hash, private endpoint inventory, customer identifier or raw Provider payload in GitHub, logs, screenshots or evidence artifacts.

## Canonical secret boundary

The controlled topology mounts the live-voice credential only into `app-controlled`:

```text
LIVE_VOICE_TOKEN_HOST_PATH -> /run/nexus/live_voice_token:ro
```

The AI, outbound, background, handoff and migration services must not receive this credential.

The credential file must:

- be owned by the deployment operator or secret manager;
- be readable only by the intended controlled service path;
- contain one token value and a trailing newline only;
- never be baked into the image or Compose environment;
- be rotated independently from the AI Runtime token and application signing secret.

## Preconditions

1. Freeze the exact source SHA, Git tree, image digest and migration head.
2. Keep these controls fail-closed:
   - `WEBCHAT_VOICE_ENABLED=false`;
   - `PROVIDER_RUNTIME_ENABLED=false` unless separately authorized;
   - `ENABLE_OUTBOUND_DISPATCH=false`;
   - `OPERATIONS_DISPATCH_MODE=disabled`.
3. Confirm the current controlled candidate passes repository, PostgreSQL, migration, supply-chain, storage and business-readiness gates.
4. Confirm a rollback credential remains valid for the authorized rollback window.
5. Confirm the upstream supports overlapping credentials or a bounded atomic cutover. If not, document the expected interruption and obtain explicit approval.
6. Record only bounded evidence identifiers: candidate SHA, image digest, rotation request ID, start/end timestamps, HTTP status class and fixed reason codes.

## Prepare the new credential

1. Generate the new credential using the upstream administrative authority outside the repository.
2. Write it to a new server-side file with a temporary name in the configured runtime-secret directory.
3. Set restrictive ownership and permissions before the file is populated.
4. Validate the file without printing its contents:

```bash
python - <<'PY'
from pathlib import Path
import os

path = Path(os.environ['NEW_LIVE_VOICE_TOKEN_FILE'])
stat = path.stat()
value = path.read_text(encoding='utf-8').strip()
assert path.is_file()
assert 32 <= len(value) <= 4096
assert '\n' not in value and '\r' not in value
assert stat.st_mode & 0o077 == 0
print({'credential_file': 'valid', 'bytes': stat.st_size})
PY
```

Do not print the token, its prefix/suffix or a reusable hash.

## Controlled verification before cutover

Use a non-customer, non-production-write verification method approved by the upstream. Verification must prove only:

- authentication accepted or rejected;
- expected upstream identity/environment;
- bounded network reachability;
- no customer session, recording, TTS, ticket, queue, outbound or Provider authority was created.

If the upstream has no read-only credential check, stop. Do not use a real customer call as a credential probe.

## Cutover

After explicit authorization:

1. Stop new live-voice admission while existing sessions drain.
2. Atomically replace the configured host file or secret-manager version pointer.
3. Restart only the service that consumes the credential. Do not restart unrelated Workers.
4. Verify:
   - controlled service health;
   - release identity unchanged;
   - migration identity unchanged;
   - credential authentication success through the approved bounded check;
   - Provider, outbound and Operations controls remain in their authorized states.
5. Keep the old credential valid only for the approved rollback window.

## Failure and rollback

Rollback immediately when any of the following occurs:

- authentication fails;
- candidate identity changes unexpectedly;
- the service cannot become ready;
- an unauthorized service can read the credential;
- customer-visible or external side effects occur during verification;
- evidence cannot be bounded and redacted.

Rollback steps:

1. Stop new live-voice admission.
2. Atomically restore the previous secret version/file.
3. Restart only the consuming service.
4. Re-run the bounded health/authentication check.
5. Record a fixed failure code and timestamps; do not record the token or Provider payload.

## Revocation and closure

Revoke the old credential only after:

- the authorized observation window completes;
- controlled health and business-readiness remain stable;
- rollback is no longer required;
- the owner explicitly authorizes revocation.

Final evidence must state separately:

- repository preparation complete;
- new credential verified;
- controlled cutover executed;
- old credential revoked;
- production live voice authorized or still disabled.

None of these states implies another.
